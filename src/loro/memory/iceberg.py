import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from loro.config import SharedMemoryConfig
from loro.memory.base import (
    SharedMemoryBackendCheck,
    SharedMemoryDraft,
    SharedMemoryLifecycleRequest,
    SharedMemorySearchRecord,
    SharedMemoryStatement,
    like_term,
)

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class IcebergTableSnapshotStatus:
    table: str
    snapshot_count: int
    current_snapshot_id: int | None
    parent_snapshot_id: int | None
    sequence_number: int | None
    timestamp_ms: int | None


@dataclass(frozen=True)
class IcebergSnapshotReport:
    memory: IcebergTableSnapshotStatus
    events: IcebergTableSnapshotStatus

    @property
    def aligned(self) -> bool:
        return (
            self.memory.current_snapshot_id is not None
            and self.events.current_snapshot_id is not None
        )


class IcebergSharedMemoryStore:
    """Iceberg shared memory backend.

    This adapter renders SQL compatible with Spark/Trino-style Iceberg engines
    and can execute search/append operations through a configured PyIceberg
    catalog. In enterprise deployments, that PyIceberg catalog should point at
    a Polaris-governed REST catalog or another governed Iceberg catalog.
    """

    def __init__(
        self, config: SharedMemoryConfig, *, authorized_tenant_id: str | None = None
    ) -> None:
        self.config = config
        self.authorized_tenant_id = authorized_tenant_id
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
  expires_at TIMESTAMP,
  legal_hold BOOLEAN,
  deleted_at TIMESTAMP
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

    def render_insert(self, draft: SharedMemoryDraft) -> SharedMemoryStatement:
        self._authorize_tenant(draft.tenant_id)
        memory_id = _draft_memory_id(draft.draft_id)
        event_id = _draft_event_id(draft.draft_id)
        sql = f"""
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
  expires_at,
  legal_hold,
  deleted_at
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
  :expires_at,
  FALSE,
  NULL
);
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
WITH latest AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY memory_id ORDER BY COALESCE(updated_at, created_at) DESC
  ) AS version_rank
  FROM {self.memory_table}
  WHERE tenant_id = :tenant_id
)
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
FROM latest
WHERE version_rank = 1
  AND status = 'active'
  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
  AND (LOWER(content) LIKE LOWER(:query) ESCAPE '\\'
       OR LOWER(summary) LIKE LOWER(:query) ESCAPE '\\')
ORDER BY created_at DESC
LIMIT :limit;
""".strip()
        return SharedMemoryStatement(
            sql=sql,
            params={"tenant_id": tenant_id, "query": like_term(query), "limit": limit},
        )

    def render_lifecycle(self, request: SharedMemoryLifecycleRequest) -> SharedMemoryStatement:
        self._authorize_tenant(request.tenant_id)
        status = "'deleted'" if request.action == "delete" else "status"
        content = ":content" if request.action == "correct" else "content"
        summary = ":summary" if request.action == "correct" else "summary"
        expires_at = ":expires_at" if request.action == "expire" else "expires_at"
        legal_hold = {
            "hold": "TRUE",
            "release_hold": "FALSE",
        }.get(request.action, "legal_hold")
        deleted_at = ":requested_at" if request.action == "delete" else "deleted_at"
        hold_guard = "AND legal_hold = FALSE" if request.action in {"delete", "expire"} else ""
        sql = f"""
INSERT INTO {self.events_table} (
  event_id, memory_id, tenant_id, event_type, actor, event_at, payload
)
VALUES (
  :event_id, :memory_id, :tenant_id, :event_type, :actor, :requested_at, :event_payload
);

INSERT INTO {self.memory_table}
SELECT
  memory_id, tenant_id, scope_type, scope_key, memory_type,
  {content}, {summary}, tags, classification, source, created_by, created_at,
  :requested_at, {status}, confidence, review, embedding_ref, supersedes,
  {expires_at}, {legal_hold}, {deleted_at}
FROM {self.memory_table}
WHERE memory_id = :memory_id AND tenant_id = :tenant_id
  {hold_guard}
ORDER BY COALESCE(updated_at, created_at) DESC
LIMIT 1;
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
                "event_payload": json.dumps({"reason": request.reason}),
            },
        )

    def check(self) -> SharedMemoryBackendCheck:
        messages = [
            f"Tenant isolation: {self.config.tenant_isolation}",
            f"Iceberg catalog: {self.config.iceberg_catalog_name}",
            f"Iceberg memory table: {self.memory_table}",
            f"Iceberg event table: {self.events_table}",
            "Configured Iceberg identifiers are valid.",
        ]
        catalog_props = self._catalog_properties()
        if catalog_props:
            messages.append(
                "Found env-backed Iceberg catalog properties: " + ", ".join(sorted(catalog_props))
            )
        else:
            messages.append(
                "No Loro Iceberg catalog env vars found; "
                "PyIceberg config files/env may still apply."
            )
        try:
            import pyiceberg  # noqa: F401

            messages.append("pyiceberg is importable.")
            pyiceberg_available = True
        except ModuleNotFoundError:
            messages.append("pyiceberg is not installed. Install the data extra.")
            pyiceberg_available = False
        # A readiness check must require a catalog target, mirroring the Postgres sibling's
        # DSN requirement — an importable pyiceberg alone cannot reach any table.
        catalog_configured = bool(catalog_props) or bool(self.config.iceberg_warehouse)
        if not catalog_configured:
            messages.append(
                "No Iceberg catalog target is configured. Set "
                f"{self.config.iceberg_catalog_uri_env} or shared.iceberg_warehouse."
            )
        return SharedMemoryBackendCheck(
            backend="iceberg",
            ok=pyiceberg_available and catalog_configured,
            messages=messages,
        )

    def snapshot_report(self) -> IcebergSnapshotReport:
        """Return content-free snapshot metadata for lifecycle/recovery diagnostics."""
        catalog = self._load_catalog()
        return IcebergSnapshotReport(
            memory=_snapshot_status(catalog.load_table(self.memory_table), self.memory_table),
            events=_snapshot_status(catalog.load_table(self.events_table), self.events_table),
        )

    def commit_draft(self, draft: SharedMemoryDraft) -> None:
        self._authorize_tenant(draft.tenant_id)
        try:
            import pyarrow as pa
        except ModuleNotFoundError as error:
            raise RuntimeError("pyarrow is required for Iceberg shared memory writes.") from error
        memory_statement = self.render_insert(draft)
        memory_row = {
            "memory_id": memory_statement.params["memory_id"],
            "tenant_id": draft.tenant_id,
            "scope_type": draft.scope_type,
            "scope_key": draft.scope_key,
            "memory_type": draft.memory_type,
            "content": draft.content,
            "summary": draft.summary,
            "tags": [],
            "classification": draft.classification,
            "source": memory_statement.params["source"],
            "created_by": draft.created_by,
            "created_at": _iceberg_value(draft.created_at),
            "updated_at": None,
            "status": "active",
            "confidence": None,
            "review": None,
            "embedding_ref": None,
            "supersedes": [],
            "expires_at": _iceberg_value(draft.expires_at),
            "legal_hold": False,
            "deleted_at": None,
        }
        event_row = {
            "event_id": memory_statement.params["event_id"],
            "memory_id": memory_statement.params["memory_id"],
            "tenant_id": draft.tenant_id,
            "event_type": "memory.created",
            "actor": draft.created_by,
            "event_at": _iceberg_value(draft.created_at),
            "payload": memory_statement.params["event_payload"],
        }
        catalog = self._load_catalog()
        memory_table = catalog.load_table(self.memory_table)
        events_table = catalog.load_table(self.events_table)
        memory_exists = _table_contains(memory_table, "memory_id", memory_row["memory_id"])
        event_exists = _table_contains(events_table, "event_id", event_row["event_id"])
        # Iceberg has no cross-table transaction. Commit provenance first and use stable
        # operation IDs so a retry can complete either missing append without duplication.
        if not event_exists:
            events_table.append(
                pa.Table.from_pylist([event_row], schema=events_table.schema().as_arrow())
            )
        if not memory_exists:
            memory_table.append(
                pa.Table.from_pylist([memory_row], schema=memory_table.schema().as_arrow())
            )

    def apply_lifecycle(self, request: SharedMemoryLifecycleRequest) -> None:
        self._authorize_tenant(request.tenant_id)
        try:
            import pyarrow as pa
        except ModuleNotFoundError as error:
            raise RuntimeError("pyarrow is required for Iceberg memory lifecycle.") from error
        catalog = self._load_catalog()
        memory_table = catalog.load_table(self.memory_table)
        events_table = catalog.load_table(self.events_table)
        rows = (
            memory_table.scan(
                row_filter=_memory_filter(
                    tenant_id=request.tenant_id,
                    memory_id=request.memory_id,
                )
            )
            .to_arrow()
            .to_pylist()
        )
        candidates = [row for row in rows if isinstance(row, dict)]
        if not candidates:
            raise RuntimeError("Memory lifecycle target was not found.")
        current = max(candidates, key=_memory_version_time)
        if request.action in {"delete", "expire"} and current.get("legal_hold") is True:
            raise RuntimeError("Memory lifecycle target is protected by legal hold.")
        existing_event = _table_row(events_table, "event_id", request.event_id)
        if existing_event is not None:
            _validate_lifecycle_event(existing_event, request)
        operation_time = (
            existing_event.get("event_at")
            if existing_event is not None
            and isinstance(existing_event.get("event_at"), datetime)
            else request.requested_at
        )
        updated = dict(current)
        updated["updated_at"] = _iceberg_value(operation_time)
        if request.action == "correct":
            updated["content"] = request.content
            updated["summary"] = request.summary
        elif request.action == "delete":
            updated["status"] = "deleted"
            updated["deleted_at"] = _iceberg_value(operation_time)
        elif request.action == "expire":
            updated["expires_at"] = _iceberg_value(request.expires_at)
        elif request.action == "hold":
            updated["legal_hold"] = True
        elif request.action == "release_hold":
            updated["legal_hold"] = False
        event = {
            "event_id": request.event_id,
            "memory_id": request.memory_id,
            "tenant_id": request.tenant_id,
            "event_type": f"memory.{request.action}",
            "actor": request.actor,
            "event_at": _iceberg_value(request.requested_at),
            "payload": json.dumps({"reason": request.reason}),
        }
        version_exists = any(
            _lifecycle_version_matches(row, request, operation_time) for row in candidates
        )
        if existing_event is None:
            events_table.append(
                pa.Table.from_pylist([event], schema=events_table.schema().as_arrow())
            )
        if not version_exists:
            memory_table.append(
                pa.Table.from_pylist([updated], schema=memory_table.schema().as_arrow())
            )

    def search(
        self,
        *,
        tenant_id: str,
        query: str,
        limit: int = 20,
    ) -> list[SharedMemorySearchRecord]:
        self._authorize_tenant(tenant_id)
        try:
            import pyarrow as pa
        except ModuleNotFoundError as error:
            raise RuntimeError("pyarrow is required for Iceberg shared memory search.") from error
        catalog = self._load_catalog()
        table = catalog.load_table(self.memory_table)
        arrow_table = table.scan(
            row_filter=_memory_filter(tenant_id=tenant_id),
            selected_fields=(
                "memory_id",
                "tenant_id",
                "scope_type",
                "scope_key",
                "memory_type",
                "content",
                "summary",
                "classification",
                "created_by",
                "created_at",
                "status",
                "updated_at",
                "expires_at",
            ),
        ).to_arrow()
        if not isinstance(arrow_table, pa.Table):
            raise RuntimeError("PyIceberg search did not return a PyArrow table.")
        needle = query.lower()
        records: list[SharedMemorySearchRecord] = []
        latest: dict[str, dict[str, Any]] = {}
        for row in arrow_table.to_pylist():
            if not isinstance(row, dict) or row.get("tenant_id") != tenant_id:
                continue
            memory_id = str(row.get("memory_id") or "")
            if memory_id not in latest or _memory_version_time(row) > _memory_version_time(
                latest[memory_id]
            ):
                latest[memory_id] = row
        now = datetime.now(UTC)
        for row in sorted(latest.values(), key=_memory_version_time, reverse=True):
            if len(records) >= limit:
                break
            if row.get("tenant_id") != tenant_id or row.get("status") != "active":
                continue
            expires_at_value = row.get("expires_at")
            if isinstance(expires_at_value, datetime):
                comparable = expires_at_value
                if comparable.tzinfo is None:
                    comparable = comparable.replace(tzinfo=UTC)
                if comparable <= now:
                    continue
            content = str(row.get("content") or "")
            summary = str(row.get("summary") or "")
            if needle not in content.lower() and needle not in summary.lower():
                continue
            records.append(
                SharedMemorySearchRecord(
                    memory_id=str(row.get("memory_id") or ""),
                    tenant_id=str(row.get("tenant_id") or ""),
                    scope_type=str(row.get("scope_type") or ""),
                    scope_key=str(row.get("scope_key") or ""),
                    memory_type=str(row.get("memory_type") or ""),
                    content=content,
                    summary=summary,
                    classification=str(row.get("classification") or ""),
                    created_by=str(row.get("created_by") or ""),
                    created_at=_created_at_text(row.get("created_at")),
                    status=str(row.get("status") or ""),
                    backend="iceberg",
                )
            )
        return records

    def list_expired(self, *, tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Active memories whose expiry has passed, newest version per memory_id."""

        self._authorize_tenant(tenant_id)
        catalog = self._load_catalog()
        table = catalog.load_table(self.memory_table)
        arrow_table = table.scan(row_filter=_memory_filter(tenant_id=tenant_id)).to_arrow()
        latest: dict[str, dict[str, Any]] = {}
        for row in arrow_table.to_pylist():
            if not isinstance(row, dict) or row.get("tenant_id") != tenant_id:
                continue
            memory_id = str(row.get("memory_id") or "")
            if memory_id not in latest or _memory_version_time(row) > _memory_version_time(
                latest[memory_id]
            ):
                latest[memory_id] = row
        now = datetime.now(UTC)
        expired: list[dict[str, Any]] = []
        for row in latest.values():
            if row.get("status") != "active":
                continue
            expires_at = row.get("expires_at")
            if not isinstance(expires_at, datetime):
                continue
            comparable = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
            if comparable > now:
                continue
            expired.append(
                {
                    "memory_id": str(row.get("memory_id") or ""),
                    "tenant_id": str(row.get("tenant_id") or ""),
                    "summary": str(row.get("summary") or ""),
                    "expires_at": comparable.isoformat(),
                    "legal_hold": bool(row.get("legal_hold")),
                }
            )
            if len(expired) >= limit:
                break
        return expired

    def _authorize_tenant(self, tenant_id: str) -> None:
        if self.config.tenant_isolation != "identity":
            return
        if not self.authorized_tenant_id:
            raise PermissionError("Identity tenant is required for shared-memory access.")
        if tenant_id != self.authorized_tenant_id:
            raise PermissionError(f"Cross-tenant shared-memory access denied: {tenant_id}")

    def _load_catalog(self) -> Any:
        try:
            from pyiceberg.catalog import load_catalog
        except ModuleNotFoundError as error:
            raise RuntimeError("pyiceberg is required for Iceberg shared memory access.") from error
        try:
            return load_catalog(self.config.iceberg_catalog_name, **self._catalog_properties())
        except Exception as error:
            raise RuntimeError(f"Failed to load Iceberg catalog: {error}") from error

    def _catalog_properties(self) -> dict[str, str]:
        properties: dict[str, str] = {}
        uri = os.environ.get(self.config.iceberg_catalog_uri_env)
        credential = os.environ.get(self.config.iceberg_credential_env)
        token = os.environ.get(self.config.iceberg_token_env)
        if uri:
            properties["type"] = "rest"
            properties["uri"] = uri
            properties["header.X-Iceberg-Access-Delegation"] = "vended-credentials"
        if self.config.iceberg_warehouse:
            properties["warehouse"] = self.config.iceberg_warehouse
        if credential:
            properties["credential"] = credential
        if token:
            properties["token"] = token
        return properties


