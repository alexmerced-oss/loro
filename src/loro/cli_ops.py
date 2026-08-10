"""Operator-facing commands: aggregate health, compliance queries, retention, linting.

These live outside `cli.py` so each domain stays reviewable on its own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from loro.audit import AuditLogger
from loro.audit.query import AuditQuery, audit_report, query_audit_events
from loro.config import LoroConfig, load_config
from loro.config_check import check_config
from loro.identity import diagnose_identity
from loro.mcp.registry import diagnose_mcp
from loro.memory.operations import check_shared_memory_backend, sweep_shared_memories
from loro.models import smoke_model_client
from loro.sandbox import SandboxRunner

console = Console()

approvals_app = typer.Typer(help="Review identity-bound approval decisions.")


def _parse_time(value: str | None, flag: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise typer.BadParameter(f"{flag} must be an ISO-8601 timestamp: {value}") from error


# ---------------------------------------------------------------- loro doctor


def _doctor_checks(config: LoroConfig) -> list[dict[str, Any]]:
    """Run every subsystem health check and normalize the results."""

    checks: list[dict[str, Any]] = []

    identity = diagnose_identity(config.identity)
    checks.append(
        {
            "check": "identity",
            "ok": identity.ok,
            "detail": (
                f"{identity.context.subject} @ {identity.context.tenant} "
                f"(source: {identity.context.source})"
                if identity.ok
                else "missing required identity fields: " + ", ".join(identity.missing_fields)
            ),
        }
    )

    try:
        provider = smoke_model_client(config.model, execute=False)
        checks.append(
            {
                "check": "provider",
                "ok": True,
                "detail": f"{provider['provider']} / {provider['model']} request builds cleanly",
            }
        )
    except Exception as error:  # noqa: BLE001 - report, never abort the whole doctor run
        checks.append({"check": "provider", "ok": False, "detail": str(error)})

    sandbox = SandboxRunner(config.sandbox).diagnose()
    profiles = sandbox.get("profiles", {})
    unready = sorted(name for name, item in profiles.items() if not item.get("ready", False))
    checks.append(
        {
            "check": "sandbox",
            "ok": bool(sandbox.get("enabled")) and not unready,
            "detail": (
                f"{len(profiles)} profile(s) ready"
                if not unready
                else "profiles not ready: " + ", ".join(unready)
            ),
        }
    )

    audit = AuditLogger(config.audit, safety_config=config.safety).doctor()
    checks.append(
        {
            "check": "audit",
            "ok": bool(audit.get("ok")),
            "detail": "; ".join(audit.get("issues", [])) or f"sink: {audit.get('sink')}",
        }
    )

    if config.memory.shared.enabled:
        backend = check_shared_memory_backend(config.memory.shared)
        checks.append(
            {
                "check": "memory",
                "ok": backend.ok,
                "detail": "; ".join(backend.messages[-2:]) or backend.backend,
            }
        )
    else:
        checks.append({"check": "memory", "ok": True, "detail": "shared memory disabled"})

    if config.mcp.enabled:
        mcp = diagnose_mcp(config.mcp, None)
        checks.append(
            {
                "check": "mcp",
                "ok": bool(mcp.get("ok")),
                "detail": "; ".join(str(item) for item in mcp.get("issues", []))
                or f"{len(config.mcp.servers)} server(s) configured",
            }
        )
    else:
        checks.append({"check": "mcp", "ok": True, "detail": "MCP disabled"})

    lint = check_config(config)
    errors = [finding for finding in lint if finding.severity == "error"]
    checks.append(
        {
            "check": "config",
            "ok": not errors,
            "detail": (
                f"{len(lint)} lint finding(s); run `loro config check` for detail"
                if lint
                else "no lint findings"
            ),
        }
    )
    return checks


def doctor(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the consolidated result as JSON.")
    ] = False,
) -> None:
    """Run every subsystem health check and print one consolidated pass/fail table."""

    config = load_config()
    checks = _doctor_checks(config)
    ok = all(item["ok"] for item in checks)
    if json_output:
        console.print_json(data={"ok": ok, "checks": checks})
    else:
        table = Table(title="loro doctor")
        table.add_column("check")
        table.add_column("status")
        table.add_column("detail", overflow="fold")
        for item in checks:
            table.add_row(
                str(item["check"]),
                "[green]pass[/green]" if item["ok"] else "[red]fail[/red]",
                str(item["detail"]),
            )
        console.print(table)
    raise typer.Exit(code=0 if ok else 1)


# ----------------------------------------------------------- loro config check


def config_check(
    json_output: Annotated[bool, typer.Option("--json", help="Emit findings as JSON.")] = False,
    strict: Annotated[
        bool, typer.Option("--strict", help="Exit non-zero on warnings as well as errors.")
    ] = False,
) -> None:
    """Report risky-but-valid configuration that schema validation cannot catch."""

    findings = check_config(load_config())
    if json_output:
        console.print_json(data={"findings": [finding.to_payload() for finding in findings]})
    elif not findings:
        console.print("No configuration findings.")
    else:
        table = Table(title="loro config check")
        table.add_column("code")
        table.add_column("severity")
        table.add_column("pointer", overflow="fold")
        table.add_column("message", overflow="fold")
        for finding in findings:
            table.add_row(finding.code, finding.severity, finding.pointer, finding.message)
        console.print(table)
    blocking = {"error"} | ({"warning"} if strict else set())
    raise typer.Exit(code=1 if any(item.severity in blocking for item in findings) else 0)


# ------------------------------------------------- loro audit query / report


def audit_query(
    actor: Annotated[str | None, typer.Option("--actor", help="Exact actor subject.")] = None,
    tenant: Annotated[str | None, typer.Option("--tenant", help="Exact tenant id.")] = None,
    event_type: Annotated[
        str | None, typer.Option("--event-type", help="Event type glob, e.g. 'approval.*'.")
    ] = None,
    action: Annotated[
        str | None, typer.Option("--action", help="Action glob, e.g. 'shell.*'.")
    ] = None,
    since: Annotated[
        str | None, typer.Option("--since", help="ISO-8601 lower bound (inclusive).")
    ] = None,
    until: Annotated[
        str | None, typer.Option("--until", help="ISO-8601 upper bound (inclusive).")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum events to return.")] = 50,
) -> None:
    """Query the audit chain by actor, tenant, event type, action, and time range."""

    config = load_config()
    events = query_audit_events(
        config.audit.path,
        AuditQuery(
            actor=actor,
            tenant=tenant,
            event_type=event_type,
            action=action,
            since=_parse_time(since, "--since"),
            until=_parse_time(until, "--until"),
            limit=limit,
        ),
    )
    console.print_json(data={"count": len(events), "events": events})


def audit_report_command(
    actor: Annotated[str | None, typer.Option("--actor", help="Exact actor subject.")] = None,
    tenant: Annotated[str | None, typer.Option("--tenant", help="Exact tenant id.")] = None,
    event_type: Annotated[str | None, typer.Option("--event-type", help="Event type glob.")] = None,
    action: Annotated[str | None, typer.Option("--action", help="Action glob.")] = None,
    since: Annotated[str | None, typer.Option("--since", help="ISO-8601 lower bound.")] = None,
    until: Annotated[str | None, typer.Option("--until", help="ISO-8601 upper bound.")] = None,
) -> None:
    """Verify the audit chain and summarize matching events as compliance evidence."""

    config = load_config()
    report = audit_report(
        config.audit.path,
        AuditQuery(
            actor=actor,
            tenant=tenant,
            event_type=event_type,
            action=action,
            since=_parse_time(since, "--since"),
            until=_parse_time(until, "--until"),
            limit=0,
        ),
    )
    console.print_json(data=report.to_payload())
    raise typer.Exit(code=0 if report.chain_ok else 1)


@approvals_app.command("list")
def approvals_list(
    actor: Annotated[str | None, typer.Option("--actor", help="Exact actor subject.")] = None,
    tenant: Annotated[str | None, typer.Option("--tenant", help="Exact tenant id.")] = None,
    action: Annotated[str | None, typer.Option("--action", help="Action glob.")] = None,
    since: Annotated[str | None, typer.Option("--since", help="ISO-8601 lower bound.")] = None,
    until: Annotated[str | None, typer.Option("--until", help="ISO-8601 upper bound.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum records to return.")] = 50,
    json_output: Annotated[bool, typer.Option("--json", help="Emit raw events as JSON.")] = False,
) -> None:
    """List approval decisions recorded in the tamper-evident audit chain."""

    config = load_config()
    events = query_audit_events(
        config.audit.path,
        AuditQuery(
            actor=actor,
            tenant=tenant,
            event_type="approval.*",
            action=action,
            since=_parse_time(since, "--since"),
            until=_parse_time(until, "--until"),
            limit=limit,
        ),
    )
    if json_output:
        console.print_json(data={"count": len(events), "approvals": events})
        return
    if not events:
        console.print("No approval records matched.")
        return
    table = Table(title="approvals")
    table.add_column("timestamp")
    table.add_column("event")
    table.add_column("actor")
    table.add_column("tenant")
    table.add_column("action", overflow="fold")
    for event in events:
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        table.add_row(
            str(event.get("timestamp", "")),
            str(event.get("event_type", "")),
            str(event.get("actor") or details.get("actor") or ""),
            str(event.get("tenant_id") or details.get("tenant_id") or ""),
            str(event.get("action") or details.get("action") or ""),
        )
    console.print(table)


# ------------------------------------------------------------ loro memory sweep


def memory_sweep(
    tenant: Annotated[
        str | None, typer.Option("--tenant", help="Tenant to sweep; defaults to the identity.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum memories to consider.")] = 100,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Actually retire expired memories instead of reporting."),
    ] = False,
    reason: Annotated[
        str, typer.Option("--reason", help="Reason recorded on each lifecycle event.")
    ] = "retention: expires_at elapsed",
) -> None:
    """Retire shared memories whose retention window has elapsed."""

    config = load_config()
    if not config.memory.shared.enabled:
        raise typer.BadParameter("Shared memory is disabled.")
    resolved_tenant = tenant or config.identity.tenant or "default"
    try:
        result = sweep_shared_memories(
            config,
            tenant_id=resolved_tenant,
            reason=reason,
            limit=limit,
            execute=apply,
        )
    except (PermissionError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(
        data={
            "backend": result.backend,
            "tenant_id": result.tenant_id,
            "applied": apply,
            "scanned": result.scanned,
            "swept": result.swept,
            "held": result.held,
            "messages": result.messages,
            "entries": [entry.__dict__ for entry in result.entries],
        }
    )
    if any(entry.error for entry in result.entries):
        raise typer.Exit(code=1)
