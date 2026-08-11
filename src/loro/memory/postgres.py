import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
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
from loro.memory.migrations import (
    LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
    migration_table,
    postgres_memory_migrations,
    record_migration_sql,
    render_postgres_memory_schema,
)

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class PostgresMigrationResult:
    previous_version: int
    current_version: int
    applied: tuple[int, ...] = ()
    rolled_back: tuple[int, ...] = ()


@dataclass(frozen=True)
class MemoryReconciliationReport:
    memories: int
    events: int
    orphan_events: int
    memories_without_created_event: int
    tenant_mismatches: int
    lifecycle_state_mismatches: int
    schema_version: int
    issues: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.issues


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
        return render_postgres_memory_schema(
            self.config.postgres_schema,
            tenant_isolation=self.config.tenant_isolation == "identity",
        )

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
        content_digest = _content_digest(draft.content)
        sql = f"""
WITH existing_operation AS (
  SELECT memory_id, event_type, actor, payload
  FROM {self._table("memory_events")}
  WHERE tenant_id = %(tenant_id)s AND operation_id = %(operation_id)s
),
matching_operation AS (
  SELECT memory_id
  FROM existing_operation
  WHERE event_type = 'memory.created'
    AND actor = %(created_by)s
    AND payload->>'content_digest' = %(content_digest)s
),
inserted_memory AS (
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
  SELECT
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
  WHERE NOT EXISTS (SELECT 1 FROM existing_operation)
  RETURNING memory_id
),
inserted_event AS (
INSERT INTO {self._table("memory_events")} (
  event_id,
  memory_id,
  tenant_id,
  operation_id,
  event_type,
  actor,
  payload
)
SELECT
  %(event_id)s,
  inserted_memory.memory_id,
  %(tenant_id)s,
  %(operation_id)s,
  'memory.created',
  %(created_by)s,
  %(event_payload)s::jsonb
FROM inserted_memory
ON CONFLICT (tenant_id, operation_id) WHERE operation_id IS NOT NULL DO NOTHING
RETURNING event_id
),
result AS (
  SELECT
    EXISTS (SELECT 1 FROM existing_operation) AS operation_exists,
    EXISTS (SELECT 1 FROM matching_operation) AS operation_matches,
    EXISTS (SELECT 1 FROM inserted_memory) AS memory_inserted,
    EXISTS (SELECT 1 FROM inserted_event) AS event_inserted
)
SELECT operation_exists, operation_matches, memory_inserted, event_inserted FROM result;
""".strip()
        return SharedMemoryStatement(
            sql=sql,
            params={
                "memory_id": memory_id,
                "event_id": event_id,
                "operation_id": draft.draft_id,
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
                "content_digest": content_digest,
                "event_payload": json.dumps(
                    {"draft_id": draft.draft_id, "content_digest": content_digest}
                ),
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

    def render_lifecycle(self, request: SharedMemoryLifecycleRequest) -> SharedMemoryStatement:
        self._authorize_tenant(request.tenant_id)
        assignments = {
            "correct": (
                "content = %(content)s, summary = %(summary)s, updated_at = %(requested_at)s"
            ),
            "delete": (
                "status = 'deleted', deleted_at = %(requested_at)s, updated_at = %(requested_at)s"
            ),
            "expire": "expires_at = %(expires_at)s, updated_at = %(requested_at)s",
            "hold": "legal_hold = TRUE, updated_at = %(requested_at)s",
            "release_hold": "legal_hold = FALSE, updated_at = %(requested_at)s",
        }
        assignment = assignments[request.action]
        hold_guard = "AND legal_hold = FALSE" if request.action in {"delete", "expire"} else ""
        replacement_digest = _content_digest(request.content)
        sql = f"""
WITH existing_operation AS (
  SELECT memory_id, event_type, actor, payload
  FROM {self._table("memory_events")}
  WHERE tenant_id = %(tenant_id)s AND operation_id = %(operation_id)s
),
matching_operation AS (
  SELECT memory_id
  FROM existing_operation
  WHERE memory_id = %(memory_id)s
    AND event_type = %(event_type)s
    AND actor = %(actor)s
    AND payload->>'replacement_digest' IS NOT DISTINCT FROM %(replacement_digest)s
),
updated_memory AS (
  UPDATE {self._table("shared_memories")}
  SET {assignment}
  WHERE memory_id = %(memory_id)s
    AND tenant_id = %(tenant_id)s
    {hold_guard}
    AND NOT EXISTS (SELECT 1 FROM existing_operation)
  RETURNING memory_id
),
inserted_event AS (
INSERT INTO {self._table("memory_events")} (
  event_id, memory_id, tenant_id, operation_id, event_type, actor, event_at, payload
)
SELECT
  %(event_id)s, memory_id, %(tenant_id)s, %(operation_id)s, %(event_type)s,
  %(actor)s, %(requested_at)s, %(event_payload)s::jsonb
FROM updated_memory
ON CONFLICT (tenant_id, operation_id) WHERE operation_id IS NOT NULL DO NOTHING
RETURNING event_id
)
SELECT
  EXISTS (SELECT 1 FROM existing_operation),
  EXISTS (SELECT 1 FROM matching_operation),
  EXISTS (SELECT 1 FROM updated_memory),
  EXISTS (SELECT 1 FROM inserted_event);
""".strip()
        return SharedMemoryStatement(
            sql=sql,
            params={
                "memory_id": request.memory_id,
                "tenant_id": request.tenant_id,
                "event_id": request.event_id,
                "operation_id": request.event_id,
                "event_type": f"memory.{request.action}",
                "actor": request.actor,
                "requested_at": request.requested_at,
                "content": request.content,
                "summary": request.summary,
                "expires_at": request.expires_at,
                "replacement_digest": replacement_digest,
                "event_payload": json.dumps(
                    {"reason": request.reason, "replacement_digest": replacement_digest}
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
                    self._lock_operation(cursor, request.tenant_id, request.event_id)
                    cursor.execute(statement.sql, statement.params)
                    operation_exists, operation_matches, updated, event_inserted = cursor.fetchone()
                    if operation_exists and not operation_matches:
                        raise RuntimeError("Memory lifecycle operation ID is already bound.")
                    if operation_matches:
                        return
                    if not updated or not event_inserted:
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
                    self._lock_operation(cursor, draft.tenant_id, draft.draft_id)
                    cursor.execute(statement.sql, statement.params)
                    (
                        operation_exists,
                        operation_matches,
                        inserted,
                        event_inserted,
                    ) = cursor.fetchone()
                    if operation_exists and not operation_matches:
                        raise RuntimeError("Shared-memory draft ID is already bound.")
                    if not operation_matches and (not inserted or not event_inserted):
                        raise RuntimeError(
                            "Shared-memory draft commit did not complete atomically."
                        )
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
        self.migrate()

    def schema_version(self) -> int:
        dsn = os.environ.get(self.config.postgres_dsn_env)
        if not dsn:
            raise RuntimeError(f"Missing DSN env var: {self.config.postgres_dsn_env}")
        try:
            import psycopg
        except ModuleNotFoundError as error:
            raise RuntimeError("psycopg is required for Postgres schema inspection.") from error
        with _driver_errors("schema inspection"):
            with psycopg.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT to_regclass(%s)",
                        (migration_table(self.config.postgres_schema),),
                    )
                    if cursor.fetchone()[0] is None:
                        return 0
                    cursor.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM "
                        f"{migration_table(self.config.postgres_schema)}"
                    )
                    return int(cursor.fetchone()[0])

    def migrate(
        self,
        *,
        target_version: int = LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
        allow_destructive: bool = False,
    ) -> PostgresMigrationResult:
        if target_version < 0 or target_version > LATEST_POSTGRES_MEMORY_SCHEMA_VERSION:
            raise ValueError(f"Unsupported Postgres memory schema target: {target_version}")
        dsn = os.environ.get(self.config.postgres_dsn_env)
        if not dsn:
            raise RuntimeError(f"Missing DSN env var: {self.config.postgres_dsn_env}")
        try:
            import psycopg
        except ModuleNotFoundError as error:
            raise RuntimeError("psycopg is required for Postgres schema migration.") from error
        migrations = postgres_memory_migrations(
            self.config.postgres_schema,
            tenant_isolation=self.config.tenant_isolation == "identity",
        )
        with _driver_errors("schema migration"):
            with psycopg.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        ("loro.memory.migrations",),
                    )
                    cursor.execute(
                        "SELECT to_regclass(%s)",
                        (migration_table(self.config.postgres_schema),),
                    )
                    current = 0
                    if cursor.fetchone()[0] is not None:
                        cursor.execute(
                            "SELECT version, checksum FROM "
                            f"{migration_table(self.config.postgres_schema)} ORDER BY version"
                        )
                        applied_rows = dict(cursor.fetchall())
                        current = max(applied_rows, default=0)
                        for migration in migrations:
                            if (
                                migration.version in applied_rows
                                and applied_rows[migration.version] != migration.checksum
                            ):
                                raise RuntimeError(
                                    "Postgres memory migration checksum mismatch at "
                                    f"version {migration.version}."
                                )
                    previous = current
                    applied: list[int] = []
                    rolled_back: list[int] = []
                    if target_version > current:
                        for migration in migrations:
                            if current < migration.version <= target_version:
                                cursor.execute(migration.up)
                                cursor.execute(
                                    record_migration_sql(
                                        self.config.postgres_schema,
                                        migration,
                                    )
                                )
                                applied.append(migration.version)
                    elif target_version < current:
                        for migration in reversed(migrations):
                            if target_version < migration.version <= current:
                                if migration.rollback == "destructive" and not allow_destructive:
                                    raise RuntimeError(
                                        "Refusing destructive Postgres memory rollback "
                                        "without explicit authorization."
                                    )
                                cursor.execute(migration.down)
                                cursor.execute(
                                    "DELETE FROM "
                                    f"{migration_table(self.config.postgres_schema)} "
                                    "WHERE version = %s",
                                    (migration.version,),
                                )
                                rolled_back.append(migration.version)
                connection.commit()
        return PostgresMigrationResult(
            previous_version=previous,
            current_version=target_version,
            applied=tuple(applied),
            rolled_back=tuple(rolled_back),
        )

    def reconcile(self) -> MemoryReconciliationReport:
        dsn = os.environ.get(self.config.postgres_dsn_env)
        if not dsn:
            raise RuntimeError(f"Missing DSN env var: {self.config.postgres_dsn_env}")
        try:
            import psycopg
        except ModuleNotFoundError as error:
            raise RuntimeError("psycopg is required for Postgres reconciliation.") from error
        memory = self._table("shared_memories")
        events = self._table("memory_events")
        sql = f"""
SELECT
  (SELECT count(*) FROM {memory}) AS memories,
  (SELECT count(*) FROM {events}) AS events,
  (SELECT count(*) FROM {events} e LEFT JOIN {memory} m ON m.memory_id = e.memory_id
    WHERE e.memory_id IS NOT NULL AND m.memory_id IS NULL) AS orphan_events,
  (SELECT count(*) FROM {memory} m WHERE NOT EXISTS (
    SELECT 1 FROM {events} e WHERE e.memory_id = m.memory_id AND e.event_type = 'memory.created'
  )) AS memories_without_created_event,
  (SELECT count(*) FROM {events} e JOIN {memory} m ON m.memory_id = e.memory_id
    WHERE e.tenant_id <> m.tenant_id) AS tenant_mismatches,
  (SELECT count(*) FROM {memory} m WHERE
    (m.status = 'deleted' AND NOT EXISTS (
      SELECT 1 FROM {events} e WHERE e.memory_id = m.memory_id AND e.event_type = 'memory.delete'
    )) OR (m.legal_hold AND NOT EXISTS (
      SELECT 1 FROM {events} e WHERE e.memory_id = m.memory_id AND e.event_type = 'memory.hold'
    ))) AS lifecycle_state_mismatches;
""".strip()
        with _driver_errors("reconciliation"):
            with psycopg.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    self._set_tenant_context(cursor)
                    cursor.execute(sql)
                    values = tuple(int(value) for value in cursor.fetchone())
        labels = (
            "orphan events",
            "memories without creation events",
            "cross-tenant event mismatches",
            "lifecycle state mismatches",
        )
        issues = tuple(
            f"{count} {label}"
            for label, count in zip(labels, values[2:], strict=True)
            if count
        )
        return MemoryReconciliationReport(
            memories=values[0],
            events=values[1],
            orphan_events=values[2],
            memories_without_created_event=values[3],
            tenant_mismatches=values[4],
            lifecycle_state_mismatches=values[5],
            schema_version=self.schema_version(),
            issues=issues,
        )

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

    def _lock_operation(self, cursor: object, tenant_id: str, operation_id: str) -> None:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"loro-memory:{tenant_id}:{operation_id}",),
        )


def _content_digest(content: str | None) -> str | None:
    if content is None:
        return None
    import hashlib

    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