def _memory_filter(*, tenant_id: str, memory_id: str | None = None) -> Any:
    """Build a typed PyIceberg row filter for the tenant-isolation boundary.

    Tenant isolation must never depend on hand-rolled quoting inside the filter DSL, so
    the values are bound as typed literals instead of interpolated into a string.
    """

    try:
        from pyiceberg.expressions import And, EqualTo
    except ModuleNotFoundError as error:
        raise RuntimeError("pyiceberg is required for Iceberg shared memory access.") from error
    tenant_predicate = EqualTo("tenant_id", tenant_id)
    if memory_id is None:
        return tenant_predicate
    return And(tenant_predicate, EqualTo("memory_id", memory_id))


def _iceberg_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.replace(tzinfo=None)
    return value


def _draft_memory_id(draft_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"loro:shared-memory:{draft_id}"))


def _draft_event_id(draft_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"loro:shared-memory-event:{draft_id}"))


def _table_contains(table: Any, field: str, value: str) -> bool:
    return _table_row(table, field, value, selected_fields=(field,)) is not None


def _table_row(
    table: Any,
    field: str,
    value: str,
    *,
    selected_fields: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    try:
        from pyiceberg.expressions import EqualTo
    except ModuleNotFoundError as error:
        raise RuntimeError("pyiceberg is required for Iceberg shared memory access.") from error
    options: dict[str, Any] = {"row_filter": EqualTo(field, value)}
    if selected_fields is not None:
        options["selected_fields"] = selected_fields
    rows = table.scan(**options).to_arrow()
    return next(
        (
            row
            for row in rows.to_pylist()
            if isinstance(row, dict) and str(row.get(field) or "") == value
        ),
        None,
    )


def _same_instant(left: Any, right: datetime) -> bool:
    if not isinstance(left, datetime):
        return False
    left_utc = left.replace(tzinfo=UTC) if left.tzinfo is None else left.astimezone(UTC)
    right_utc = right.replace(tzinfo=UTC) if right.tzinfo is None else right.astimezone(UTC)
    return left_utc == right_utc


def _lifecycle_version_matches(
    row: dict[str, Any],
    request: SharedMemoryLifecycleRequest,
    operation_time: datetime,
) -> bool:
    if not _same_instant(row.get("updated_at"), operation_time):
        return False
    if request.action == "correct":
        return row.get("content") == request.content and row.get("summary") == request.summary
    if request.action == "delete":
        return row.get("status") == "deleted" and _same_instant(
            row.get("deleted_at"), operation_time
        )
    if request.action == "expire" and request.expires_at is not None:
        return _same_instant(row.get("expires_at"), request.expires_at)
    if request.action == "hold":
        return row.get("legal_hold") is True
    return row.get("legal_hold") is False


def _validate_lifecycle_event(
    event: dict[str, Any],
    request: SharedMemoryLifecycleRequest,
) -> None:
    expected = {
        "memory_id": request.memory_id,
        "tenant_id": request.tenant_id,
        "event_type": f"memory.{request.action}",
    }
    if any(str(event.get(field) or "") != value for field, value in expected.items()):
        raise RuntimeError("Iceberg lifecycle operation ID is already bound to another action.")


def _created_at_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _memory_version_time(row: dict[str, Any]) -> datetime:
    value = row.get("updated_at") or row.get("created_at")
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=UTC)


def _snapshot_status(table: Any, name: str) -> IcebergTableSnapshotStatus:
    metadata = getattr(table, "metadata", None)
    snapshots = getattr(metadata, "snapshots", ()) if metadata is not None else ()
    current = table.current_snapshot() if hasattr(table, "current_snapshot") else None
    return IcebergTableSnapshotStatus(
        table=name,
        snapshot_count=len(snapshots or ()),
        current_snapshot_id=getattr(current, "snapshot_id", None),
        parent_snapshot_id=getattr(current, "parent_snapshot_id", None),
        sequence_number=getattr(current, "sequence_number", None),
        timestamp_ms=getattr(current, "timestamp_ms", None),
    )
