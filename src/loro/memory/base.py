from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
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


@dataclass(frozen=True)
class SharedMemoryStatement:
    sql: str
    params: dict[str, Any]


@dataclass(frozen=True)
class SharedMemorySearchRecord:
    memory_id: str
    tenant_id: str
    scope_type: str
    scope_key: str
    memory_type: str
    content: str
    summary: str
    classification: str
    created_by: str
    created_at: str
    status: str
    backend: str

    @property
    def citation(self) -> str:
        return (
            f"{self.backend}:{self.tenant_id}/{self.scope_type}/{self.scope_key}/{self.memory_id}"
        )


@dataclass(frozen=True)
class SharedMemorySearchResult:
    backend: str
    query: str
    tenant_id: str
    executed: bool
    records: list[SharedMemorySearchRecord] = field(default_factory=list)
    statement: SharedMemoryStatement | None = None
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SharedMemoryBackendCheck:
    backend: str
    ok: bool
    messages: list[str]


class MemoryStore(Protocol):
    def remember(self, content: str, scope: str = "local") -> MemoryRecord: ...

    def list(self) -> list[MemoryRecord]: ...
