from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from threading import Event

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from loro.config import GatewayEndpointConfig, GatewayIdentityConfig, LoroConfig
from loro.gateway.adapters import parse_inbound
from loro.gateway.service import GatewayDispatcher


def _endpoint(platform: str) -> GatewayEndpointConfig:
    return GatewayEndpointConfig(
        platform=platform,
        route=f"/{platform}",
        credentials={},
        identities={"user-1": GatewayIdentityConfig(subject="alex", tenant="acme")},
    )


def test_slack_signature_challenge_and_message() -> None:
    body = json.dumps({"type": "url_verification", "challenge": "ok"}).encode()
    timestamp = "1000"
    signature = (
        "v0="
        + hmac.new(
            b"signing-value", b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )
    result = parse_inbound(
        "work",
        _endpoint("slack"),
        {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature},
        body,
        lambda _name: "signing-value",
        now=1000,
    )
    assert result.response == {"challenge": "ok"}


def test_discord_signature_and_deferred_response() -> None:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    body = json.dumps(
        {
            "id": "interaction-1",
            "type": 2,
            "application_id": "app-1",
            "token": "response-token",
            "channel_id": "channel-1",
            "member": {"user": {"id": "user-1"}},
            "data": {"name": "loro", "options": [{"name": "prompt", "value": "status"}]},
        }
    ).encode()
    timestamp = "1000"
    signature = private.sign(timestamp.encode() + body).hex()
    result = parse_inbound(
        "discord",
        _endpoint("discord"),
        {"X-Signature-Timestamp": timestamp, "X-Signature-Ed25519": signature},
        body,
        lambda _name: public.hex(),
        now=1000,
    )
    assert result.response["type"] == 5
    assert result.message is not None
    assert result.message.text == "status"


def test_telegram_secret_and_dispatch_identity_boundary(tmp_path: Path) -> None:
    endpoint = _endpoint("telegram").model_copy(
        update={
            "credentials": {
                "webhook-secret": "vault://gateway/tg/webhook",  # pragma: allowlist secret
                "bot-token": "vault://gateway/tg/bot",
            }
        }
    )
    config = LoroConfig.model_validate(
        {
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

    class Vault:
        def get(self, ref: str) -> str:
            return {
                "vault://gateway/tg/webhook": "webhook-value",
                "vault://gateway/tg/bot": "bot-value",
            }[ref]

    delivered: list[str] = []
    complete = Event()

    def deliver(_message, _endpoint, content: str) -> None:
        delivered.append(content)
        complete.set()

    dispatcher = GatewayDispatcher(
        config,
        vault=Vault(),  # type: ignore[arg-type]
        runner=lambda resolved, prompt: f"{resolved.identity.subject}:{prompt[-6:]}",
        deliverer=deliver,
    )
    body = json.dumps(
        {
            "update_id": 42,
            "message": {
                "text": "status",
                "from": {"id": "user-1"},
                "chat": {"id": "channel-1"},
            },
        }
    ).encode()
    response = dispatcher.handle(
        "/telegram", {"X-Telegram-Bot-Api-Secret-Token": "webhook-value"}, body
    )
    assert response.status == 200
    assert complete.wait(2)
    assert delivered == ["alex:status"]
    state = (tmp_path / "gateway-state.json").read_text(encoding="utf-8")
    assert "telegram:42" not in state
    assert (
        dispatcher.handle(
            "/telegram", {"X-Telegram-Bot-Api-Secret-Token": "webhook-value"}, body
        ).status
        == 200
    )
    dispatcher.close()
    event_types = {
        json.loads(line)["event_type"]
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    }
    assert {"gateway.accepted", "gateway.delivered", "gateway.duplicate"} <= event_types


def test_teams_and_generic_bridge_signatures() -> None:
    body = json.dumps(
        {
            "id": "message-1",
            "text": "status",
            "timestamp": 1000,
            "from": {"id": "user-1"},
            "conversation": {"id": "channel-1"},
        }
    ).encode()
    teams_secret = base64.b64encode(b"teams-secret").decode()
    teams_signature = base64.b64encode(
        hmac.new(b"teams-secret", body, hashlib.sha256).digest()
    ).decode()
    teams = parse_inbound(
        "teams",
        _endpoint("teams"),
        {"Authorization": f"HMAC {teams_signature}"},
        body,
        lambda _name: teams_secret,
        now=1000,
    )
    assert teams.message is not None
    assert teams.response == {
        "type": "message",
        "text": "Loro accepted this task for processing.",
    }
    generic_signature = hmac.new(b"bridge-secret", body, hashlib.sha256).hexdigest()
    generic = parse_inbound(
        "signal",
        _endpoint("signal"),
        {"X-Loro-Signature": f"sha256={generic_signature}"},
        body,
        lambda _name: "bridge-secret",
        now=1000,
    )
    assert generic.message is not None
    assert generic.message.platform == "signal"


def test_gateway_replay_state_corruption_fails_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "gateway-state.json"
    state_path.write_text("not-json", encoding="utf-8")
    config = LoroConfig.model_validate(
        {
            "gateway": {"enabled": True, "state_path": str(state_path)},
            "audit": {"enabled": False},
        }
    )

    with pytest.raises(RuntimeError, match="replay state is invalid"):
        GatewayDispatcher(config)


def test_gateway_replay_write_failure_restores_queue_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = _endpoint("telegram").model_copy(
        update={
            "credentials": {
                "webhook-secret": "vault://gateway/tg/webhook"  # pragma: allowlist secret
            }
        }
    )
    config = LoroConfig.model_validate(
        {
            "gateway": {
                "enabled": True,
                "max_workers": 1,
                "max_pending_tasks": 1,
                "state_path": str(tmp_path / "gateway-state.json"),
                "endpoints": {"telegram": endpoint.model_dump()},
            },
            "audit": {"enabled": False},
        }
    )

    class Vault:
        def get(self, _ref: str) -> str:
            return "webhook-value"

    completed = Event()
    dispatcher = GatewayDispatcher(
        config,
        vault=Vault(),  # type: ignore[arg-type]
        runner=lambda _config, _prompt: "done",
        deliverer=lambda *_args: completed.set(),
    )
    original_save = dispatcher._save_seen
    monkeypatch.setattr(
        dispatcher,
        "_save_seen",
        lambda: (_ for _ in ()).throw(OSError("disk full")),
    )
    body = json.dumps(
        {
            "update_id": 7,
            "message": {
                "text": "status",
                "from": {"id": "user-1"},
                "chat": {"id": "channel-1"},
            },
        }
    ).encode()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "webhook-value"}

    assert dispatcher.handle("/telegram", headers, body).status == 500
    monkeypatch.setattr(dispatcher, "_save_seen", original_save)
    assert dispatcher.handle("/telegram", headers, body).status == 200
    assert completed.wait(2)
    dispatcher.close()


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://hooks.example.test/reply", True),
        ("http://localhost:9999/reply", True),
        ("http://hooks.example.test/reply", False),
        ("https://user@hooks.example.test/reply", False),
    ],
)
def test_gateway_outbound_url_policy(url: str, allowed: bool) -> None:
    if allowed:
        GatewayDispatcher._validate_outbound_url(url)
    else:
        with pytest.raises(ValueError):
            GatewayDispatcher._validate_outbound_url(url)
