import builtins
import sys
import types

import pytest

from loro.config import LoroConfig, MemoryConfig, SharedMemoryConfig
from loro.memory.base import SharedMemoryDraft
from loro.memory.drafts import SharedMemoryDraftStore
from loro.memory.iceberg import IcebergSharedMemoryStore
from loro.memory.local import LocalMemoryStore
from loro.memory.operations import search_shared_memories
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
    assert statement.params == {"tenant_id": "acme", "query": "%launch%", "limit": 5}


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
    install_fake_iceberg_modules(
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
    check = IcebergSharedMemoryStore(SharedMemoryConfig()).check()
    assert check.ok is True
    assert any("pyiceberg is importable" in message for message in check.messages)


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

    def schema(self):
        return FakeIcebergSchema()

    def append(self, table):
        self.appended.append(table)

    def scan(self, selected_fields=None):
        return FakeIcebergScan(self.rows)


class FakeIcebergCatalog:
    def __init__(self, rows) -> None:
        self.tables = {
            "enterprise_memory.agent_facts": FakeIcebergTable(rows),
            "enterprise_memory.memory_events": FakeIcebergTable([]),
        }

    def load_table(self, identifier):
        return self.tables[identifier]


def install_fake_iceberg_modules(monkeypatch, *, rows):
    fake_catalog = FakeIcebergCatalog(rows)
    pyiceberg_module = types.ModuleType("pyiceberg")
    catalog_module = types.ModuleType("pyiceberg.catalog")
    pyarrow_module = types.ModuleType("pyarrow")
    pyarrow_module.Table = FakeArrowTable

    def fake_load_catalog(name, **properties):
        return fake_catalog

    catalog_module.load_catalog = fake_load_catalog
    monkeypatch.setitem(sys.modules, "pyiceberg", pyiceberg_module)
    monkeypatch.setitem(sys.modules, "pyiceberg.catalog", catalog_module)
    monkeypatch.setitem(sys.modules, "pyarrow", pyarrow_module)
    return fake_catalog
