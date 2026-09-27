"""`loro config`: show and lint resolved configuration."""

from __future__ import annotations

import typer

from loro.cli._common import console
from loro.cli.ops import (
    config_check as ops_config_check,
)
from loro.config import (
    load_config,
)
from loro.identity import (
    diagnose_identity,
)

config_app = typer.Typer(help="Show and lint resolved configuration.")


config_app.command("check")(ops_config_check)


@config_app.callback(invoke_without_command=True)
def config_root(ctx: typer.Context) -> None:
    """Show resolved configuration, or run a config subcommand."""
    if ctx.invoked_subcommand is None:
        console.print_json(load_config().model_dump_json(indent=2))


@config_app.command("show")
def show_config() -> None:
    """Show resolved configuration."""
    console.print_json(load_config().model_dump_json(indent=2))


@config_app.command("summary")
def config_summary() -> None:
    """Print a human-readable summary of the resolved configuration."""
    config = load_config()
    identity_diagnostic = diagnose_identity(config.identity)
    console.print("[bold green]Loro configuration[/bold green]")
    console.print(f"Model provider: {config.model.provider}")
    console.print(f"Model: {config.model.model}")
    if config.model.small_model:
        console.print(f"Small model: {config.model.small_model}")
    if config.model.api_key_env:
        console.print(f"API key env var: {config.model.api_key_env}")
    if config.model.credential_ref:
        console.print(f"Credential vault ref: {config.model.credential_ref}")
    if config.model.base_url:
        console.print(f"Base URL: {config.model.base_url}")
    console.print(f"Default agent profile: {config.agent_profiles.default_profile or 'none'}")
    console.print(f"Default permission: {config.permissions.default}")
    console.print(f"Policy version: {config.permissions.version}")
    console.print(f"Permission rules: {len(config.permissions.rules)}")
    console.print(
        "Workspace roots: "
        + (", ".join(config.permissions.workspace_roots) or "unrestricted local mode")
    )
    console.print(f"Local memory: {'enabled' if config.memory.local.enabled else 'disabled'}")
    console.print(f"Shared memory: {'enabled' if config.memory.shared.enabled else 'disabled'}")
    console.print(f"Shared memory tenant isolation: {config.memory.shared.tenant_isolation}")
    console.print(f"Polaris: {'enabled' if config.polaris.enabled else 'disabled'}")
    console.print(
        f"MCP: {'enabled' if config.mcp.enabled else 'disabled'} "
        f"({len(config.mcp.servers)} configured servers)"
    )
    console.print(f"Audit log: {'enabled' if config.audit.enabled else 'disabled'}")
    console.print(f"Audit schema: {config.audit.schema_version}")
    console.print(f"Audit sink: {config.audit.sink} ({config.audit.failure_mode})")
    console.print(f"Session path: {config.sessions.path}")
    console.print(
        f"Agentic Graphs: {'enabled' if config.agraph.enabled else 'disabled'} "
        f"(AGS conformance level {config.agraph.conformance_level})"
    )
    from loro.agraph import SUPPORTED_FEATURES

    console.print("AGS supported features: " + ", ".join(SUPPORTED_FEATURES))
    console.print(f"Safety scanner: {'enabled' if config.safety.enabled else 'disabled'}")
    console.print(
        f"Identity: {'ready' if identity_diagnostic.ok else 'missing required fields'} "
        f"({identity_diagnostic.context.subject}, {identity_diagnostic.context.source})"
    )
    console.print(
        "Approvals: "
        f"interactive={'enabled' if config.approvals.interactive else 'disabled'}, "
        "non-interactive="
        f"{'allowed' if config.approvals.allow_non_interactive else 'denied'}"
    )
    console.print(f"Approval store: {config.approvals.store}")
    if not identity_diagnostic.ok:
        console.print(f"Missing identity fields: {', '.join(identity_diagnostic.missing_fields)}")
        raise typer.Exit(code=1)
