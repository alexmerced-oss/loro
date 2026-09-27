"""Web UI role-based access control in OIDC mode, driven by verified token claims."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from loro.config import OIDCConfig
from loro.webui.server import create_app


def _project(tmp_path: Path, extra: str = "") -> Path:
    config = tmp_path / ".loro" / "config.local.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        'schema_version = "1.0"\n[model]\nprovider = "mock"\nmodel = "mock-agent"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n'
        f'buffer_path = "{tmp_path / "buffer.jsonl"}"\n'
        "[memory.local]\nenabled = false\n" + extra,
        encoding="utf-8",
    )
    return tmp_path


def _client(tmp_path: Path, idp, extra: str = "") -> httpx.AsyncClient:
    app = create_app(
        project_root=_project(tmp_path, extra),
        database_path=tmp_path / "web.sqlite3",
        database_synchronous="OFF",
        auth_mode="oidc",
        oidc_config=OIDCConfig(
            enabled=True, issuer=idp.issuer, client_id="loro-web", audience="loro-api"
        ),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _as(idp, sub: str, roles=(), groups=()) -> dict[str, str]:
    token = idp.token({"sub": sub, "aud": "loro-api", "roles": list(roles), "groups": list(groups)})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_role_matrix_is_enforced(tmp_path: Path, idp) -> None:
    async with _client(tmp_path, idp) as client:
        viewer, operator = _as(idp, "vic", ["viewer"]), _as(idp, "olga", ["operator"])
        approver, admin = _as(idp, "abe", ["approver"]), _as(idp, "ada", ["admin"])

        assert (await client.get("/api/status", headers=viewer)).status_code == 200
        blocked = await client.post("/api/conversations", json={}, headers=viewer)
        assert blocked.status_code == 403
        assert blocked.json()["required"] == "operate"
        assert "viewer" in blocked.json()["detail"]

        assert (
            await client.post("/api/conversations", json={}, headers=operator)
        ).status_code == 201
        # Separation of duties: operators cannot decide approvals.
        decision = {"request_id": "r-1", "decision": "approve", "scope": "once"}
        denied = await client.post("/api/approvals/decisions", json=decision, headers=operator)
        assert denied.status_code == 403 and denied.json()["required"] == "approve"
        assert (await client.patch("/api/settings", json={}, headers=operator)).status_code == 403

        allowed = await client.post("/api/approvals/decisions", json=decision, headers=approver)
        assert allowed.status_code != 403  # unknown request: a 409 from the store, not RBAC
        assert (
            await client.post("/api/conversations", json={}, headers=approver)
        ).status_code == 403

        settings = await client.patch("/api/settings", json={}, headers=admin)
        assert settings.status_code != 403

        me = (await client.get("/api/session", headers=operator)).json()
        assert me["roles"] == ["operator"] and me["permissions"] == ["read", "operate"]

    denials = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
        if json.loads(line)["event_type"] == "policy.access_denied"
    ]
    assert {event["details"]["required"] for event in denials} == {"operate", "approve", "admin"}


@pytest.mark.asyncio
async def test_users_without_a_role_are_refused_with_guidance(tmp_path: Path, idp) -> None:
    async with _client(tmp_path, idp) as client:
        response = await client.get("/api/session", headers=_as(idp, "nobody", groups=["sales"]))
        assert response.status_code == 403
        assert "no Loro role" in response.json()["detail"]


@pytest.mark.asyncio
async def test_group_mappings_admins_and_default_role(tmp_path: Path, idp) -> None:
    extra = (
        "[webui.rbac]\n"
        'admins = ["root@example.com"]\n'
        'default_role = "viewer"\n'
        "[webui.rbac.mappings]\n"
        '"data-platform" = "operator"\n'
    )
    async with _client(tmp_path, idp, extra) as client:
        mapped = await client.get("/api/session", headers=_as(idp, "m", groups=["data-platform"]))
        assert mapped.json()["roles"] == ["operator"]
        boot = await client.get("/api/session", headers=_as(idp, "root@example.com"))
        assert boot.json()["roles"] == ["admin"]
        fallback = await client.get("/api/session", headers=_as(idp, "someone"))
        assert fallback.json()["roles"] == ["viewer"]


@pytest.mark.asyncio
async def test_admins_edit_access_rules_without_locking_themselves_out(tmp_path: Path, idp) -> None:
    async with _client(tmp_path, idp) as client:
        admin = _as(idp, "ada", ["admin"])
        body = {"mappings": {"sre": "approver"}, "admins": [], "default_role": None}
        updated = await client.put("/api/access/admin/rbac", json=body, headers=admin)
        assert updated.status_code == 200, updated.text
        assert updated.json()["mappings"] == {"sre": "approver"}
        sre = await client.get("/api/session", headers=_as(idp, "s", groups=["sre"]))
        assert sre.json()["roles"] == ["approver"]

        lockout = await client.put(
            "/api/access/admin/rbac",
            json={**body, "accept_role_names": False},
            headers=admin,
        )
        assert lockout.status_code == 409
        assert "remove your own admin access" in lockout.json()["detail"]

        viewer = await client.put(
            "/api/access/admin/rbac", json=body, headers=_as(idp, "v", ["viewer"])
        )
        assert viewer.status_code == 403
        listing = (await client.get("/api/access", headers=_as(idp, "v", ["viewer"]))).json()
        assert listing["rbac_enabled"] is True
        assert [role["name"] for role in listing["roles"]] == [
            "viewer",
            "operator",
            "approver",
            "admin",
        ]
    saved = (tmp_path / ".loro" / "config.local.toml").read_text()
    assert "[webui.rbac.mappings]" in saved and 'sre = "approver"' in saved


@pytest.mark.asyncio
async def test_token_mode_is_a_single_admin(tmp_path: Path) -> None:
    app = create_app(
        project_root=_project(tmp_path),
        database_path=tmp_path / "w.sqlite3",
        database_synchronous="OFF",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        session = (await client.get("/api/session")).json()
        assert session["roles"] == ["admin"]
        assert (await client.get("/api/access")).json()["rbac_enabled"] is False
