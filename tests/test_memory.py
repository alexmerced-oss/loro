from loro.config import SharedMemoryConfig
from loro.memory.base import SharedMemoryDraft
from loro.memory.local import LocalMemoryStore
from loro.memory.postgres import PostgresSharedMemoryStore
from loro.memory.shared import SharedMemoryDraftStore, shared_memory_schema


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
