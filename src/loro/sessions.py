from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from loro.config import SessionConfig


@dataclass(frozen=True)
class SessionRecord:
    prompt: str
    mode: str
    summary: str
    recalled_memories: list[str] = field(default_factory=list)
    recalled_shared_memories: list[dict[str, str]] = field(default_factory=list)
    tool_executions: list[dict[str, Any]] = field(default_factory=list)
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
            "identity": self.identity,
            "stop_reason": self.stop_reason,
        }


class SessionStore:
    def __init__(self, config: SessionConfig) -> None:
        self.root = Path(config.path).expanduser()

    def save(self, record: SessionRecord) -> SessionRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{record.session_id}.json"
        path.write_text(json.dumps(record.to_payload(), indent=2) + "\n", encoding="utf-8")
        return record

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        records = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            records.append(json.loads(path.read_text(encoding="utf-8")))
        return records

    def get(self, session_id: str) -> dict[str, Any]:
        path = self.root / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return json.loads(path.read_text(encoding="utf-8"))
