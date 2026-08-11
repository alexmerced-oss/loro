from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from loro.config import SharedMemoryConfig
from loro.memory.postgres import PostgresSharedMemoryStore

RECOVERY_SCHEMA_VERSION = "1.0"
DEFAULT_RPO_SECONDS = 300
DEFAULT_RTO_SECONDS = 900


@dataclass(frozen=True)
class BackupVerification:
    ok: bool
    backup: str
    manifest: str
    sha256: str | None
    schema_version: int | None
    issue: str | None = None


def create_postgres_backup(
    config: SharedMemoryConfig,
    output: str | Path,
    *,
    dsn: str | None = None,
    pg_dump: str = "pg_dump",
    timeout_seconds: int = DEFAULT_RTO_SECONDS,
) -> Path:
    database_url = dsn or os.environ.get(config.postgres_dsn_env)
    if not database_url:
        raise RuntimeError(f"Missing DSN env var: {config.postgres_dsn_env}")
    executable = _executable(pg_dump)
    destination = Path(output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    _run(
        [
            executable,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--schema={config.postgres_schema}",
            f"--file={temporary}",
        ],
        dsn=database_url,
        timeout_seconds=timeout_seconds,
    )
    os.replace(temporary, destination)
    schema_version = PostgresSharedMemoryStore(config).schema_version() if dsn is None else None
    manifest = {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "backend": "postgres",
        "postgres_schema": config.postgres_schema,
        "memory_schema_version": schema_version,
        "backup_format": "postgres-custom",
        "backup_file": destination.name,
        "sha256": _sha256(destination),
        "bytes": destination.stat().st_size,
        "targets": {
            "rpo_seconds": DEFAULT_RPO_SECONDS,
            "rto_seconds": DEFAULT_RTO_SECONDS,
        },
    }
    _manifest_path(destination).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def verify_postgres_backup(
    backup: str | Path,
    *,
    pg_restore: str = "pg_restore",
    timeout_seconds: int = 120,
) -> BackupVerification:
    path = Path(backup).expanduser()
    manifest_path = _manifest_path(path)
    if not path.is_file() or not manifest_path.is_file():
        return BackupVerification(
            False, str(path), str(manifest_path), None, None, "Backup or manifest is missing."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return BackupVerification(
            False, str(path), str(manifest_path), None, None, f"Invalid manifest: {error}"
        )
    digest = _sha256(path)
    if manifest.get("schema_version") != RECOVERY_SCHEMA_VERSION:
        return BackupVerification(
            False, str(path), str(manifest_path), digest, None, "Unsupported recovery manifest."
        )
    if not hmac_digest_matches(str(manifest.get("sha256") or ""), digest):
        return BackupVerification(
            False,
            str(path),
            str(manifest_path),
            digest,
            manifest.get("memory_schema_version"),
            "Backup checksum does not match manifest.",
        )
    try:
        _run(
            [_executable(pg_restore), "--list", str(path)],
            timeout_seconds=timeout_seconds,
        )
    except RuntimeError as error:
        return BackupVerification(
            False,
            str(path),
            str(manifest_path),
            digest,
            manifest.get("memory_schema_version"),
            str(error),
        )
    return BackupVerification(
        True,
        str(path),
        str(manifest_path),
        digest,
        manifest.get("memory_schema_version"),
    )


def restore_postgres_backup(
    backup: str | Path,
    dsn: str,
    *,
    pg_restore: str = "pg_restore",
    clean: bool = False,
    allow_destructive: bool = False,
    timeout_seconds: int = DEFAULT_RTO_SECONDS,
) -> None:
    verification = verify_postgres_backup(backup, pg_restore=pg_restore)
    if not verification.ok:
        raise RuntimeError(verification.issue or "Backup verification failed.")
    if clean and not allow_destructive:
        raise RuntimeError("A clean restore requires explicit destructive authorization.")
    command = [_executable(pg_restore), "--no-owner", "--no-privileges"]
    if clean:
        command.extend(["--clean", "--if-exists"])
    command.append(str(Path(backup).expanduser()))
    _run(command, dsn=dsn, timeout_seconds=timeout_seconds)


def hmac_digest_matches(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def _run(
    command: list[str],
    *,
    dsn: str | None = None,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
        if (value := os.environ.get(key)) is not None
    }
    if dsn is not None:
        environment["PGDATABASE"] = dsn
    try:
        # Executable is resolved by _executable, arguments are a list, and no shell is used.
        return subprocess.run(  # nosec B603
            command,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as error:
        stderr = getattr(error, "stderr", None)
        suffix = f": {stderr.strip()}" if isinstance(stderr, str) and stderr.strip() else ""
        raise RuntimeError(f"Recovery command failed ({Path(command[0]).name}){suffix}") from error


def _executable(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise RuntimeError(f"Required recovery executable is not available: {name}")
    return resolved


def _manifest_path(backup: Path) -> Path:
    return backup.with_suffix(backup.suffix + ".manifest.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()
