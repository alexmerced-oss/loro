from typing import Literal

from loro.config import SharedMemoryConfig
from loro.memory.iceberg import IcebergSharedMemoryStore
from loro.memory.migrations import render_postgres_memory_schema

SharedBackend = Literal["postgres", "iceberg"]


POSTGRES_SHARED_MEMORY_SCHEMA = render_postgres_memory_schema("public")


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
  expires_at TIMESTAMP,
  legal_hold BOOLEAN,
  deleted_at TIMESTAMP
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
