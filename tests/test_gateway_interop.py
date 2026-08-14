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
from loro.gateway.adapters import GatewayAdapterError, parse_inbound
from loro.gateway.service import GatewayDispatcher


def _endpoint(platform: str) -> GatewayEndpointConfig:
    return GatewayEndpointConfig(platform=platform, route=f"/{platform}")


@pytest.mark.parametrize("platform", ["teams", "signal", "generic"])
def test_signed_bridge_fixtures_and_credential_rotation(platform: str) -> None:
    body = json.dumps(
        {
            "id": f"{platform}-fixture",
            "type": "signed_bridge_message",
            "timestamp": 1000,
            "text": "fixture",
            "from": {"id": "user"},
            "conversation": {"id": "channel"},
            "tenant_id": "tenant",
        },
        separators=(",", ":"),
    ).encode()
    old = "old-fixture-secret"
    signature = hmac.new(old.encode(), body, hashlib.sha256).digest()
    headers = (
        {"Authorization": "HMAC " + base64.b64encode(signature).decode()}
        if platform == "teams"
        else {"X-Loro-Signature": "sha256=" + signature.hex()}
    )
    secret = base64.b64encode(old.encode()).decode() if platform == "teams" else old
    assert parse_inbound(platform, _endpoint(platform), headers, body, lambda _: secret, now=1000)

    rotated = base64.b64encode(b"rotated").decode() if platform == "teams" else "rotated"
    with pytest.raises(GatewayAdapterError, match="signature"):
        parse_inbound(platform, _endpoint(platform), headers, body, lambda _: rotated, now=1000)


def test_slack_discord_and_telegram_reject_invalid_or_replayed_signatures() -> None:
    slack_body = b'{"event":{"user":"u","channel":"c","text":"fixture","ts":"1"}}'
    with pytest.raises(GatewayAdapterError, match="stale Slack"):
        parse_inbound(
            "slack",
            _endpoint("slack"),
            {"X-Slack-Request-Timestamp": "1", "X-Slack-Signature": "v0=invalid"},
            slack_body,
            lambda _: "fixture",
            now=1000,
        )

    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    discord_body = b'{"id":"1","type":2,"data":{"name":"loro"},"user":{"id":"u"}}'
    with pytest.raises(GatewayAdapterError, match="invalid Discord"):
        parse_inbound(
            "discord",
            _endpoint("discord"),
            {"X-Signature-Timestamp": "1000", "X-Signature-Ed25519": "00" * 64},
            discord_body,
            lambda _: public.hex(),
            now=1000,
        )

    with pytest.raises(GatewayAdapterError, match="Telegram"):
        parse_inbound(
            "telegram",
            _endpoint("telegram"),
            {"X-Telegram-Bot-Api-Secret-Token": "captured-old-token"},
            b"{}",
            lambda _: "rotated-token",
            now=1000,
        )


def test_gateway_overload_and_tenant_channel_mismatch_fail_closed(tmp_path: Path) -> None:
    endpoint = GatewayEndpointConfig(
        platform="telegram",
        route="/telegram",
        credentials={
            "webhook-secret": "vault://gateway/tg/webhook"  # pragma: allowlist secret
        },
        identities={"user": GatewayIdentityConfig(subject="alex", tenant="tenant-a")},
        allowed_channels=["approved-channel"],
        allowed_workspaces=["approved-channel"],
    )
    config = LoroConfig.model_validate(
        {
            "gateway": {
                "enabled": True,
                "max_workers": 1,
                "max_pending_tasks": 1,
                "state_path": str(tmp_path / "seen.json"),
                "endpoints": {"tg": endpoint.model_dump()},
            },
            "audit": {"enabled": False},
            "memory": {"local": {"enabled": False}},
        }
    )

    class Vault:
        def get(self, _ref: str) -> str:
            return "fixture-secret"  # pragma: allowlist secret

    release = Event()
    started = Event()

    def run(_config: LoroConfig, _prompt: str) -> str:
        started.set()
        release.wait(2)
        return "done"

    dispatcher = GatewayDispatcher(config, vault=Vault(), runner=run)  # type: ignore[arg-type]
    headers = {"X-Telegram-Bot-Api-Secret-Token": "fixture-secret"}

    def body(message_id: int, channel: str) -> bytes:
        return json.dumps(
            {
                "update_id": message_id,
                "message": {
                    "text": "fixture",
                    "from": {"id": "user"},
                    "chat": {"id": channel},
                },
            }
        ).encode()

    assert dispatcher.handle("/telegram", headers, body(1, "wrong-channel")).status == 403
    assert dispatcher.handle("/telegram", headers, body(2, "approved-channel")).status == 200
    assert started.wait(1)
    assert dispatcher.handle("/telegram", headers, body(3, "approved-channel")).status == 429
    release.set()
    dispatcher.close()

def test_unsupported_authenticated_gateway_event_never_becomes_a_task() -> None:
    from loro.gateway.adapters import GatewayUnsupportedEventError

    with pytest.raises(GatewayUnsupportedEventError, match="unsupported Telegram"):
        parse_inbound(
            "telegram",
            _endpoint("telegram"),
            {"X-Telegram-Bot-Api-Secret-Token": "fixture-token"},
            b'{"update_id":1,"callback_query":{}}',
            lambda _: "fixture-token",
            now=1000,
        )
