import json
import os
import re
from uuid import uuid4

from loro.config import SharedMemoryConfig
from loro.memory.base import (
    SharedMemoryBackendCheck,
    SharedMemoryDraft,
    SharedMemorySearchRecord,
    SharedMemoryStatement,
)
from loro.memory.schemas import POSTGRES_SHARED_MEMORY_SCHEMA

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PostgresSharedMemoryStore:
    """Postgres shared memory backend.

    This adapter renders SQL in all environments and executes it when psycopg
    plus a DSN are available. The write path accepts only explicit drafts.
    """

    def __init__(self, config: SharedMemoryConfig) -> None:
        self.config = config
        if not IDENTIFIER_PATTERN.fullmatch(config.postgres_schema):
            raise ValueError("postgres_schema must be a simple PostgreSQL identifier.")

    def _table(self, name: str) -> str:
        return f"{self.config.postgres_schema}.{name}"

    def render_schema(self) -> str:
        if self.config.postgres_schema == "public":
            return POSTGRES_SHARED_MEMORY_SCHEMA
        schema = self.config.postgres_schema
        schema_sql = (
            POSTGRES_SHARED_MEMORY_SCHEMA.replace(
                "idx_shared_memories_scope",
                f"idx_{schema}_shared_memories_scope",
            )
            .replace(
                "idx_shared_memories_type",
                f"idx_{schema}_shared_memories_type",
            )
            .replace(
                "CREATE TABLE IF NOT EXISTS shared_memories",
                f"CREATE TABLE IF NOT EXISTS {self._table('shared_memories')}",
            )
            .replace(
                "CREATE TABLE IF NOT EXISTS memory_events",
                f"CREATE TABLE IF NOT EXISTS {self._table('memory_events')}",
            )
            .replace("ON shared_memories", f"ON {self._table('shared_memories')}")
        )
        return (
            f"CREATE SCHEMA IF NOT EXISTS {schema};\n\n"
            + schema_sql
        )

    def check(self) -> SharedMemoryBackendCheck:
        messages: list[str] = []
        dsn = os.environ.get(self.config.postgres_dsn_env)
        if dsn:
            messages.append(f"Found DSN env var: {self.config.postgres_dsn_env}")
        else:
            messages.append(f"Missing DSN env var: {self.config.postgres_dsn_env}")
        try:
            import psycopg  # noqa: F401

            messages.append("psycopg is importable.")
            psycopg_available = True
        except ModuleNotFoundError:
            messages.append("psycopg is not installed. Install the data extra.")
            psycopg_available = False
        return SharedMemoryBackendCheck(
            backend="postgres",
            ok=bool(dsn) and psycopg_available,
            messages=messages,
        )

    def render_insert(self, draft: SharedMemoryDraft) -> SharedMemoryStatement:
        memory_id = str(uuid4())
        event_id = str(uuid4())
        sql = f"""
WITH inserted_memory AS (
  INSERT INTO {self._table("shared_memories")} (
    memory_id,
    tenant_id,
    scope_type,
    scope_key,
    memory_type,
    content,
    summary,
    classification,
    source,
    created_by,
    created_at,
    status
  )
  VALUES (
    %(memory_id)s,
    %(tenant_id)s,
    %(scope_type)s,
    %(scope_key)s,
    %(memory_type)s,
    %(content)s,
    %(summary)s,
    %(classification)s,
    %(source)s::jsonb,
    %(created_by)s,
    %(created_at)s,
    'active'
  )
  RETURNING memory_id
)
INSERT INTO {self._table("memory_events")} (
  event_id,
  memory_id,
  tenant_id,
  event_type,
  actor,
  payload
)
SELECT
  %(event_id)s,
  inserted_memory.memory_id,
  %(tenant_id)s,
  'memory.created',
  %(created_by)s,
  %(event_payload)s::jsonb
FROM inserted_memory;
""".strip()
        return SharedMemoryStatement(
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
    ) -> SharedMemoryStatement:
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
FROM {self._table("shared_memories")}
WHERE tenant_id = %(tenant_id)s
  AND status = 'active'
  AND (content ILIKE %(query)s OR summary ILIKE %(query)s)
ORDER BY created_at DESC
LIMIT %(limit)s;
""".strip()
        return SharedMemoryStatement(
            sql=sql,
            params={"tenant_id": tenant_id, "query": f"%{query}%", "limit": limit},
        )

    def commit_draft(self, draft: SharedMemoryDraft) -> None:
        dsn = os.environ.get(self.config.postgres_dsn_env)
        if not dsn:
            raise RuntimeError(f"Missing DSN env var: {self.config.postgres_dsn_env}")
        try:
            import psycopg
        except ModuleNotFoundError as error:
            raise RuntimeError("psycopg is required for Postgres shared memory writes.") from error
        statement = self.render_insert(draft)
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement.sql, statement.params)
            connection.commit()

    def search(
        self,
        *,
        tenant_id: str,
        query: str,
        limit: int = 20,
    ) -> list[SharedMemorySearchRecord]:
        dsn = os.environ.get(self.config.postgres_dsn_env)
        if not dsn:
            raise RuntimeError(f"Missing DSN env var: {self.config.postgres_dsn_env}")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as error:
            raise RuntimeError("psycopg is required for Postgres shared memory search.") from error
        statement = self.render_search(tenant_id=tenant_id, query=query, limit=limit)
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement.sql, statement.params)
                rows = cursor.fetchall()
        return [
            SharedMemorySearchRecord(
                memory_id=str(row["memory_id"]),
                tenant_id=str(row["tenant_id"]),
                scope_type=str(row["scope_type"]),
                scope_key=str(row["scope_key"]),
                memory_type=str(row["memory_type"]),
                content=str(row["content"]),
                summary=str(row["summary"]),
                classification=str(row["classification"]),
                created_by=str(row["created_by"]),
                created_at=str(row["created_at"]),
                status=str(row["status"]),
                backend="postgres",
            )
            for row in rows
        ]

    def apply_schema(self) -> None:
        dsn = os.environ.get(self.config.postgres_dsn_env)
        if not dsn:
            raise RuntimeError(f"Missing DSN env var: {self.config.postgres_dsn_env}")
        try:
            import psycopg
        except ModuleNotFoundError as error:
            raise RuntimeError("psycopg is required for Postgres schema application.") from error
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(self.render_schema())
            connection.commit()
