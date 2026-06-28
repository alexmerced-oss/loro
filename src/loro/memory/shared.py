import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from loro.config import SharedMemoryConfig
from loro.memory.base import SharedMemoryDraft
from loro.memory.iceberg import IcebergSharedMemoryStore

SharedBackend = Literal["postgres", "iceberg"]


POSTGRES_SHARED_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_memories (
  memory_id UUID PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  memory_type TEXT NOT NULL,
  content TEXT NOT NULL,
  summary TEXT NOT NULL,
  tags TEXT[] NOT NULL DEFAULT '{}',
  classification TEXT NOT NULL,
  source JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'active',
  confidence DOUBLE PRECISION,
  review JSONB,
  embedding_ref TEXT,
  supersedes UUID[] NOT NULL DEFAULT '{}',
  expires_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS memory_events (
  event_id UUID PRIMARY KEY,
  memory_id UUID,
  tenant_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  event_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_shared_memories_scope
  ON shared_memories (tenant_id, scope_type, scope_key, status);

CREATE INDEX IF NOT EXISTS idx_shared_memories_type
  ON shared_memories (tenant_id, memory_type, status);
""".strip()


ICEBERG_SHARED_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_memory.shared_memories (
  memory_id STRING,
  tenant_id STRING,
  scope_type STRING,
  scope_key STRING,
  memory_type STRING,
  content STRING,
  summary STRING,
  tags ARRAY<STRING>,
  classification STRING,
  source STRING,
  created_by STRING,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  status STRING,
  confidence DOUBLE,
  review STRING,
  embedding_ref STRING,
  supersedes ARRAY<STRING>,
  expires_at TIMESTAMP
)
USING iceberg
PARTITIONED BY (tenant_id, scope_type);

CREATE TABLE IF NOT EXISTS agent_memory.memory_events (
  event_id STRING,
  memory_id STRING,
  tenant_id STRING,
  event_type STRING,
  actor STRING,
  event_at TIMESTAMP,
  payload STRING
)
USING iceberg
PARTITIONED BY (tenant_id, event_type);
""".strip()


def shared_memory_schema(
    backend: SharedBackend,
    config: SharedMemoryConfig | None = None,
) -> str:
    if backend == "postgres":
        return POSTGRES_SHARED_MEMORY_SCHEMA
    if backend == "iceberg":
        if config is not None:
            return IcebergSharedMemoryStore(config).render_schema()
        return ICEBERG_SHARED_MEMORY_SCHEMA
    raise ValueError(f"Unsupported shared memory backend: {backend}")


class SharedMemoryDraftStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "shared-memory-drafts.jsonl"

    def stage(self, draft: SharedMemoryDraft) -> SharedMemoryDraft:
        payload = {
            "draft_id": draft.draft_id,
            "tenant_id": draft.tenant_id,
            "scope_type": draft.scope_type,
            "scope_key": draft.scope_key,
            "memory_type": draft.memory_type,
            "content": draft.content,
            "summary": draft.summary,
            "classification": draft.classification,
            "created_by": draft.created_by,
            "status": draft.status,
            "created_at": draft.created_at.isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload) + "\n")
        return draft

    def list(self) -> list[SharedMemoryDraft]:
        if not self.path.exists():
            return []
        drafts: list[SharedMemoryDraft] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            drafts.append(
                SharedMemoryDraft(
                    draft_id=data["draft_id"],
                    tenant_id=data["tenant_id"],
                    scope_type=data["scope_type"],
                    scope_key=data["scope_key"],
                    memory_type=data["memory_type"],
                    content=data["content"],
                    summary=data["summary"],
                    classification=data["classification"],
                    created_by=data["created_by"],
                    status=data["status"],
                    created_at=datetime.fromisoformat(data["created_at"]),
                )
            )
        return drafts
