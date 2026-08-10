import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from loro.config import SharedMemoryConfig
from loro.memory.base import (
    SharedMemoryBackendCheck,
    SharedMemoryDraft,
    SharedMemoryLifecycleRequest,
    SharedMemorySearchRecord,
    SharedMemoryStatement,
    like_term,
)
from loro.memory.schemas import POSTGRES_SHARED_MEMORY_SCHEMA

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@contextmanager
def _driver_errors(operation: str) -> Iterator[None]:
    """Present driver failures as RuntimeError so callers can fall back to rendering SQL.

    Only missing-DSN/missing-psycopg cases used to raise RuntimeError; a bad DSN,
    unreachable host, auth failure or missing table raises psycopg.Error, which escaped
    every caller's fallback as a raw traceback.
    """

    try:
        yield
    except (PermissionError, RuntimeError):
        raise
    except Exception as error:
        raise RuntimeError(f"Postgres {operation} failed: {error}") from error


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
                .replace(
                    "ALTER TABLE shared_memories",
                    f"ALTER TABLE {self._table('shared_memories')}",
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
    status,
    expires_at
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
    'active',
    %(expires_at)s
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
                "expires_at": draft.expires_at,
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
  AND (expires_at IS NULL OR expires_at > now())
  AND (content ILIKE %(query)s ESCAPE '\\' OR summary ILIKE %(query)s ESCAPE '\\')
ORDER BY created_at DESC
LIMIT %(limit)s;
""".strip()
        return SharedMemoryStatement(
            sql=sql,
            params={"tenant_id": tenant_id, "query": like_term(query), "limit": limit},
        )

    def render_expired(self, *, tenant_id: str, limit: int = 100) -> SharedMemoryStatement:
        self._authorize_tenant(tenant_id)
        sql = f"""
SELECT memory_id, tenant_id, summary, expires_at, legal_hold
FROM {self._table("shared_memories")}
WHERE tenant_id = %(tenant_id)s
  AND status = 'active'
  AND expires_at IS NOT NULL
  AND expires_at <= now()
ORDER BY expires_at ASC
LIMIT %(limit)s;
""".strip()
        return SharedMemoryStatement(
            sql=sql,
            params={"tenant_id": tenant_id, "limit": limit},
        )

    def list_expired(self, *, tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
        dsn = os.environ.get(self.config.postgres_dsn_env)
        if not dsn:
            raise RuntimeError(f"Missing DSN env var: {self.config.postgres_dsn_env}")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as error:
            raise RuntimeError("psycopg is required for the shared memory sweep.") from error
        statement = self.render_expired(tenant_id=tenant_id, limit=limit)
        with _driver_errors("shared memory sweep"):
            with psycopg.connect(dsn, row_factory=dict_row) as connection:
                with connection.cursor() as cursor:
                    self._set_tenant_context(cursor)
                    cursor.execute(statement.sql, statement.params)
                    rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def render_lifecycle(
        self, request: SharedMemoryLifecycleRequest
    ) -> SharedMemoryStatement:
        self._authorize_tenant(request.tenant_id)
        assignments = {
            "correct": (
                "content = %(content)s, summary = %(summary)s, updated_at = %(requested_at)s"
            ),
            "delete": (
                "status = 'deleted', deleted_at = %(requested_at)s, "
                "updated_at = %(requested_at)s"
            ),
            "expire": "expires_at = %(expires_at)s, updated_at = %(requested_at)s",
            "hold": "legal_hold = TRUE, updated_at = %(requested_at)s",
            "release_hold": "legal_hold = FALSE, updated_at = %(requested_at)s",
        }
        assignment = assignments[request.action]
        hold_guard = "AND legal_hold = FALSE" if request.action in {"delete", "expire"} else ""
        sql = f"""
WITH updated_memory AS (
  UPDATE {self._table("shared_memories")}
  SET {assignment}
  WHERE memory_id = %(memory_id)s
    AND tenant_id = %(tenant_id)s
    {hold_guard}
  RETURNING memory_id
)
INSERT INTO {self._table("memory_events")} (
  event_id, memory_id, tenant_id, event_type, actor, event_at, payload
)
SELECT
  %(event_id)s, memory_id, %(tenant_id)s, %(event_type)s,
  %(actor)s, %(requested_at)s, %(event_payload)s::jsonb
FROM updated_memory;
""".strip()
        return SharedMemoryStatement(
            sql=sql,
            params={
                "memory_id": request.memory_id,
                "tenant_id": request.tenant_id,
                "event_id": request.event_id,
                "event_type": f"memory.{request.action}",
                "actor": request.actor,
                "requested_at": request.requested_at,
                "content": request.content,
                "summary": request.summary,
                "expires_at": request.expires_at,
                "event_payload": json.dumps(
                    {
                        "reason": request.reason,
                        "replacement_digest": _content_digest(request.content),
                    }
                ),
            },
        )

    def apply_lifecycle(self, request: SharedMemoryLifecycleRequest) -> None:
        dsn = os.environ.get(self.config.postgres_dsn_env)
        if not dsn:
            raise RuntimeError(f"Missing DSN env var: {self.config.postgres_dsn_env}")
        try:
            import psycopg
        except ModuleNotFoundError as error:
            raise RuntimeError("psycopg is required for Postgres memory lifecycle.") from error
        statement = self.render_lifecycle(request)
        with _driver_errors("memory lifecycle"):
            with psycopg.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    self._set_tenant_context(cursor)
                    cursor.execute(statement.sql, statement.params)
                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            "Memory lifecycle target was not found or is protected by legal hold."
                        )
                connection.commit()

    def commit_draft(self, draft: SharedMemoryDraft) -> None:
        dsn = os.environ.get(self.config.postgres_dsn_env)
        if not dsn:
            raise RuntimeError(f"Missing DSN env var: {self.config.postgres_dsn_env}")
        try:
            import psycopg
        except ModuleNotFoundError as error:
            raise RuntimeError("psycopg is required for Postgres shared memory writes.") from error
        statement = self.render_insert(draft)
        with _driver_errors("shared memory write"):
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
        with _driver_errors("shared memory search"):
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
        with _driver_errors("schema application"):
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


def _content_digest(content: str | None) -> str | None:
    if content is None:
        return None
    import hashlib

    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
