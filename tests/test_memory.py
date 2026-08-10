import builtins
import sys
import types
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from loro.config import IdentityConfig, LoroConfig, MemoryConfig, SharedMemoryConfig
from loro.memory.base import SharedMemoryDraft, SharedMemoryLifecycleRequest
from loro.memory.drafts import SharedMemoryDraftStore
from loro.memory.iceberg import IcebergSharedMemoryStore
from loro.memory.local import LocalMemoryStore
from loro.memory.operations import (
    apply_shared_memory_lifecycle,
    create_shared_memory_draft,
    search_shared_memories,
)
from loro.memory.postgres import PostgresSharedMemoryStore
from loro.memory.proposals import MemoryProposal, MemoryProposalStore
from loro.memory.schemas import shared_memory_schema
from loro.memory.shared import SharedMemoryDraftStore as CompatDraftStore


def test_local_memory_search(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path)
    first = store.remember("Status briefs include risks and next steps")
    store.remember("Use two-space indentation")
    matches = store.search("briefs")
    assert matches == [first]


def test_shared_memory_schema_postgres() -> None:
    schema = shared_memory_schema("postgres")
    assert "CREATE TABLE IF NOT EXISTS shared_memories" in schema
    assert "memory_events" in schema


def test_shared_memory_schema_iceberg_uses_config() -> None:
    schema = shared_memory_schema(
        "iceberg",
        SharedMemoryConfig(
            iceberg_namespace="enterprise_memory",
            iceberg_table="agent_facts",
        ),
    )
    assert "CREATE TABLE IF NOT EXISTS enterprise_memory.agent_facts" in schema
    assert "CREATE TABLE IF NOT EXISTS enterprise_memory.memory_events" in schema


def test_shared_memory_draft_store(tmp_path) -> None:
    store = SharedMemoryDraftStore(tmp_path)
    draft = store.stage(
        SharedMemoryDraft(
            content="Use the launch readiness template",
            summary="Use the launch readiness template",
            tenant_id="acme",
        )
    )
    drafts = store.list()
    assert drafts[0] == draft
    assert store.get(draft.draft_id) == draft
    assert store.get("missing") is None


def test_shared_memory_draft_store_hides_and_rejects_other_tenants(tmp_path) -> None:
    unrestricted = SharedMemoryDraftStore(tmp_path)
    own = unrestricted.stage(SharedMemoryDraft(content="own", summary="own", tenant_id="acme"))
    unrestricted.stage(SharedMemoryDraft(content="other", summary="other", tenant_id="other"))
    isolated = SharedMemoryDraftStore(tmp_path, authorized_tenant_id="acme")

    assert isolated.list() == [own]
    with pytest.raises(PermissionError, match="Cross-tenant"):
        isolated.stage(SharedMemoryDraft(content="denied", summary="denied", tenant_id="other"))


def test_memory_proposal_store_roundtrip(tmp_path) -> None:
    store = MemoryProposalStore(tmp_path)
    proposal = store.propose(
        MemoryProposal(
            content="Use launch readiness template",
            target="shared",
            rationale="Repeated launch work",
        )
    )
    assert store.get(proposal.proposal_id) == proposal
    updated = store.update_status(proposal.proposal_id, "accepted_as_shared_draft")
    assert updated is not None
    assert updated.status == "accepted_as_shared_draft"
    assert store.list()[0].status == "accepted_as_shared_draft"


def test_shared_memory_compat_imports() -> None:
    assert CompatDraftStore is SharedMemoryDraftStore


def test_postgres_shared_memory_insert_sql() -> None:
    draft = SharedMemoryDraft(
        content="Use the launch readiness template",
        summary="Launch template",
        tenant_id="acme",
        created_by="alex",
    )
    statement = PostgresSharedMemoryStore(SharedMemoryConfig()).render_insert(draft)
    assert "INSERT INTO public.shared_memories" in statement.sql
    assert "INSERT INTO public.memory_events" in statement.sql
    assert statement.params["tenant_id"] == "acme"
    assert statement.params["content"] == "Use the launch readiness template"
    assert statement.params["created_by"] == "alex"


