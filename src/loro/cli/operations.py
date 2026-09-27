"""`loro operations`: data protection, backup, recovery, and readiness."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer

from loro.benchmarks import run_reference_benchmarks, write_benchmark_report
from loro.cli._common import _audit, console
from loro.config import (
    load_config,
)
from loro.recovery import (
    DEFAULT_RPO_SECONDS,
    DEFAULT_RTO_SECONDS,
    create_postgres_backup,
    restore_postgres_backup,
    verify_postgres_backup,
)
from loro.release_readiness import assess_release_readiness

operations_app = typer.Typer(help="Run data protection, backup, and recovery operations.")


@operations_app.command("recovery-targets")
def operations_recovery_targets() -> None:
    """Show the declared reference-deployment recovery objectives."""
    console.print_json(
        data={
            "rpo_seconds": DEFAULT_RPO_SECONDS,
            "rto_seconds": DEFAULT_RTO_SECONDS,
            "scope": "Postgres shared-memory state and lifecycle events",
        }
    )


@operations_app.command("benchmark")
def operations_benchmark(
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Content-free JSON evidence output path.")
    ] = Path("loro-benchmark.json"),
    iterations: Annotated[
        int, typer.Option("--iterations", min=1, help="Measured iterations per scenario.")
    ] = 25,
    warmup: Annotated[
        int, typer.Option("--warmup", min=0, help="Unmeasured warmup iterations.")
    ] = 3,
    strict: Annotated[
        bool, typer.Option("--strict", help="Exit non-zero when a candidate target is missed.")
    ] = False,
) -> None:
    """Record reproducible local release-candidate performance baselines."""
    report = run_reference_benchmarks(iterations=iterations, warmup=warmup)
    destination = write_benchmark_report(report, output)
    console.print_json(data=report.to_payload())
    console.print(f"Wrote benchmark evidence: {destination}")
    if strict and not report.passed:
        raise typer.Exit(code=1)


@operations_app.command("release-readiness")
def operations_release_readiness(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the content-free JSON report to this path."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit non-zero on warnings as well as failed checks."),
    ] = False,
) -> None:
    """Evaluate this installation against the frozen stabilization contract."""
    report = assess_release_readiness(load_config())
    payload = report.to_payload()
    console.print_json(data=payload)
    if output is not None:
        destination = output.expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        console.print(f"Wrote readiness evidence: {destination}")
    blocked = not report.ready or (
        strict and any(check.status == "warn" for check in report.checks)
    )
    if blocked:
        raise typer.Exit(code=1)


@operations_app.command("backup")
def operations_backup(
    output: Annotated[Path, typer.Option("--output", "-o", help="Backup output path")],
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Run pg_dump. Without this flag, show the plan."),
    ] = False,
) -> None:
    """Create a checksummed Postgres shared-memory backup and manifest."""
    config = load_config()
    if config.memory.shared.backend != "postgres":
        raise typer.BadParameter("Reference backup currently supports Postgres memory only.")
    if not execute:
        console.print_json(
            data={
                "execute": False,
                "backend": "postgres",
                "schema": config.memory.shared.postgres_schema,
                "output": str(output.expanduser()),
                "rpo_seconds": DEFAULT_RPO_SECONDS,
                "rto_seconds": DEFAULT_RTO_SECONDS,
            }
        )
        return
    try:
        backup = create_postgres_backup(config.memory.shared, output)
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("memory.backup_created", backend="postgres", target=str(backup))
    console.print(f"Created backup and manifest: {backup}")


@operations_app.command("verify-backup")
def operations_verify_backup(
    backup: Annotated[Path, typer.Argument(help="Postgres custom-format backup path.")],
) -> None:
    """Verify a backup checksum, manifest, and pg_restore catalog."""
    result = verify_postgres_backup(backup)
    console.print_json(data=result.__dict__)
    raise typer.Exit(code=0 if result.ok else 1)


@operations_app.command("restore")
def operations_restore(
    backup: Annotated[Path, typer.Argument(help="Postgres custom-format backup path.")],
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Run pg_restore against the configured database."),
    ] = False,
    clean: Annotated[
        bool,
        typer.Option("--clean", help="Remove conflicting target objects before restore."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Authorize an executed restore and destructive clean."),
    ] = False,
) -> None:
    """Restore a verified shared-memory backup with explicit authorization."""
    config = load_config()
    if config.memory.shared.backend != "postgres":
        raise typer.BadParameter("Reference restore currently supports Postgres memory only.")
    verification = verify_postgres_backup(backup)
    if not verification.ok:
        raise typer.BadParameter(verification.issue or "Backup verification failed.")
    if not execute:
        console.print_json(
            data={
                "execute": False,
                "verified": True,
                "backup": str(backup.expanduser()),
                "clean": clean,
                "target_env": config.memory.shared.postgres_dsn_env,
            }
        )
        return
    if not yes:
        raise typer.BadParameter("Executed restore requires --yes explicit authorization.")
    dsn = os.environ.get(config.memory.shared.postgres_dsn_env)
    if not dsn:
        raise typer.BadParameter(f"Missing DSN env var: {config.memory.shared.postgres_dsn_env}")
    try:
        restore_postgres_backup(
            backup,
            dsn,
            clean=clean,
            allow_destructive=yes,
        )
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "memory.backup_restored",
        backend="postgres",
        target=config.memory.shared.postgres_schema,
        clean=clean,
    )
    console.print("Restore completed. Run `loro memory reconcile` before returning to service.")
