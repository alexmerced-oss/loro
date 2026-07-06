from typing import Literal

from loro.config import SharedMemoryConfig
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
