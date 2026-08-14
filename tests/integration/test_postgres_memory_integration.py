import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from loro.config import SharedMemoryConfig
from loro.memory.base import SharedMemoryDraft, SharedMemoryLifecycleRequest
from loro.memory.postgres import PostgresSharedMemoryStore

pytestmark = pytest.mark.integration


def test_postgres_shared_memory_lifecycle_and_recovery_with_container(monkeypatch) -> None:
    if os.environ.get("LORO_INTEGRATION_POSTGRES") != "1":
        pytest.skip("Set LORO_INTEGRATION_POSTGRES=1 to run Postgres container tests.")
    try:
        import psycopg
        from testcontainers.community.postgres import PostgresContainer
    except ModuleNotFoundError as error:
        pytest.skip(f"Missing integration dependency: {error.name}")

    container = PostgresContainer("postgres:16-alpine").with_tmpfs_mount(
        "/var/lib/postgresql/data"
    )
    container.start()
    try:
        dsn = _psycopg_dsn(container.get_connection_url())
        monkeypatch.setenv("LORO_POSTGRES_DSN", dsn)
        config = SharedMemoryConfig(tenant_isolation="identity")
        store = PostgresSharedMemoryStore(config, authorized_tenant_id="acme")
        store.apply_schema()
        draft = SharedMemoryDraft(
            content="Use the launch readiness template",
            summary="Launch readiness template",
            tenant_id="acme",
            created_by="integration-test",
        )
        store.commit_draft(draft)
        store.commit_draft(draft)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(store.commit_draft, (draft, draft)))
        with pytest.raises(RuntimeError, match="draft ID is already bound"):
            store.commit_draft(replace(draft, content="Conflicting retry"))
        search_results = store.search(tenant_id="acme", query="launch", limit=5)

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('loro.tenant_id', %s, true)", ("acme",))
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
                cursor.execute(
                    "SELECT count(*) FROM shared_memories WHERE tenant_id = %s",
                    ("acme",),
                )
                assert cursor.fetchone()[0] == 1

        assert memory_row == ("Use the launch readiness template", "integration-test")
        assert event_row == ("memory.created", "integration-test")
        assert len(search_results) == 1
        assert search_results[0].content == "Use the launch readiness template"
        assert search_results[0].citation.startswith("postgres:acme/")

        correction = SharedMemoryLifecycleRequest(
            memory_id=memory_id,
            tenant_id="acme",
            action="correct",
            actor="integration-test",
            reason="Correct stale wording",
            content="Use the governed launch readiness template",
            summary="Governed launch readiness template",
        )
        store.apply_lifecycle(correction)
        store.apply_lifecycle(correction)
        hold = SharedMemoryLifecycleRequest(
            memory_id=memory_id,
            tenant_id="acme",
            action="hold",
            actor="integration-test",
            reason="Preserve during investigation",
        )
        store.apply_lifecycle(hold)
        store.apply_lifecycle(hold)
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

        concurrent_drafts = [
            SharedMemoryDraft(
                content=f"Concurrent memory {index}",
                summary=f"Concurrent {index}",
                tenant_id="acme",
                created_by="integration-test",
            )
            for index in range(8)
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(store.commit_draft, concurrent_drafts))

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('loro.tenant_id', %s, true)", ("acme",))
                cursor.execute(
                    "SELECT memory_id FROM shared_memories WHERE summary = %s",
                    ("Concurrent 0",),
                )
                delete_memory_id = str(cursor.fetchone()[0])
        deletion = SharedMemoryLifecycleRequest(
            memory_id=delete_memory_id,
            tenant_id="acme",
            action="delete",
            actor="integration-test",
            reason="Approved deletion",
        )
        store.apply_lifecycle(deletion)
        store.apply_lifecycle(deletion)

        report = store.reconcile()
        assert report.ok
        assert report.memories == 9
        assert report.events >= 14
        assert report.schema_version == 2

        store.migrate(target_version=1)
        assert store.schema_version() == 1
        store.migrate(target_version=2)
        assert store.schema_version() == 2

        isolated = PostgresSharedMemoryStore(config, authorized_tenant_id="acme")
        with pytest.raises(PermissionError, match="Cross-tenant"):
            isolated.render_search(tenant_id="other", query="launch")

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE ROLE loro_rls_reader NOLOGIN")
                cursor.execute("GRANT USAGE ON SCHEMA public TO loro_rls_reader")
                cursor.execute(
                    "GRANT SELECT ON shared_memories, memory_events TO loro_rls_reader"
                )
                cursor.execute("SET ROLE loro_rls_reader")
                cursor.execute("SELECT set_config('loro.tenant_id', %s, true)", ("other",))
                cursor.execute("SELECT count(*) FROM shared_memories")
                assert cursor.fetchone()[0] == 0
                cursor.execute("SELECT set_config('loro.tenant_id', %s, true)", ("acme",))
                cursor.execute("SELECT count(*) FROM shared_memories")
                assert cursor.fetchone()[0] == 9

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('loro.tenant_id', %s, true)", ("acme",))
                cursor.execute(
                    """
                    INSERT INTO memory_events (
                      event_id, memory_id, tenant_id, event_type, actor, payload
                    ) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)
                    """,
                    (
                        str(uuid4()),
                        str(uuid4()),
                        "acme",
                        "memory.test_orphan",
                        "integration-test",
                    ),
                )
            connection.commit()
        drift = store.reconcile()
        assert not drift.ok
        assert drift.orphan_events == 1
    finally:
        container.stop()


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+psycopg://",
        "postgresql://",
    )
