from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from loro.config import SharedMemoryConfig
from loro.memory.base import SharedMemoryDraft
from loro.memory.postgres import PostgresSharedMemoryStore
from loro.recovery import DEFAULT_RTO_SECONDS, create_postgres_backup, restore_postgres_backup

pytestmark = pytest.mark.integration


def test_postgres_backup_restore_reconcile_drill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    if os.environ.get("LORO_INTEGRATION_POSTGRES") != "1":
        pytest.skip("Set LORO_INTEGRATION_POSTGRES=1 to run Postgres recovery tests.")
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        pytest.skip("PostgreSQL client tools are required for the recovery drill.")
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ModuleNotFoundError as error:
        pytest.skip(f"Missing integration dependency: {error.name}")

    started = time.monotonic()
    config = SharedMemoryConfig(postgres_schema="loro_memory")
    source = PostgresContainer("postgres:16-alpine").with_tmpfs_mount(
        "/var/lib/postgresql/data"
    )
    source.start()
    try:
        source_dsn = _psycopg_dsn(source.get_connection_url())
        monkeypatch.setenv(config.postgres_dsn_env, source_dsn)
        store = PostgresSharedMemoryStore(config)
        store.migrate()
        store.commit_draft(
            SharedMemoryDraft(
                content="Recovery drill memory",
                summary="Recovery drill",
                tenant_id="acme",
                created_by="recovery-test",
            )
        )
        backup = create_postgres_backup(config, tmp_path / "memory.dump")
    finally:
        source.stop()

    target = PostgresContainer("postgres:16-alpine").with_tmpfs_mount(
        "/var/lib/postgresql/data"
    )
    target.start()
    try:
        target_dsn = _psycopg_dsn(target.get_connection_url())
        restore_postgres_backup(backup, target_dsn)
        monkeypatch.setenv(config.postgres_dsn_env, target_dsn)
        restored = PostgresSharedMemoryStore(config)

        records = restored.search(tenant_id="acme", query="Recovery drill")
        report = restored.reconcile()

        assert len(records) == 1
        assert records[0].content == "Recovery drill memory"
        assert report.ok
        assert report.memories == 1
        assert report.schema_version == 2
        assert time.monotonic() - started < DEFAULT_RTO_SECONDS
    finally:
        target.stop()


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+psycopg://",
        "postgresql://",
    )
