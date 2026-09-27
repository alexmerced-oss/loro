"""Gateways can require an OIDC bearer token from the bridge in addition to the signature."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Event

from loro.config import GatewayEndpointConfig, GatewayIdentityConfig, LoroConfig
from loro.gateway.service import GatewayDispatcher

BODY = json.dumps(
    {
        "update_id": 7,
        "message": {"text": "status", "from": {"id": "user-1"}, "chat": {"id": "channel-1"}},
    }
).encode()
SECRET_HEADER = {"X-Telegram-Bot-Api-Secret-Token": "webhook-value"}


class Vault:
    def get(self, ref: str) -> str:
        return {"vault://gateway/tg/webhook": "webhook-value", "vault://gateway/tg/bot": "b"}[ref]


def _dispatcher(tmp_path: Path, idp, runs: list[tuple[str, bool]]):
    endpoint = GatewayEndpointConfig(
        platform="telegram",
        route="/telegram",
        credentials={
            "webhook-secret": "vault://gateway/tg/webhook",  # pragma: allowlist secret
            "bot-token": "vault://gateway/tg/bot",
        },
        identities={"user-1": GatewayIdentityConfig(subject="alex", tenant="acme")},
        oidc_audience="loro-gateway",
    )
    config = LoroConfig.model_validate(
        {
            "identity": {"oidc": {"enabled": True, "issuer": idp.issuer, "client_id": "c"}},
            "gateway": {
                "enabled": True,
                "max_workers": 1,
                "state_path": str(tmp_path / "gateway-state.json"),
                "endpoints": {"telegram": endpoint.model_dump()},
            },
            "audit": {"path": str(tmp_path / "audit.jsonl")},
            "memory": {"local": {"enabled": False}},
        }
    )
    done = Event()

    def runner(resolved: LoroConfig, _prompt: str) -> str:
        runs.append((resolved.identity.auth_method or "", True))
        return "ok"

    return GatewayDispatcher(
        config,
        vault=Vault(),  # type: ignore[arg-type]
        runner=runner,
        deliverer=lambda *_args: done.set(),
    ), done


def test_missing_or_invalid_bridge_token_is_rejected(tmp_path: Path, idp) -> None:
    runs: list[tuple[str, bool]] = []
    dispatcher, _done = _dispatcher(tmp_path, idp, runs)
    assert dispatcher.handle("/telegram", SECRET_HEADER, BODY).status == 401
    wrong = idp.token({"sub": "bridge", "aud": "someone-else"})
    headers = {**SECRET_HEADER, "Authorization": f"Bearer {wrong}"}
    assert dispatcher.handle("/telegram", headers, BODY).status == 401
    dispatcher.close()
    assert runs == []
    reasons = [
        json.loads(line)["details"].get("reason", "")
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    assert any(reason.startswith("oidc:") for reason in reasons)


def test_valid_bridge_token_is_accepted_and_labelled(tmp_path: Path, idp) -> None:
    runs: list[tuple[str, bool]] = []
    dispatcher, done = _dispatcher(tmp_path, idp, runs)
    token = idp.token({"sub": "chat-bridge", "aud": "loro-gateway"})
    headers = {**SECRET_HEADER, "Authorization": f"Bearer {token}"}
    assert dispatcher.handle("/telegram", headers, BODY).status == 200
    assert done.wait(10)
    dispatcher.close()
    assert runs == [("telegram-signed-webhook+oidc-bridge", True)]
    accepted = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
        if json.loads(line)["event_type"] == "gateway.accepted"
    ]
    assert accepted[0]["details"]["bridge_subject"] == "chat-bridge"


def test_user_delegated_token_makes_the_run_verified(tmp_path: Path, idp) -> None:
    runs: list[tuple[str, bool]] = []
    dispatcher, done = _dispatcher(tmp_path, idp, runs)
    dispatcher.runner = dispatcher._run_agent  # the real runtime path
    dispatcher.config.sessions.path = str(tmp_path / "sessions")
    token = idp.token({"sub": "alex", "aud": "loro-gateway"})
    headers = {**SECRET_HEADER, "Authorization": f"Bearer {token}"}
    assert dispatcher.handle("/telegram", headers, BODY).status == 200
    assert done.wait(30)
    dispatcher.close()
    [record] = [json.loads(path.read_text()) for path in (tmp_path / "sessions").glob("*.json")]
    assert record["identity"]["subject"] == "alex"
    assert record["identity"]["verified"] is True
    assert record["identity"]["auth_method"] == "oidc"
