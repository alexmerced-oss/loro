import os

import pytest

from loro.config import SharedMemoryConfig
from loro.memory.base import SharedMemoryDraft
from loro.memory.postgres import PostgresSharedMemoryStore
from loro.memory.schemas import shared_memory_schema

pytestmark = pytest.mark.integration


def test_postgres_shared_memory_commit_with_container(monkeypatch) -> None:
    if os.environ.get("LORO_INTEGRATION_POSTGRES") != "1":
        pytest.skip("Set LORO_INTEGRATION_POSTGRES=1 to run Postgres container tests.")
    try:
        import psycopg
        from testcontainers.postgres import PostgresContainer
    except ModuleNotFoundError as error:
        pytest.skip(f"Missing integration dependency: {error.name}")

    container = PostgresContainer("postgres:16-alpine")
    container.start()
    try:
        dsn = _psycopg_dsn(container.get_connection_url())
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(shared_memory_schema("postgres"))
            connection.commit()

        monkeypatch.setenv("LORO_POSTGRES_DSN", dsn)
        draft = SharedMemoryDraft(
            content="Use the launch readiness template",
            summary="Launch readiness template",
            tenant_id="acme",
            created_by="integration-test",
        )
        PostgresSharedMemoryStore(SharedMemoryConfig()).commit_draft(draft)

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
    finally:
        container.stop()


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+psycopg://",
        "postgresql://",
    )
