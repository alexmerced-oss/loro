from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4


@dataclass(frozen=True)
class MemoryRecord:
    content: str
    scope: str = "local"
    memory_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SharedMemoryDraft:
    content: str
    summary: str
    tenant_id: str = "default"
    scope_type: str = "org"
    scope_key: str = "default"
    memory_type: str = "fact"
    classification: str = "public-internal"
    created_by: str = "unknown"
    status: str = "draft"
    draft_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MemoryStore(Protocol):
    def remember(self, content: str, scope: str = "local") -> MemoryRecord: ...

    def list(self) -> list[MemoryRecord]: ...
