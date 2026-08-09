import os
from datetime import UTC, datetime

import pytest

from loro.config import SharedMemoryConfig
from loro.memory.base import SharedMemoryDraft, SharedMemoryLifecycleRequest
from loro.memory.postgres import PostgresSharedMemoryStore

pytestmark = pytest.mark.integration


def test_postgres_shared_memory_commit_with_container(monkeypatch) -> None:
    if os.environ.get("LORO_INTEGRATION_POSTGRES") != "1":
        pytest.skip("Set LORO_INTEGRATION_POSTGRES=1 to run Postgres container tests.")
    try:
        import psycopg
        from testcontainers.community.postgres import PostgresContainer
    except ModuleNotFoundError as error:
        pytest.skip(f"Missing integration dependency: {error.name}")

    container = PostgresContainer("postgres:16-alpine")
    container.start()
    try:
        dsn = _psycopg_dsn(container.get_connection_url())
        monkeypatch.setenv("LORO_POSTGRES_DSN", dsn)
        store = PostgresSharedMemoryStore(SharedMemoryConfig())
        store.apply_schema()
        draft = SharedMemoryDraft(
            content="Use the launch readiness template",
            summary="Launch readiness template",
            tenant_id="acme",
            created_by="integration-test",
        )
        store.commit_draft(draft)
        search_results = store.search(tenant_id="acme", query="launch", limit=5)

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT content, created_by
                    FROM shared_memories
                    WHERE tenant_id = %s
                    """,
                    ("acme",),
                )
                memory_row = cursor.fetchone()
                cursor.execute(
                    "SELECT memory_id FROM shared_memories WHERE tenant_id = %s",
                    ("acme",),
                )
                memory_id = str(cursor.fetchone()[0])
                cursor.execute(
                    """
                    SELECT event_type, actor
                    FROM memory_events
                    WHERE tenant_id = %s
                    """,
                    ("acme",),
                )
                event_row = cursor.fetchone()

        assert memory_row == ("Use the launch readiness template", "integration-test")
        assert event_row == ("memory.created", "integration-test")
        assert len(search_results) == 1
        assert search_results[0].content == "Use the launch readiness template"
        assert search_results[0].citation.startswith("postgres:acme/")

        store.apply_lifecycle(
            SharedMemoryLifecycleRequest(
                memory_id=memory_id,
                tenant_id="acme",
                action="hold",
                actor="integration-test",
                reason="Preserve during investigation",
            )
        )
        with pytest.raises(RuntimeError, match="legal hold"):
            store.apply_lifecycle(
                SharedMemoryLifecycleRequest(
                    memory_id=memory_id,
                    tenant_id="acme",
                    action="delete",
                    actor="integration-test",
                    reason="Deletion request",
                )
            )
        store.apply_lifecycle(
            SharedMemoryLifecycleRequest(
                memory_id=memory_id,
                tenant_id="acme",
                action="release_hold",
                actor="integration-test",
                reason="Investigation complete",
            )
        )
        store.apply_lifecycle(
            SharedMemoryLifecycleRequest(
                memory_id=memory_id,
                tenant_id="acme",
                action="expire",
                actor="integration-test",
                reason="Retention elapsed",
                expires_at=datetime.now(UTC),
            )
        )
        assert store.search(tenant_id="acme", query="launch", limit=5) == []
    finally:
        container.stop()


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+psycopg://",
        "postgresql://",
    )
