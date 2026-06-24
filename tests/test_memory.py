from loro.memory.base import SharedMemoryDraft
from loro.memory.local import LocalMemoryStore
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
