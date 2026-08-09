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

    def __init__(
        self, config: SharedMemoryConfig, *, authorized_tenant_id: str | None = None
    ) -> None:
        self.config = config
        self.authorized_tenant_id = authorized_tenant_id
        if not IDENTIFIER_PATTERN.fullmatch(config.postgres_schema):
            raise ValueError("postgres_schema must be a simple PostgreSQL identifier.")

    def _table(self, name: str) -> str:
        return f"{self.config.postgres_schema}.{name}"

    def render_schema(self) -> str:
        schema = self.config.postgres_schema
        schema_sql = POSTGRES_SHARED_MEMORY_SCHEMA
        if schema != "public":
            schema_sql = (
                schema_sql.replace(
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
        prefix = f"CREATE SCHEMA IF NOT EXISTS {schema};\n\n" if schema != "public" else ""
        if self.config.tenant_isolation == "identity":
            schema_sql += "\n\n" + self._tenant_policy_sql()
        return prefix + schema_sql

    def _tenant_policy_sql(self) -> str:
        memory_table = self._table("shared_memories")
        events_table = self._table("memory_events")
        return f"""
ALTER TABLE {memory_table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {memory_table} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS loro_tenant_isolation ON {memory_table};
CREATE POLICY loro_tenant_isolation ON {memory_table}
  USING (tenant_id = current_setting('loro.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('loro.tenant_id', true));

ALTER TABLE {events_table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {events_table} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS loro_tenant_isolation ON {events_table};
CREATE POLICY loro_tenant_isolation ON {events_table}
  USING (tenant_id = current_setting('loro.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('loro.tenant_id', true));
""".strip()

    def check(self) -> SharedMemoryBackendCheck:
        messages = [f"Tenant isolation: {self.config.tenant_isolation}"]
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
        self._authorize_tenant(draft.tenant_id)
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
        self._authorize_tenant(tenant_id)
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
                self._set_tenant_context(cursor)
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
                self._set_tenant_context(cursor)
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

    def _authorize_tenant(self, tenant_id: str) -> None:
        if self.config.tenant_isolation != "identity":
            return
        if not self.authorized_tenant_id:
            raise PermissionError("Identity tenant is required for shared-memory access.")
        if tenant_id != self.authorized_tenant_id:
            raise PermissionError(f"Cross-tenant shared-memory access denied: {tenant_id}")

    def _set_tenant_context(self, cursor: object) -> None:
        if self.config.tenant_isolation != "identity":
            return
        if not self.authorized_tenant_id:
            raise PermissionError("Identity tenant is required for shared-memory access.")
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT set_config('loro.tenant_id', %s, true)",
            (self.authorized_tenant_id,),
        )