def test_postgres_shared_memory_search_sql() -> None:
    statement = PostgresSharedMemoryStore(SharedMemoryConfig()).render_search(
        tenant_id="acme",
        query="launch",
        limit=5,
    )
    assert "FROM public.shared_memories" in statement.sql
    assert "ILIKE %(query)s" in statement.sql
    assert "expires_at > now()" in statement.sql
    assert statement.params == {"tenant_id": "acme", "query": "%launch%", "limit": 5}


@pytest.mark.parametrize("action", ["correct", "delete", "expire", "hold", "release_hold"])
def test_postgres_memory_lifecycle_renders_event_and_guards_hold(action) -> None:
    request = SharedMemoryLifecycleRequest(
        memory_id="2c0e6f41-b2f9-4e62-bcdc-0532dc18dc39",
        tenant_id="acme",
        action=action,
        actor="alex",
        reason="Policy lifecycle test",
        content="Corrected content" if action == "correct" else None,
        summary="Corrected" if action == "correct" else None,
        expires_at=datetime.now(UTC) if action == "expire" else None,
    )
    statement = PostgresSharedMemoryStore(SharedMemoryConfig()).render_lifecycle(request)

    assert "INSERT INTO public.memory_events" in statement.sql
    assert statement.params["event_type"] == f"memory.{action}"
    if action in {"delete", "expire"}:
        assert "AND legal_hold = FALSE" in statement.sql


def test_memory_retention_and_lifecycle_validation() -> None:
    before = datetime.now(UTC) + timedelta(days=29)
    draft = create_shared_memory_draft(
        "Retained memory",
        tenant_id="acme",
        scope_type="org",
        scope_key="default",
        memory_type="fact",
        classification="internal",
        created_by="alex",
        retention_days=30,
    )
    assert draft.expires_at is not None and draft.expires_at > before

    request = SharedMemoryLifecycleRequest(
        memory_id="memory-1",
        tenant_id="acme",
        action="correct",
        actor="alex",
        reason="Correct stale guidance",
    )
    with pytest.raises(ValueError, match="replacement content"):
        apply_shared_memory_lifecycle(LoroConfig(), request)


def test_postgres_identity_isolation_rejects_cross_tenant_and_renders_rls() -> None:
    config = SharedMemoryConfig(tenant_isolation="identity")
    store = PostgresSharedMemoryStore(config, authorized_tenant_id="acme")

    own = store.render_search(tenant_id="acme", query="launch")

    assert own.params["tenant_id"] == "acme"
    with pytest.raises(PermissionError, match="Cross-tenant"):
        store.render_search(tenant_id="other", query="launch")
    schema = store.render_schema()
    assert "FORCE ROW LEVEL SECURITY" in schema
    assert "current_setting('loro.tenant_id', true)" in schema


def test_shared_memory_operation_binds_requested_tenant_to_identity() -> None:
    config = LoroConfig(
        identity=IdentityConfig(tenant="acme"),
        memory=MemoryConfig(shared=SharedMemoryConfig(tenant_isolation="identity")),
    )

    own = search_shared_memories(config, query="launch", tenant_id="acme", execute=False)

    assert own.tenant_id == "acme"
    with pytest.raises(PermissionError, match="other"):
        search_shared_memories(config, query="launch", tenant_id="other", execute=False)


def test_search_shared_memories_renders_postgres_without_dsn(monkeypatch) -> None:
    monkeypatch.delenv("LORO_POSTGRES_DSN", raising=False)
    result = search_shared_memories(
        LoroConfig(),
        query="launch",
        tenant_id="acme",
        limit=5,
    )
    assert result.backend == "postgres"
    assert result.executed is False
    assert result.statement is not None
    assert "FROM public.shared_memories" in result.statement.sql
    assert any("Rendered SQL" in message for message in result.messages)


