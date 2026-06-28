import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from loro.config import SharedMemoryConfig
from loro.memory.base import SharedMemoryDraft

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class IcebergStatement:
    sql: str
    params: dict[str, Any]


class IcebergSharedMemoryStore:
    """Iceberg shared memory backend.

    This adapter renders SQL compatible with Spark/Trino-style Iceberg engines.
    Live writes remain a future integration point because enterprises usually
    route Iceberg access through Polaris-managed engines or governed catalogs.
    """

    def __init__(self, config: SharedMemoryConfig) -> None:
        self.config = config
        self._validate_identifier(config.iceberg_namespace, "iceberg_namespace")
        self._validate_identifier(config.iceberg_table, "iceberg_table")

    def _validate_identifier(self, value: str, name: str) -> None:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(f"{name} must be a simple Iceberg identifier.")

    @property
    def memory_table(self) -> str:
        return f"{self.config.iceberg_namespace}.{self.config.iceberg_table}"

    @property
    def events_table(self) -> str:
        return f"{self.config.iceberg_namespace}.memory_events"

    def render_schema(self) -> str:
        return f"""
CREATE TABLE IF NOT EXISTS {self.memory_table} (
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

CREATE TABLE IF NOT EXISTS {self.events_table} (
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

    def render_insert(self, draft: SharedMemoryDraft) -> IcebergStatement:
        memory_id = str(uuid4())
        event_id = str(uuid4())
        sql = f"""
INSERT INTO {self.memory_table} (
  memory_id,
  tenant_id,
  scope_type,
  scope_key,
  memory_type,
  content,
  summary,
  tags,
  classification,
  source,
  created_by,
  created_at,
  updated_at,
  status,
  confidence,
  review,
  embedding_ref,
  supersedes,
  expires_at
)
VALUES (
  :memory_id,
  :tenant_id,
  :scope_type,
  :scope_key,
  :memory_type,
  :content,
  :summary,
  ARRAY(),
  :classification,
  :source,
  :created_by,
  :created_at,
  NULL,
  'active',
  NULL,
  NULL,
  NULL,
  ARRAY(),
  NULL
);

INSERT INTO {self.events_table} (
  event_id,
  memory_id,
  tenant_id,
  event_type,
  actor,
  event_at,
  payload
)
VALUES (
  :event_id,
  :memory_id,
  :tenant_id,
  'memory.created',
  :created_by,
  :created_at,
  :event_payload
);
""".strip()
        return IcebergStatement(
            sql=sql,
            params={
                "memory_id": memory_id,
                "event_id": event_id,
                "tenant_id": draft.tenant_id,
                "scope_type": draft.scope_type,
                "scope_key": draft.scope_key,
                "memory_type": draft.memory_type,
                "content": draft.content,
                "summary": draft.summary,
                "classification": draft.classification,
                "source": json.dumps({"source": "loro.shared_memory_draft"}),
                "created_by": draft.created_by,
                "created_at": draft.created_at,
                "event_payload": json.dumps({"draft_id": draft.draft_id}),
            },
        )

    def render_search(
        self,
        *,
        tenant_id: str,
        query: str,
        limit: int = 20,
    ) -> IcebergStatement:
        sql = f"""
SELECT
  memory_id,
  tenant_id,
  scope_type,
  scope_key,
  memory_type,
  content,
  summary,
  classification,
  created_by,
  created_at,
  status
FROM {self.memory_table}
WHERE tenant_id = :tenant_id
  AND status = 'active'
  AND (LOWER(content) LIKE LOWER(:query) OR LOWER(summary) LIKE LOWER(:query))
ORDER BY created_at DESC
LIMIT :limit;
""".strip()
        return IcebergStatement(
            sql=sql,
            params={"tenant_id": tenant_id, "query": f"%{query}%", "limit": limit},
        )
