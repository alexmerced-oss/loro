"""`loro audit`: audit delivery, verification, metrics, and collection."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from loro.audit import verify_jsonl_audit
from loro.audit.collector import (
    AuditCollector,
    AuditCollectorError,
    serve_audit_collector,
    token_from_environment,
)
from loro.audit.metrics import OperationalMetrics
from loro.cli._common import _audit, console
from loro.cli.ops import (
    audit_query as ops_audit_query,
)
from loro.cli.ops import (
    audit_report_command as ops_audit_report,
)
from loro.config import (
    load_config,
)

audit_app = typer.Typer(help="Inspect and flush audit delivery.")


audit_app.command("query")(ops_audit_query)
audit_app.command("report")(ops_audit_report)


@audit_app.command("doctor")
def audit_doctor() -> None:
    """Validate audit schema, sink configuration, credentials, and buffer state."""
    diagnostic = _audit().doctor()
    console.print_json(data=diagnostic)
    raise typer.Exit(code=0 if diagnostic["ok"] else 1)


@audit_app.command("flush")
def audit_flush() -> None:
    """Retry delivery of events retained in the external-sink buffer."""
    try:
        result = _audit().flush()
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(
        data={
            "attempted": result.attempted,
            "delivered": result.delivered,
            "remaining": result.remaining,
        }
    )
    raise typer.Exit(code=0 if result.remaining == 0 else 1)


@audit_app.command("verify")
def audit_verify(
    anchor: Annotated[
        str | None,
        typer.Option(
            "--anchor",
            help="Expected externally stored final SHA-256 event hash.",
        ),
    ] = None,
) -> None:
    """Verify the local JSONL audit hash chain and optional external anchor."""
    config = load_config().audit
    if config.sink != "jsonl":
        raise typer.BadParameter("Local hash verification requires the JSONL audit sink.")
    result = verify_jsonl_audit(config.path, expected_final_hash=anchor)
    console.print_json(data=result.__dict__)
    raise typer.Exit(code=0 if result.ok else 1)


@audit_app.command("metrics")
def audit_metrics() -> None:
    """Render content-free operational metrics in Prometheus text format."""
    config = load_config().audit
    if not config.metrics_enabled:
        raise typer.BadParameter("Operational metrics are disabled in audit configuration.")
    try:
        console.print(OperationalMetrics(config.metrics_path).prometheus(), end="")
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error


@audit_app.command("collect")
def audit_collect(
    path: Annotated[
        Path,
        typer.Option("--path", help="SQLite collector database path."),
    ] = Path("~/.local/state/loro/audit-collector.sqlite3"),
    token_env: Annotated[
        str,
        typer.Option("--token-env", help="Environment variable containing the bearer token."),
        # This is an environment variable name, not a credential.
    ] = "LORO_AUDIT_COLLECTOR_TOKEN",  # nosec B107
    host: Annotated[str, typer.Option("--host", help="Collector bind host.")] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Collector bind port."),
    ] = 8788,
    max_body_bytes: Annotated[
        int,
        typer.Option("--max-body-bytes", min=1024, help="Maximum accepted request body."),
    ] = 5_000_000,
) -> None:
    """Run the reference authenticated, deduplicating audit collector."""
    try:
        collector = AuditCollector(
            path,
            token_from_environment(token_env),
            max_body_bytes=max_body_bytes,
        )
    except (AuditCollectorError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"Audit collector listening on http://{host}:{port}")
    serve_audit_collector(collector, host=host, port=port)


@audit_app.command("collector-verify")
def audit_collector_verify(
    path: Annotated[
        Path,
        typer.Option("--path", help="SQLite collector database path."),
    ] = Path("~/.local/state/loro/audit-collector.sqlite3"),
) -> None:
    """Verify the reference collector's durable hash chain."""
    expanded = path.expanduser()
    if not expanded.exists():
        raise typer.BadParameter(f"Audit collector database does not exist: {expanded}")
    try:
        result = AuditCollector(expanded, "verification-only").verify()
    except (AuditCollectorError, ValueError, OSError) as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=result.__dict__)
    raise typer.Exit(code=0 if result.ok else 1)
