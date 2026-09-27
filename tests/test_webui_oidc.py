"""Web UI OIDC sign-in (authorization code + PKCE) and bearer-token API access."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from loro.config import OIDCConfig
from loro.webui.server import create_app


def _project(tmp_path: Path) -> Path:
    config = tmp_path / ".loro" / "config.local.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        'schema_version = "1.0"\n[model]\nprovider = "mock"\nmodel = "mock-agent"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n'
        f'buffer_path = "{tmp_path / "buffer.jsonl"}"\n'
        f'[sessions]\npath = "{tmp_path / "sessions"}"\n'
        f'message_path = "{tmp_path / "messages"}"\n'
        "[memory.local]\nenabled = false\n",
        encoding="utf-8",
    )
    return tmp_path


def _app(tmp_path: Path, idp):
    oidc = OIDCConfig(enabled=True, issuer=idp.issuer, client_id=idp.client_id, audience="loro-api")
    return create_app(
        project_root=_project(tmp_path),
        database_path=tmp_path / "web.sqlite3",
        database_synchronous="OFF",
        auth_mode="oidc",
        oidc_config=oidc,
    )


async def _sign_in(client: httpx.AsyncClient, next_path: str = "/") -> httpx.Response:
    start = await client.get("/auth/login", params={"next": next_path})
    assert start.status_code == 303
    authorize = start.headers["location"]
    query = parse_qs(urlsplit(authorize).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["response_type"] == ["code"]
    assert "nonce" in query and "state" in query
    with httpx.Client(follow_redirects=False) as browser:
        idp_response = browser.get(authorize)
    assert idp_response.status_code == 302
    callback = urlsplit(idp_response.headers["location"])
    return await client.get(f"{callback.path}?{callback.query}")


@pytest.mark.asyncio
async def test_browser_sign_in_creates_a_verified_session(tmp_path: Path, idp) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(tmp_path, idp)), base_url="http://test"
    ) as client:
        blocked = await client.get("/api/status")
        assert blocked.status_code == 401
        assert blocked.json()["login_url"] == "/auth/login"
        me = (await client.get("/auth/me")).json()
        assert me["mode"] == "oidc" and me["authenticated"] is False

        finished = await _sign_in(client, "/?view=chat")
        assert finished.status_code == 303
        assert finished.headers["location"] == "/?view=chat"
        assert "samesite=lax" in finished.headers["set-cookie"].lower()
        assert "httponly" in finished.headers["set-cookie"].lower()

        session = (await client.get("/api/session")).json()
        assert session["identity"]["subject"] == "alex@example.com"
        assert session["identity"]["verified"] is True
        assert session["identity"]["auth_method"] == "oidc"
        me = (await client.get("/auth/me")).json()
        assert me["authenticated"] is True
        assert me["identity"]["display_name"] == "Alex Example"

        headers = {"X-Loro-CSRF": session["csrf_token"]}
        assert (await client.post("/api/conversations", json={})).status_code == 403
        created = await client.post("/api/conversations", json={}, headers=headers)
        assert created.status_code == 201

        started = await client.post(
            f"/api/conversations/{created.json()['id']}/messages",
            json={"content": "Hello."},
            headers=headers,
        )
        events = await client.get(f"/api/runs/{started.json()['run_id']}/events")
        assert "event: run.completed" in events.text

        logout = await client.post("/auth/logout", headers=headers)
        assert logout.status_code == 200
        assert (await client.get("/api/status")).status_code == 401

    # The run executed as the signed-in user, not the OS account.
    sessions = list((tmp_path / "sessions").glob("*.json"))
    record = json.loads(sessions[0].read_text())
    assert record["identity"]["subject"] == "alex@example.com"
    assert record["identity"]["verified"] is True
    # PKCE: the token request carried a verifier the IdP checked against the challenge.
    assert idp.token_requests and idp.token_requests[0]["code_verifier"]


@pytest.mark.asyncio
async def test_bearer_tokens_authenticate_api_clients(tmp_path: Path, idp) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(tmp_path, idp)), base_url="http://test"
    ) as client:
        good = idp.token({"sub": "ci-bot", "aud": "loro-api", "roles": ["operator"]})
        ok = await client.get("/api/status", headers={"Authorization": f"Bearer {good}"})
        assert ok.status_code == 200
        # Bearer clients are not cookie-authenticated, so no CSRF token is needed.
        created = await client.post(
            "/api/conversations", json={}, headers={"Authorization": f"Bearer {good}"}
        )
        assert created.status_code == 201

        for bad in (
            idp.token({"sub": "x", "aud": "someone-else"}),
            idp.token({"sub": "x", "aud": "loro-api"}, alg="none"),
            "not-a-jwt",
        ):
            rejected = await client.get("/api/status", headers={"Authorization": f"Bearer {bad}"})
            assert rejected.status_code == 401
            assert rejected.json()["error"] == "invalid_token"
            assert "invalid_token" in rejected.headers["www-authenticate"]


@pytest.mark.asyncio
async def test_sign_in_failures_return_to_the_app_with_a_reason(tmp_path: Path, idp) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(tmp_path, idp)), base_url="http://test"
    ) as client:
        refused = await client.get(
            "/auth/callback", params={"error": "access_denied", "error_description": "No access"}
        )
        assert refused.status_code == 303
        assert refused.headers["location"].startswith("/?auth_error=")
        assert "No%20access" in refused.headers["location"]

        replay = await client.get("/auth/callback", params={"state": "unknown", "code": "x"})
        assert "expired" in replay.headers["location"]

        start = await client.get("/auth/login", params={"next": "//evil.example/steal"})
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        with httpx.Client(follow_redirects=False) as browser:
            back = urlsplit(browser.get(start.headers["location"]).headers["location"])
        done = await client.get(f"{back.path}?{back.query}")
        assert done.headers["location"] == "/"  # no open redirect
        again = await client.get("/auth/callback", params={"state": state, "code": "reused"})
        assert "auth_error" in again.headers["location"]


@pytest.mark.asyncio
async def test_token_mode_is_unchanged(tmp_path: Path) -> None:
    app = create_app(
        project_root=_project(tmp_path),
        database_path=tmp_path / "web.sqlite3",
        database_synchronous="OFF",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/auth/me")).json() == {"mode": "token"}
        assert (await client.get("/auth/login")).status_code == 404
        assert (await client.get("/api/session")).status_code == 200


def test_oidc_mode_requires_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="identity.oidc"):
        create_app(project_root=_project(tmp_path), auth_mode="oidc", oidc_config=OIDCConfig())
