import pytest

from loro.config import SharedMemoryConfig
from loro.memory.base import SharedMemoryDraft
from loro.memory.drafts import SharedMemoryDraftStore
from loro.memory.iceberg import IcebergSharedMemoryStore
from loro.memory.local import LocalMemoryStore
from loro.memory.postgres import PostgresSharedMemoryStore
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


def test_iceberg_rejects_invalid_identifier() -> None:
    with pytest.raises(ValueError, match="iceberg_namespace"):
        IcebergSharedMemoryStore(SharedMemoryConfig(iceberg_namespace="bad-name"))