def test_postgres_shared_memory_schema_uses_configured_schema() -> None:
    schema = PostgresSharedMemoryStore(
        SharedMemoryConfig(postgres_schema="agent_memory")
    ).render_schema()
    assert "CREATE SCHEMA IF NOT EXISTS agent_memory" in schema
    assert "CREATE TABLE IF NOT EXISTS agent_memory.shared_memories" in schema
    assert "CREATE TABLE IF NOT EXISTS agent_memory.memory_events" in schema
    assert "idx_agent_memory_shared_memories_scope" in schema


def test_postgres_apply_schema_missing_dsn(monkeypatch) -> None:
    monkeypatch.delenv("LORO_POSTGRES_DSN", raising=False)
    with pytest.raises(RuntimeError, match="Missing DSN env var"):
        PostgresSharedMemoryStore(SharedMemoryConfig()).apply_schema()


def test_postgres_backend_check_missing_dsn(monkeypatch) -> None:
    monkeypatch.delenv("LORO_POSTGRES_DSN", raising=False)
    check = PostgresSharedMemoryStore(SharedMemoryConfig()).check()
    assert check.backend == "postgres"
    assert check.ok is False
    assert any("Missing DSN env var: LORO_POSTGRES_DSN" in message for message in check.messages)


def test_iceberg_shared_memory_insert_sql() -> None:
    draft = SharedMemoryDraft(
        content="Use the enterprise launch template",
        summary="Launch template",
        tenant_id="acme",
        created_by="alex",
    )
    store = IcebergSharedMemoryStore(
        SharedMemoryConfig(iceberg_namespace="enterprise_memory", iceberg_table="agent_facts")
    )
    statement = store.render_insert(draft)
    assert "INSERT INTO enterprise_memory.agent_facts" in statement.sql
    assert "INSERT INTO enterprise_memory.memory_events" in statement.sql
    assert statement.params["tenant_id"] == "acme"
    assert statement.params["content"] == "Use the enterprise launch template"


def test_iceberg_shared_memory_search_sql() -> None:
    store = IcebergSharedMemoryStore(
        SharedMemoryConfig(iceberg_namespace="enterprise_memory", iceberg_table="agent_facts")
    )
    statement = store.render_search(tenant_id="acme", query="launch", limit=10)
    assert "FROM enterprise_memory.agent_facts" in statement.sql
    assert "LOWER(content) LIKE LOWER(:query)" in statement.sql
    assert "ROW_NUMBER() OVER" in statement.sql
    assert statement.params == {"tenant_id": "acme", "query": "%launch%", "limit": 10}


def test_search_shared_memories_renders_iceberg() -> None:
    result = search_shared_memories(
        LoroConfig(
            memory=MemoryConfig(
                shared=SharedMemoryConfig(
                    backend="iceberg",
                    iceberg_namespace="enterprise_memory",
                    iceberg_table="agent_facts",
                )
            )
        ),
        query="launch",
        tenant_id="acme",
        execute=False,
    )
    assert result.backend == "iceberg"
    assert result.executed is False
    assert result.statement is not None
    assert "FROM enterprise_memory.agent_facts" in result.statement.sql
    assert any("Rendered Iceberg" in message for message in result.messages)


def test_search_shared_memories_executes_iceberg(monkeypatch) -> None:
    fake_catalog = install_fake_iceberg_modules(
        monkeypatch,
        rows=[
            {
                "memory_id": "old",
                "tenant_id": "acme",
                "scope_type": "team",
                "scope_key": "platform",
                "memory_type": "fact",
                "content": "Unrelated item",
                "summary": "Other",
                "classification": "public-internal",
                "created_by": "alex",
                "created_at": "2026-07-01T00:00:00",
                "status": "active",
            },
            {
                "memory_id": "mem-1",
                "tenant_id": "acme",
                "scope_type": "team",
                "scope_key": "platform",
                "memory_type": "fact",
                "content": "Use the launch readiness template",
                "summary": "Launch template",
                "classification": "public-internal",
                "created_by": "alex",
                "created_at": "2026-07-02T00:00:00",
                "status": "active",
            },
        ],
    )
    result = search_shared_memories(
        LoroConfig(
            memory=MemoryConfig(
                shared=SharedMemoryConfig(
                    backend="iceberg",
                    iceberg_namespace="enterprise_memory",
                    iceberg_table="agent_facts",
                )
            )
        ),
        query="launch",
        tenant_id="acme",
        execute=True,
    )

    assert result.executed is True
    assert len(result.records) == 1
    assert result.records[0].memory_id == "mem-1"
    assert result.records[0].citation == "iceberg:acme/team/platform/mem-1"
    scan_options = fake_catalog.tables["enterprise_memory.agent_facts"].scan_options
    # The tenant-isolation predicate must be a typed expression, never an interpolated
    # string that relies on hand-rolled quoting inside the PyIceberg filter parser.
    assert scan_options["row_filter"] == FakeEqualTo(term="tenant_id", value="acme")


