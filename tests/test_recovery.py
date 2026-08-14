from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from loro.config import SharedMemoryConfig
from loro.recovery import (
    _postgres_environment,
    create_postgres_backup,
    restore_postgres_backup,
    verify_postgres_backup,
)


def test_postgres_uri_becomes_minimized_libpq_environment() -> None:
    environment = _postgres_environment(
        "postgresql://user:p%40ss@db.example:5433/loro?sslmode=verify-full&application_name=loro"
    )

    assert environment == {
        "PGHOST": "db.example",
        "PGPORT": "5433",
        "PGDATABASE": "loro",
        "PGUSER": "user",
        "PGPASSWORD": "p@ss",
        "PGSSLMODE": "verify-full",
        "PGAPPNAME": "loro",
    }


@pytest.mark.skipif(os.name == "nt", reason="Fixture uses executable shebang scripts.")
def test_backup_manifest_verification_and_restore_guards(tmp_path: Path, monkeypatch) -> None:
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    _executable(
        bin_path / "pg_dump",
        """
import pathlib, sys
target = next(item.split('=', 1)[1] for item in sys.argv if item.startswith('--file='))
pathlib.Path(target).write_bytes(b'loro-postgres-backup')
""",
    )
    _executable(
        bin_path / "pg_restore",
        """
import os, sys
if '--list' not in sys.argv:
    assert sys.argv[sys.argv.index('--dbname') + 1] == 'restored'
    assert os.environ['PGHOST'] == 'example.invalid'
    assert not any('postgresql://' in argument for argument in sys.argv)
raise SystemExit(0)
""",
    )
    monkeypatch.setenv("PATH", f"{bin_path}:{os.environ.get('PATH', '')}")
    backup = tmp_path / "memory.dump"

    create_postgres_backup(
        SharedMemoryConfig(),
        backup,
        dsn="postgresql://example.invalid/loro",
    )
    verified = verify_postgres_backup(backup)

    assert verified.ok
    assert verified.sha256 and verified.sha256.startswith("sha256:")
    assert backup.with_suffix(".dump.manifest.json").is_file()
    with pytest.raises(RuntimeError, match="explicit destructive authorization"):
        restore_postgres_backup(
            backup,
            "postgresql://example.invalid/restored",
            clean=True,
        )
    restore_postgres_backup(backup, "postgresql://example.invalid/restored")

    backup.write_bytes(b"tampered")
    assert verify_postgres_backup(backup).issue == "Backup checksum does not match manifest."


def _executable(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
