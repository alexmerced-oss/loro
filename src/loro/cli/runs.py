"""`loro run list | export | verify`: per-run evidence commands.

`loro run PROMPT` stays the way to run a task. `RunCommand` routes a first argument of exactly
`list`, `export` or `verify` to these subcommands; `loro run -- export` runs a task whose prompt
is literally "export".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table
from typer.core import TyperCommand

from loro.config import load_config
from loro.evidence import EvidenceError, export_run, list_runs, verify_bundle

RUN_SUBCOMMANDS = frozenset({"list", "export", "verify"})

console = Console()
error_console = Console(stderr=True)

run_app = typer.Typer(
    help=(
        "Inspect agent runs and export tamper-evident evidence bundles.\n\n"
        "A run id is the audit trace id of one `loro run`, `loro plan`, REPL or Web UI turn. "
        "`loro run` prints it after each task; `loro run list` shows recent ones."
    ),
    no_args_is_help=True,
    add_completion=False,
)


class RunCommand(TyperCommand):
    """`loro run PROMPT`, plus `loro run list|export|verify` evidence subcommands."""

    # Anything that is not a subcommand name is a task prompt (documentation checks use this).
    accepts_prompt = True

    @property
    def commands(self) -> dict[str, Any]:
        """The evidence subcommands, for help, documentation checks and introspection."""

        group = typer.main.get_command(run_app)
        return dict(getattr(group, "commands", {}))

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: Any = None,
        **extra: Any,
    ) -> Any:
        # Typer vendors its own click, so the context types are left as Any here.
        if args and args[0] in RUN_SUBCOMMANDS:
            group = typer.main.get_command(run_app)
            return group.make_context(info_name, list(args), parent=parent, **extra)
        return super().make_context(info_name, args, parent=parent, **extra)


def _count(value: int, noun: str) -> str:
    return f"{value} {noun}{'' if value == 1 else 's'}"


def _fail(message: str, *, hint: str | None = None, code: int = 2) -> typer.Exit:
    error_console.print(f"[bold red]Error:[/bold red] {message}", highlight=False)
    if hint:
        error_console.print(hint, highlight=False)
    return typer.Exit(code=code)


@run_app.command("list")
def run_list(
    limit: Annotated[int, typer.Option("--limit", min=1, max=500, help="Runs to show.")] = 20,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List recent runs recorded in the local audit log, newest first.

    Example: loro run list --limit 5
    """

    try:
        runs = list_runs(load_config(), limit=limit)
    except EvidenceError as error:
        raise _fail(str(error)) from error
    if json_output:
        typer.echo(json.dumps([run.to_payload() for run in runs], indent=2))
        return
    if not runs:
        console.print('No runs in the audit log yet. Run a task with `loro run "..."`.')
        return
    table = Table(box=None, pad_edge=False, header_style="bold")
    for column in ("run id", "started", "mode", "model", "stop", "session"):
        table.add_column(column, overflow="fold")
    for run in runs:
        table.add_row(
            run.run_id,
            (run.started_at or "")[:19].replace("T", " "),
            run.mode or "",
            "/".join(item for item in (run.provider, run.model) if item),
            run.stop_reason or "running",
            (run.session_id or "")[:8],
        )
    console.print(table)
    console.print("Export one with: loro run export RUN_ID --out run.zip", style="dim")


@run_app.command("export")
def run_export(
    run_id: Annotated[str, typer.Argument(help="Run id from `loro run list`.")],
    out: Annotated[
        Path, typer.Option("--out", "-o", help="Bundle path to write, e.g. run.zip.")
    ] = Path("run.zip"),
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Export one run's evidence bundle: audit slice, receipts, digests, tool calls, usage.

    Example: loro run export 3f2b9c1e-... --out run.zip

    Tool results are redacted by the audit data-protection surface. Print the bundle digest
    somewhere independent (a ticket, a signed message) so `loro run verify --expect-digest`
    can detect a bundle rewritten as a whole.
    """

    try:
        result = export_run(load_config(), run_id, out, project_root=Path.cwd(), overwrite=force)
    except EvidenceError as error:
        raise _fail(str(error)) from error
    if json_output:
        typer.echo(json.dumps(result.to_payload(), indent=2))
        return
    console.print(f"Wrote {result.path}", highlight=False, soft_wrap=True)
    console.print(
        f"Run {result.run_id}: {_count(result.audit_events, 'audit event')}, "
        f"{_count(result.tool_calls, 'tool call')}, {_count(result.approvals, 'approval')}.",
        highlight=False,
        soft_wrap=True,
    )
    console.print(f"Bundle digest: {result.digest}", highlight=False, soft_wrap=True)
    for warning in result.warnings:
        error_console.print(f"[yellow]Warning:[/yellow] {warning}", highlight=False)
    console.print(
        f"Verify with: loro run verify {result.path} --expect-digest {result.digest}",
        style="dim",
        highlight=False,
        soft_wrap=True,
    )


@run_app.command("verify")
def run_verify(
    bundle: Annotated[Path, typer.Argument(help="Bundle written by `loro run export`.")],
    expect_digest: Annotated[
        str | None,
        typer.Option("--expect-digest", help="Bundle digest recorded at export time."),
    ] = None,
    audit_log: Annotated[
        Path | None,
        typer.Option("--audit-log", help="Also check the chain segment against this audit log."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Verify a run evidence bundle; exits 1 if anything was altered.

    Example: loro run verify run.zip --expect-digest sha256:...
    """

    try:
        result = verify_bundle(bundle, expected_digest=expect_digest, audit_log=audit_log)
    except EvidenceError as error:
        raise _fail(str(error)) from error
    if json_output:
        typer.echo(json.dumps(result.to_payload(), indent=2))
    elif result.ok:
        console.print(
            f"[green]Verified[/green] run {result.run_id}: {len(result.checks)} checks passed.",
            highlight=False,
        )
        console.print(f"Bundle digest: {result.digest}", highlight=False, soft_wrap=True)
        if expect_digest is None and audit_log is None:
            console.print(
                "Integrity is self-consistent. Pass --expect-digest or --audit-log to anchor it "
                "to an independent record.",
                style="dim",
            )
    else:
        error_console.print(f"[bold red]Verification failed[/bold red] for {bundle}:")
        for issue in result.issues:
            error_console.print(f"  - {issue}", highlight=False)
    if not result.ok:
        raise typer.Exit(code=1)