def test_iceberg_lifecycle_appends_version_and_event(monkeypatch) -> None:
    created = datetime(2026, 7, 1, tzinfo=UTC)
    fake_catalog = install_fake_iceberg_modules(
        monkeypatch,
        rows=[
            {
                "memory_id": "mem-1",
                "tenant_id": "acme",
                "scope_type": "team",
                "scope_key": "platform",
                "memory_type": "fact",
                "content": "Old",
                "summary": "Old",
                "tags": [],
                "classification": "internal",
                "source": "{}",
                "created_by": "alex",
                "created_at": created,
                "updated_at": None,
                "status": "active",
                "confidence": None,
                "review": None,
                "embedding_ref": None,
                "supersedes": [],
                "expires_at": None,
                "legal_hold": False,
                "deleted_at": None,
            }
        ],
    )
    request = SharedMemoryLifecycleRequest(
        memory_id="mem-1",
        tenant_id="acme",
        action="correct",
        actor="reviewer",
        reason="Updated guidance",
        content="New",
        summary="New summary",
    )
    store = IcebergSharedMemoryStore(
        SharedMemoryConfig(iceberg_namespace="enterprise_memory", iceberg_table="agent_facts")
    )
    store.apply_lifecycle(request)

    version = fake_catalog.tables["enterprise_memory.agent_facts"].appended[0].to_pylist()[0]
    event = fake_catalog.tables["enterprise_memory.memory_events"].appended[0].to_pylist()[0]
    assert version["content"] == "New"
    assert version["updated_at"] is not None
    assert event["event_type"] == "memory.correct"


def test_iceberg_lifecycle_blocks_delete_under_legal_hold(monkeypatch) -> None:
    fake_catalog = install_fake_iceberg_modules(
        monkeypatch,
        rows=[
            {
                "memory_id": "mem-1",
                "tenant_id": "acme",
                "created_at": datetime(2026, 7, 1, tzinfo=UTC),
                "legal_hold": True,
            }
        ],
    )
    request = SharedMemoryLifecycleRequest(
        memory_id="mem-1",
        tenant_id="acme",
        action="delete",
        actor="reviewer",
        reason="Deletion request",
    )
    store = IcebergSharedMemoryStore(
        SharedMemoryConfig(iceberg_namespace="enterprise_memory", iceberg_table="agent_facts")
    )

    with pytest.raises(RuntimeError, match="legal hold"):
        store.apply_lifecycle(request)
    assert not fake_catalog.tables["enterprise_memory.agent_facts"].appended


def test_iceberg_commit_draft_appends_memory_and_event(monkeypatch) -> None:
    fake_catalog = install_fake_iceberg_modules(monkeypatch, rows=[])
    draft = SharedMemoryDraft(
        content="Use the enterprise launch template",
        summary="Launch template",
        tenant_id="acme",
        scope_type="team",
        scope_key="platform",
        created_by="alex",
    )
    IcebergSharedMemoryStore(
        SharedMemoryConfig(
            iceberg_namespace="enterprise_memory",
            iceberg_table="agent_facts",
        )
    ).commit_draft(draft)

    memory_rows = fake_catalog.tables["enterprise_memory.agent_facts"].appended[0].to_pylist()
    event_rows = fake_catalog.tables["enterprise_memory.memory_events"].appended[0].to_pylist()
    assert memory_rows[0]["tenant_id"] == "acme"
    assert memory_rows[0]["status"] == "active"
    assert memory_rows[0]["source"] == '{"source": "loro.shared_memory_draft"}'
    assert event_rows[0]["event_type"] == "memory.created"
    assert event_rows[0]["payload"] == f'{{"draft_id": "{draft.draft_id}"}}'


