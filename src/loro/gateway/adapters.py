from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from loro.config import GatewayEndpointConfig


class GatewayAdapterError(ValueError):
    """Raised when a gateway request is unauthenticated or malformed."""


@dataclass(frozen=True)
class ChannelMessage:
    platform: str
    endpoint_id: str
    message_id: str
    user_id: str
    channel_id: str
    text: str
    workspace_id: str = ""
    response: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InboundResult:
    status: int
    response: dict[str, Any]
    message: ChannelMessage | None = None


SecretResolver = Callable[[str], str]


def parse_inbound(
    endpoint_id: str,
    config: GatewayEndpointConfig,
    headers: Mapping[str, str],
    body: bytes,
    secret: SecretResolver,
    *,
    now: int | None = None,
    max_age_seconds: int = 300,
) -> InboundResult:
    normalized = {key.casefold(): value for key, value in headers.items()}
    payload = _payload(body)
    if config.platform == "slack":
        _verify_slack(normalized, body, secret("signing-secret"), now, max_age_seconds)
        if payload.get("type") == "url_verification":
            return InboundResult(200, {"challenge": payload.get("challenge", "")})
        event = payload.get("event") or {}
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return InboundResult(200, {})
        return InboundResult(
            200,
            {},
            ChannelMessage(
                "slack",
                endpoint_id,
                str(event.get("client_msg_id") or event.get("ts") or payload.get("event_id", "")),
                str(event.get("user", "")),
                str(event.get("channel", "")),
                str(event.get("text", "")),
                str(payload.get("team_id", "")),
                {"channel": str(event.get("channel", ""))},
            ),
        )
    if config.platform == "discord":
        _verify_discord(normalized, body, secret("public-key"))
        if payload.get("type") == 1:
            return InboundResult(200, {"type": 1})
        user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
        data = payload.get("data") or {}
        text = _discord_text(data)
        flags = 64 if config.ephemeral_responses else 0
        return InboundResult(
            200,
            {"type": 5, "data": {"flags": flags}},
            ChannelMessage(
                "discord",
                endpoint_id,
                str(payload.get("id", "")),
                str(user.get("id", "")),
                str(payload.get("channel_id") or payload.get("guild_id") or "dm"),
                text,
                str(payload.get("guild_id") or "dm"),
                {
                    "application_id": str(payload.get("application_id", "")),
                    "token": str(payload.get("token", "")),
                },
            ),
        )
    if config.platform == "telegram":
        expected = secret("webhook-secret")
        supplied = normalized.get("x-telegram-bot-api-secret-token", "")
        if not supplied or not hmac.compare_digest(expected, supplied):
            raise GatewayAdapterError("invalid Telegram webhook secret")
        message = payload.get("message") or payload.get("edited_message") or {}
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        return InboundResult(
            200,
            {},
            ChannelMessage(
                "telegram",
                endpoint_id,
                str(payload.get("update_id", "")),
                str(sender.get("id", "")),
                str(chat.get("id", "")),
                str(message.get("text") or message.get("caption") or ""),
                str(chat.get("id", "")),
                {"chat_id": str(chat.get("id", ""))},
            ),
        )
    if config.platform in {"teams", "signal", "generic"}:
        _verify_bridge(config.platform, normalized, body, secret("signing-secret"))
        sender = payload.get("from") or {}
        conversation = payload.get("conversation") or {}
        response = (
            {"type": "message", "text": "Loro accepted this task for processing."}
            if config.platform == "teams"
            else {"accepted": True}
        )
        return InboundResult(
            200,
            response,
            ChannelMessage(
                config.platform,
                endpoint_id,
                str(payload.get("id") or payload.get("message_id") or ""),
                str(sender.get("id") or payload.get("user_id") or ""),
                str(conversation.get("id") or payload.get("channel_id") or ""),
                str(payload.get("text") or ""),
                str(payload.get("tenant_id") or payload.get("workspace_id") or ""),
                {},
            ),
        )
    raise GatewayAdapterError(f"unsupported gateway platform: {config.platform}")


def _payload(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GatewayAdapterError("gateway body must be valid JSON") from error
    if not isinstance(payload, dict):
        raise GatewayAdapterError("gateway body must be a JSON object")
    return payload


def _verify_slack(
    headers: Mapping[str, str],
    body: bytes,
    signing_secret: str,
    now: int | None,
    max_age_seconds: int,
) -> None:
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    try:
        timestamp_value = int(timestamp)
    except ValueError as error:
        raise GatewayAdapterError("invalid Slack request timestamp") from error
    if abs((now or int(time.time())) - timestamp_value) > max_age_seconds:
        raise GatewayAdapterError("stale Slack request")
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise GatewayAdapterError("invalid Slack request signature")


def _verify_discord(headers: Mapping[str, str], body: bytes, public_key: str) -> None:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key))
        key.verify(
            bytes.fromhex(headers.get("x-signature-ed25519", "")),
            headers.get("x-signature-timestamp", "").encode() + body,
        )
    except ImportError as error:
        raise GatewayAdapterError("Discord gateway requires the gateway package extra") from error
    except (ValueError, InvalidSignature) as error:
        raise GatewayAdapterError("invalid Discord request signature") from error


def _verify_bridge(
    platform: str, headers: Mapping[str, str], body: bytes, signing_secret: str
) -> None:
    supplied = headers.get("authorization", "").removeprefix("HMAC ")
    if platform != "teams":
        supplied = headers.get("x-loro-signature", supplied).removeprefix("sha256=")
        expected = hmac.new(signing_secret.encode(), body, hashlib.sha256).hexdigest()
    else:
        try:
            expected = base64.b64encode(
                hmac.new(base64.b64decode(signing_secret), body, hashlib.sha256).digest()
            ).decode()
        except ValueError as error:
            raise GatewayAdapterError("invalid Teams signing secret encoding") from error
    if not supplied or not hmac.compare_digest(expected, supplied):
        raise GatewayAdapterError(f"invalid {platform} request signature")


def _discord_text(data: Mapping[str, Any]) -> str:
    name = str(data.get("name") or "loro")
    values: list[str] = []

    def collect(options: Any) -> None:
        for option in options if isinstance(options, list) else []:
            if not isinstance(option, dict):
                continue
            if "value" in option:
                values.append(str(option["value"]))
            collect(option.get("options"))

    collect(data.get("options"))
    return " ".join(values) or name
