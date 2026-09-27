from __future__ import annotations

import os
import re
import shutil
import subprocess
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
    pg_dump, pg_restore = _client_tools(tmp_path)
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ModuleNotFoundError as error:
        pytest.skip(f"Missing integration dependency: {error.name}")

    started = time.monotonic()
    config = SharedMemoryConfig(postgres_schema="loro_memory")
    source = PostgresContainer("postgres:16-alpine").with_tmpfs_mount("/var/lib/postgresql/data")
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
        backup = create_postgres_backup(config, tmp_path / "memory.dump", pg_dump=pg_dump)
    finally:
        source.stop()

    target = PostgresContainer("postgres:16-alpine").with_tmpfs_mount("/var/lib/postgresql/data")
    target.start()
    try:
        target_dsn = _psycopg_dsn(target.get_connection_url())
        restore_postgres_backup(backup, target_dsn, pg_restore=pg_restore)
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


SERVER_MAJOR = 16
SERVER_IMAGE = "postgres:16-alpine"


def _client_major(tool: str) -> int | None:
    path = shutil.which(tool)
    if path is None:
        return None
    output = subprocess.run([path, "--version"], capture_output=True, text=True, check=False)
    match = re.search(r"(\d+)\.", output.stdout)
    return int(match.group(1)) if match else None


def _client_tools(tmp_path: Path) -> tuple[str, str]:
    """Host pg_dump/pg_restore when new enough, else the same tools from the server image.

    pg_dump refuses to dump a newer server, so a host with an older client (common on LTS
    distributions) would fail the drill for reasons unrelated to Loro.
    """

    majors = [_client_major(tool) for tool in ("pg_dump", "pg_restore")]
    if all(major is not None and major >= SERVER_MAJOR for major in majors):
        return "pg_dump", "pg_restore"
    if shutil.which("docker") is None:
        pytest.skip("Need PostgreSQL 16+ client tools or docker for the recovery drill.")
    tools = tmp_path / "pg-client"
    tools.mkdir()
    names = []
    for tool in ("pg_dump", "pg_restore"):
        wrapper = tools / tool
        wrapper.write_text(
            "#!/bin/sh\n"
            'exec docker run --rm --network host --user "$(id -u):$(id -g)" '
            f'--volume "{tmp_path}:{tmp_path}" '
            "--env PGHOST --env PGHOSTADDR --env PGPORT --env PGDATABASE --env PGUSER "
            "--env PGPASSWORD --env PGSSLMODE "
            f'{SERVER_IMAGE} {tool} "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        names.append(str(wrapper))
    return names[0], names[1]


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+psycopg://",
        "postgresql://",
    )
