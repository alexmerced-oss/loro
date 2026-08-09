from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loro.config import LocalMemoryConfig, SafetyConfig
from loro.data_protection import DataProtectionEngine
from loro.memory.base import MemoryRecord


class LocalMemoryStore:
    def __init__(self, root: Path, safety_config: SafetyConfig | None = None) -> None:
        self.root = root.expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "memories.jsonl"
        self.protection = DataProtectionEngine(safety_config) if safety_config else None

    @classmethod
    def from_config(
        cls, config: LocalMemoryConfig, safety_config: SafetyConfig | None = None
    ) -> LocalMemoryStore:
        return cls(Path(config.path), safety_config)

    def remember(
        self, content: str, scope: str = "local", *, allow_sensitive: bool = False
    ) -> MemoryRecord:
        if self.protection is not None:
            self.protection.enforce(content, "memory_local", allow_sensitive=allow_sensitive)
        record = MemoryRecord(content=content, scope=scope)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    {
                        "memory_id": record.memory_id,
                        "content": record.content,
                        "scope": record.scope,
                        "created_at": record.created_at.isoformat(),
                    }
                )
                + "\n"
            )
        return record

    def list(self) -> list[MemoryRecord]:
        if not self.path.exists():
            return []
        records: list[MemoryRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            records.append(
                MemoryRecord(
                    memory_id=data["memory_id"],
                    content=data["content"],
                    scope=data.get("scope", "local"),
                    created_at=datetime.fromisoformat(data["created_at"]),
                )
            )
        return records

    def search(self, query: str) -> list[MemoryRecord]:
        normalized = query.casefold()
        return [record for record in self.list() if normalized in record.content.casefold()]