def test_iceberg_rejects_invalid_identifier() -> None:
    with pytest.raises(ValueError, match="iceberg_namespace"):
        IcebergSharedMemoryStore(SharedMemoryConfig(iceberg_namespace="bad-name"))


def test_iceberg_backend_check_reports_missing_pyiceberg(monkeypatch) -> None:
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pyiceberg":
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    check = IcebergSharedMemoryStore(SharedMemoryConfig()).check()
    assert check.backend == "iceberg"
    assert check.ok is False
    assert any("pyiceberg is not installed" in message for message in check.messages)


def test_iceberg_backend_check_accepts_importable_pyiceberg(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pyiceberg", types.ModuleType("pyiceberg"))
    monkeypatch.setenv("LORO_ICEBERG_CATALOG_URI", "https://polaris.example.com/api/catalog")
    check = IcebergSharedMemoryStore(SharedMemoryConfig()).check()
    assert check.ok is True
    assert any("pyiceberg is importable" in message for message in check.messages)


def test_iceberg_backend_check_requires_catalog_target(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pyiceberg", types.ModuleType("pyiceberg"))
    monkeypatch.delenv("LORO_ICEBERG_CATALOG_URI", raising=False)
    check = IcebergSharedMemoryStore(SharedMemoryConfig()).check()
    assert check.ok is False
    assert any("No Iceberg catalog target is configured" in message for message in check.messages)


class FakeArrowTable:
    def __init__(self, rows, schema=None) -> None:
        self.rows = rows
        self.arrow_schema = schema

    @classmethod
    def from_pylist(cls, rows, schema=None):
        return cls(rows, schema=schema)

    def to_pylist(self):
        return self.rows


class FakeIcebergSchema:
    def as_arrow(self):
        return "fake-arrow-schema"


class FakeIcebergScan:
    def __init__(self, rows) -> None:
        self.rows = rows

    def to_arrow(self):
        return FakeArrowTable(self.rows)


class FakeIcebergTable:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.appended = []
        self.scan_options = {}

    def schema(self):
        return FakeIcebergSchema()

    def append(self, table):
        self.appended.append(table)

    def scan(self, selected_fields=None, **options):
        self.scan_options = {"selected_fields": selected_fields, **options}
        return FakeIcebergScan(self.rows)


class FakeIcebergCatalog:
    def __init__(self, rows) -> None:
        self.tables = {
            "enterprise_memory.agent_facts": FakeIcebergTable(rows),
            "enterprise_memory.memory_events": FakeIcebergTable([]),
        }

    def load_table(self, identifier):
        return self.tables[identifier]


@dataclass(frozen=True)
class FakeEqualTo:
    term: str
    value: object


@dataclass(frozen=True)
class FakeAnd:
    left: object
    right: object


def install_fake_iceberg_modules(monkeypatch, *, rows):
    fake_catalog = FakeIcebergCatalog(rows)
    pyiceberg_module = types.ModuleType("pyiceberg")
    catalog_module = types.ModuleType("pyiceberg.catalog")
    expressions_module = types.ModuleType("pyiceberg.expressions")
    expressions_module.EqualTo = FakeEqualTo
    expressions_module.And = FakeAnd
    pyarrow_module = types.ModuleType("pyarrow")
    pyarrow_module.Table = FakeArrowTable

    def fake_load_catalog(name, **properties):
        return fake_catalog

    catalog_module.load_catalog = fake_load_catalog
    monkeypatch.setitem(sys.modules, "pyiceberg", pyiceberg_module)
    monkeypatch.setitem(sys.modules, "pyiceberg.catalog", catalog_module)
    monkeypatch.setitem(sys.modules, "pyiceberg.expressions", expressions_module)
    monkeypatch.setitem(sys.modules, "pyarrow", pyarrow_module)
    return fake_catalog
