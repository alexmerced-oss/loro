from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from loro.config import SafetyConfig, SessionConfig
from loro.data_protection import DataProtectionEngine


@dataclass(frozen=True)
class SessionRecord:
    prompt: str
    mode: str
    summary: str
    recalled_memories: list[str] = field(default_factory=list)
    recalled_shared_memories: list[dict[str, str]] = field(default_factory=list)
    tool_executions: list[dict[str, Any]] = field(default_factory=list)
    activated_skills: list[dict[str, Any]] = field(default_factory=list)
    inbound_message_ids: list[str] = field(default_factory=list)
    identity: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = "completed"
    session_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "mode": self.mode,
            "prompt": self.prompt,
            "summary": self.summary,
            "recalled_memories": self.recalled_memories,
            "recalled_shared_memories": self.recalled_shared_memories,
            "tool_executions": self.tool_executions,
            "activated_skills": self.activated_skills,
            "inbound_message_ids": self.inbound_message_ids,
            "identity": self.identity,
            "stop_reason": self.stop_reason,
        }


class SessionStore:
    def __init__(self, config: SessionConfig, safety_config: SafetyConfig | None = None) -> None:
        self.root = Path(config.path).expanduser()
        self.max_record_bytes = config.max_record_bytes
        self.protection = DataProtectionEngine(safety_config) if safety_config else None

    def save(self, record: SessionRecord) -> SessionRecord:
        payload = json.dumps(record.to_payload(), indent=2) + "\n"
        _require_bounded(payload, self.max_record_bytes)
        if self.protection is not None:
            self.protection.enforce(payload, "session")
        session_id = safe_session_id(record.session_id)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{session_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
        return record

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        records = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            records.append(self._read(path))
        return records

    def get(self, session_id: str) -> dict[str, Any]:
        path = self.root / f"{safe_session_id(session_id)}.json"
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return self._read(path)

    def _read(self, path: Path) -> dict[str, Any]:
        _require_bounded_file(path, self.max_record_bytes)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Session record must be a JSON object: {path}")
        stored_id = payload.get("session_id")
        if stored_id != path.stem or safe_session_id(str(stored_id)) != stored_id:
            raise ValueError(f"Session record identity does not match its path: {path}")
        return payload


SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def safe_session_id(value: str) -> str:
    if SESSION_ID.fullmatch(value) is None:
        raise ValueError("Session ids must be 1-128 letters, numbers, hyphens, or underscores.")
    return value


def _require_bounded(value: str, maximum: int) -> None:
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"Session record exceeds the managed limit of {maximum} bytes.")


def _require_bounded_file(path: Path, maximum: int) -> None:
    if path.stat().st_size > maximum:
        raise ValueError(f"Session record exceeds the managed limit of {maximum} bytes: {path}")
