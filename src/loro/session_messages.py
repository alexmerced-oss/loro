from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from loro.config import SafetyConfig, SessionConfig
from loro.data_protection import DataProtectionEngine
from loro.sessions import SessionStore, safe_session_id

MessageStatus = Literal["queued", "delivered", "acknowledged"]


@dataclass(frozen=True)
class SessionMessage:
    sender_session_id: str
    recipient_session_id: str
    content: str
    message_id: str = ""
    status: MessageStatus = "queued"
    created_at: str = ""
    delivered_at: str | None = None
    acknowledged_at: str | None = None
    carries_user_authority: bool = False
    trust: str = "untrusted-cross-session"

    def to_payload(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender_session_id": self.sender_session_id,
            "recipient_session_id": self.recipient_session_id,
            "content": self.content,
            "content_digest": message_digest(self.content),
            "status": self.status,
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
            "acknowledged_at": self.acknowledged_at,
            "carries_user_authority": False,
            "trust": self.trust,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SessionMessage:
        return cls(
            message_id=str(payload["message_id"]),
            sender_session_id=str(payload["sender_session_id"]),
            recipient_session_id=str(payload["recipient_session_id"]),
            content=str(payload["content"]),
            status=str(payload["status"]),  # type: ignore[arg-type]
            created_at=str(payload["created_at"]),
            delivered_at=payload.get("delivered_at"),
            acknowledged_at=payload.get("acknowledged_at"),
            carries_user_authority=False,
            trust="untrusted-cross-session",
        )


class SessionMailbox:
    def __init__(self, config: SessionConfig, safety_config: SafetyConfig | None = None) -> None:
        self.config = config
        self.root = Path(config.message_path).expanduser()
        self.sessions = SessionStore(config, safety_config)
        self.protection = DataProtectionEngine(safety_config) if safety_config else None

    def send(
        self,
        *,
        sender_session_id: str,
        recipient_session_id: str,
        content: str,
        validate_sender: bool = True,
        allow_sensitive: bool = False,
    ) -> SessionMessage:
        if not content.strip():
            raise ValueError("Session message cannot be empty.")
        if len(content.encode("utf-8")) > self.config.max_message_bytes:
            raise ValueError("Session message exceeds the managed size limit.")
        if self.protection is not None:
            self.protection.enforce(content, "session_message", allow_sensitive=allow_sensitive)
        if validate_sender:
            self.sessions.get(sender_session_id)
        self.sessions.get(recipient_session_id)
        now = datetime.now(UTC).isoformat()
        message = SessionMessage(
            message_id=str(uuid4()),
            sender_session_id=sender_session_id,
            recipient_session_id=recipient_session_id,
            content=content,
            created_at=now,
        )
        self._save(message)
        return message

    def list(
        self,
        recipient_session_id: str,
        *,
        include_acknowledged: bool = False,
    ) -> list[SessionMessage]:
        directory = self.root / _safe_id(recipient_session_id)
        if not directory.exists():
            return []
        messages = [
            self._read(path, recipient_session_id) for path in sorted(directory.glob("*.json"))
        ]
        if not include_acknowledged:
            messages = [message for message in messages if message.status != "acknowledged"]
        return messages

    def deliver(self, recipient_session_id: str) -> list[SessionMessage]:
        self.sessions.get(recipient_session_id)
        delivered: list[SessionMessage] = []
        now = datetime.now(UTC).isoformat()
        for message in self.list(recipient_session_id):
            if message.status == "queued":
                message = replace(message, status="delivered", delivered_at=now)
                self._save(message)
            delivered.append(message)
        return delivered

    def acknowledge(self, recipient_session_id: str, message_id: str) -> SessionMessage:
        message = self.get(recipient_session_id, message_id)
        if message.status == "queued":
            raise ValueError("Session message must be delivered before acknowledgement.")
        if message.status == "acknowledged":
            return message
        updated = replace(
            message,
            status="acknowledged",
            acknowledged_at=datetime.now(UTC).isoformat(),
        )
        self._save(updated)
        return updated

    def get(self, recipient_session_id: str, message_id: str) -> SessionMessage:
        path = self.root / _safe_id(recipient_session_id) / f"{_safe_id(message_id)}.json"
        if not path.exists():
            raise FileNotFoundError(f"Session message not found: {message_id}")
        return self._read(path, recipient_session_id)

    def _save(self, message: SessionMessage) -> None:
        directory = self.root / _safe_id(message.recipient_session_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{_safe_id(message.message_id)}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(message.to_payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _read(self, path: Path, recipient_session_id: str) -> SessionMessage:
        if path.stat().st_size > self.config.max_message_bytes + 8192:
            raise ValueError(f"Session message record exceeds the managed size limit: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Session message record must be a JSON object: {path}")
        required = {
            "message_id",
            "sender_session_id",
            "recipient_session_id",
            "content",
            "status",
            "created_at",
        }
        if not required.issubset(payload):
            raise ValueError(f"Session message record is incomplete: {path}")
        message = SessionMessage.from_payload(payload)
        # The digest was written on save but never checked, so anyone with write access to
        # the mailbox could rewrite `content` — which reaches AgentRuntime as cross-session
        # context — and leave the stale digest in place.
        stored_digest = payload.get("content_digest")
        if not isinstance(stored_digest, str) or not stored_digest:
            raise ValueError(f"Session message record has no content digest: {path}")
        if not hmac.compare_digest(stored_digest, message_digest(message.content)):
            raise ValueError(f"Session message content does not match its digest: {path}")
        if message.status not in {"queued", "delivered", "acknowledged"}:
            raise ValueError(f"Session message has invalid status: {message.status}")
        if message.recipient_session_id != recipient_session_id or path.stem != message.message_id:
            raise ValueError(f"Session message identity does not match its path: {path}")
        safe_session_id(message.sender_session_id)
        safe_session_id(message.recipient_session_id)
        _safe_id(message.message_id)
        if len(message.content.encode("utf-8")) > self.config.max_message_bytes:
            raise ValueError("Session message exceeds the managed size limit.")
        return message


def message_digest(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _safe_id(value: str) -> str:
    if not value or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in value
    ):
        raise ValueError(
            "Session and message ids may contain only letters, numbers, hyphens, and underscores."
        )
    return value
