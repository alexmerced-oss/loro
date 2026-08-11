import asyncio
import json
import os
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import click
import typer
from rich.console import Console
from rich.live import Live
from rich.text import Text

from loro import __version__
from loro.approvals import ApprovalManager, ApprovalRequest, ApprovalScope
from loro.artifacts.briefs import create_brief_artifact
from loro.artifacts.common import ArtifactResult, write_provenance
from loro.artifacts.documents import create_document_artifact
from loro.artifacts.presentations import create_presentation_artifact
from loro.artifacts.spreadsheets import create_spreadsheet_artifact
from loro.audit import AuditLogger, prompt_preview, verify_jsonl_audit
from loro.audit.collector import (
    AuditCollector,
    AuditCollectorError,
    serve_audit_collector,
    token_from_environment,
)
from loro.audit.metrics import OperationalMetrics
from loro.cli_credentials import credentials_app
from loro.cli_gateway import gateway_app, gateway_setup
from loro.cli_graph import graph_app
from loro.cli_ops import (
    approvals_app,
)
from loro.cli_ops import (
    audit_query as ops_audit_query,
)
from loro.cli_ops import (
    audit_report_command as ops_audit_report,
)
from loro.cli_ops import (
    config_check as ops_config_check,
)
from loro.cli_ops import (
    doctor as ops_doctor,
)
from loro.cli_ops import (
    memory_sweep as ops_memory_sweep,
)
from loro.config import (
    LoroConfig,
    MCPCredentialProfileConfig,
    MCPExtensionConfig,
    MCPServerConfig,
    load_config,
    replace_config_section,
    write_config_sections,
)
from loro.data_protection import DataProtectionEngine, DataSurface
from loro.governed_data import explain_access, inspect_table_schema
from loro.identity import (
    IdentityConfigurationError,
    IdentityContext,
    diagnose_identity,
    resolve_identity,
)
from loro.mcp import (
    MCPClientError,
    MCPExtensionError,
    MCPRegistry,
    MCPRegistryError,
    MCPService,
    MCPTaskError,
    diagnose_mcp,
)
from loro.mcp.extensions import TASKS_EXTENSION_ID
from loro.mcp.registry import server_endpoint_for_display
from loro.memory.base import SharedMemoryLifecycleRequest
from loro.memory.drafts import SharedMemoryDraftStore
from loro.memory.iceberg import IcebergSharedMemoryStore
from loro.memory.local import LocalMemoryStore
from loro.memory.migrations import (
    LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
    postgres_memory_migrations,
)
from loro.memory.operations import (
    apply_shared_memory_lifecycle,
    check_shared_memory_backend,
    create_shared_memory_draft,
    render_or_commit_shared_draft,
    search_shared_memories,
)
from loro.memory.postgres import PostgresSharedMemoryStore
from loro.memory.proposals import MemoryProposal, MemoryProposalStore
from loro.memory.schemas import shared_memory_schema
from loro.models import ModelMessage, create_model_client, redact_model_request, smoke_model_client
from loro.permissions import PermissionEngine, PermissionRequest
from loro.polaris import PolarisClient, PolarisResult
from loro.providers import (
    check_provider_config,
    get_provider_profile,
    model_config_from_profile,
    provider_names,
    write_local_model_config,
)
from loro.recovery import (
    DEFAULT_RPO_SECONDS,
    DEFAULT_RTO_SECONDS,
    create_postgres_backup,
    restore_postgres_backup,
    verify_postgres_backup,
)
from loro.resources import (
    NormalizedResource,
    filesystem_resource,
    mcp_resource,
    memory_resource,
    resource_from_payload,
    session_message_resource,
    shell_resource,
)
from loro.runtime import AgentRuntime
from loro.sandbox import SandboxRunner
from loro.serialization import jsonable_mapping
from loro.session_messages import SessionMailbox, message_digest
from loro.sessions import SessionStore
from loro.skill_compat import (
    SkillCompatibilityError,
    apply_mcp_import,
    import_compatible_skills,
    inspect_compatibility,
)
from loro.skills import SkillError, SkillRegistry
from loro.tools.files import FileTools
from loro.tools.shell import ShellTools

app = typer.Typer(
    name="loro",
    help=(
        "Enterprise agent harness for coding, governed data, and productivity work. "
        "Start with `loro configure`, then run `loro plan` or `loro run`."
    ),
    no_args_is_help=True,
    invoke_without_command=True,
)
memory_app = typer.Typer(help="Inspect and write Loro memories.")
docs_app = typer.Typer(help="Create and transform documents.")
slides_app = typer.Typer(help="Create and transform presentations.")
sheets_app = typer.Typer(help="Create and transform spreadsheets.")
brief_app = typer.Typer(help="Create enterprise briefs.")
data_app = typer.Typer(help="Discover governed enterprise data.")
sessions_app = typer.Typer(help="Inspect saved Loro sessions.")
file_app = typer.Typer(help="Read and search local files.")
shell_app = typer.Typer(help="Run permission-gated shell commands.")
safety_app = typer.Typer(help="Scan content for obvious secrets.")
providers_app = typer.Typer(help="Inspect and configure AI providers.")
setup_app = typer.Typer(help="Guided setup wizards for Loro configuration.")
identity_app = typer.Typer(help="Inspect and validate the active enterprise identity.")
policy_app = typer.Typer(help="Explain normalized permission decisions.")
audit_app = typer.Typer(help="Inspect and flush audit delivery.")
mcp_app = typer.Typer(help="Configure and use Model Context Protocol servers.")
skills_app = typer.Typer(help="Discover and govern portable Agent Skills packages.")
sandbox_app = typer.Typer(help="Inspect subprocess isolation profiles.")
config_app = typer.Typer(help="Show and lint resolved configuration.")
operations_app = typer.Typer(help="Run data protection, backup, and recovery operations.")

app.add_typer(memory_app, name="memory")
app.add_typer(docs_app, name="docs")
app.add_typer(slides_app, name="slides")
app.add_typer(sheets_app, name="sheets")
app.add_typer(brief_app, name="brief")
app.add_typer(data_app, name="data")
app.add_typer(sessions_app, name="sessions")
app.add_typer(file_app, name="file")
app.add_typer(shell_app, name="shell")
app.add_typer(safety_app, name="safety")
app.add_typer(providers_app, name="providers")
app.add_typer(setup_app, name="setup")
app.add_typer(identity_app, name="identity")
app.add_typer(policy_app, name="policy")
app.add_typer(audit_app, name="audit")
app.add_typer(mcp_app, name="mcp")
app.add_typer(skills_app, name="skills")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(credentials_app, name="credentials")
app.add_typer(gateway_app, name="gateway")
setup_app.command("gateway")(gateway_setup)
app.add_typer(graph_app, name="graph")
app.add_typer(config_app, name="config")
app.add_typer(approvals_app, name="approvals")
app.add_typer(operations_app, name="operations")
app.command("doctor")(ops_doctor)
config_app.command("check")(ops_config_check)
audit_app.command("query")(ops_audit_query)
audit_app.command("report")(ops_audit_report)
memory_app.command("sweep")(ops_memory_sweep)

console = Console()
DEFAULT_ARTIFACT_DIR = Path("artifacts")


def _runtime() -> AgentRuntime:
    config = load_config()
    try:
        return AgentRuntime(
            config,
            approval_provider=(
                lambda request: _prompt_for_approval(
                    request,
                    allow_session_scope=config.approvals.allow_session_scope,
                )
            )
            if config.approvals.interactive
            else None,
        )
    except IdentityConfigurationError as error:
        raise typer.BadParameter(str(error)) from error


def _identity() -> IdentityContext:
    try:
        return resolve_identity(load_config().identity)
    except IdentityConfigurationError as error:
        raise typer.BadParameter(str(error)) from error


def _audit() -> AuditLogger:
    config = load_config()
    try:
        identity = resolve_identity(config.identity)
    except IdentityConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    return AuditLogger(config.audit, identity, safety_config=config.safety)


def _permissions() -> PermissionEngine:
    return PermissionEngine(load_config().permissions)


def _data_protection() -> DataProtectionEngine:
    return DataProtectionEngine(load_config().safety)


def _shared_memory_tenant(config: LoroConfig, requested: str | None) -> str:
    identity_tenant = _identity().tenant
    resolved = requested or identity_tenant
    if config.memory.shared.tenant_isolation == "identity" and resolved != identity_tenant:
        _audit().write(
            "memory.tenant_denied",
            requested_tenant=resolved,
            identity_tenant=identity_tenant,
            decision="deny",
            policy_source="memory.shared.tenant_isolation",
        )
        raise typer.BadParameter(f"Cross-tenant shared-memory access denied: {resolved}")
    return resolved


def _shared_draft_store(config: LoroConfig) -> SharedMemoryDraftStore:
    authorized_tenant = (
        _identity().tenant if config.memory.shared.tenant_isolation == "identity" else None
    )
    return SharedMemoryDraftStore(
        Path(config.memory.local.path), authorized_tenant_id=authorized_tenant
    )


def _mcp_service() -> MCPService:
    config = load_config()
    return MCPService(
        config.mcp,
        sandbox_config=config.sandbox,
        workspace_roots=config.permissions.workspace_roots,
    )


def _mcp_server_endpoint(server: MCPServerConfig) -> str:
    return server_endpoint_for_display(server)


def _authorize_explicit_mcp_read(
    server_id: str,
    *,
    action: str,
    operation: str,
    name: str = "",
    arguments: dict[str, Any] | None = None,
) -> None:
    config = load_config()
    try:
        server = MCPRegistry(config.mcp).get(server_id)
    except MCPRegistryError as error:
        raise typer.BadParameter(str(error)) from error
    resource = mcp_resource(
        operation=operation,
        server_id=server_id,
        transport=server.transport,
        endpoint=_mcp_server_endpoint(server),
        name=name,
        arguments=arguments,
    )
    try:
        PermissionEngine(config.permissions).require_allowed(
            PermissionRequest(tool="mcp", action=action, target=resource.target, resource=resource),
            approved=True,
        )
    except PermissionError as error:
        raise typer.BadParameter(str(error)) from error


def _authorize_mcp_mutation(
    server_id: str,
    *,
    action: str,
    operation: str,
    name: str,
    arguments: dict[str, Any],
    yes: bool,
    risk_reason: str,
) -> None:
    config = load_config()
    try:
        server = MCPRegistry(config.mcp).get(server_id)
    except MCPRegistryError as error:
        raise typer.BadParameter(str(error)) from error
    resource = mcp_resource(
        operation=operation,
        server_id=server_id,
        transport=server.transport,
        endpoint=_mcp_server_endpoint(server),
        name=name,
        arguments=arguments,
    )
    _authorize_cli_action(
        tool="mcp",
        action=action,
        target=resource.target,
        arguments={"server_id": server_id, "name": name, "arguments": arguments},
        risk_reason=risk_reason,
        non_interactive_approved=yes,
        resource=resource,
    )


def _run_mcp_operation(
    operation: str,
    server_id: str,
    awaitable: Awaitable[dict[str, Any]],
) -> dict[str, Any]:
    audit = _audit()
    audit.write("mcp.request_started", action=operation, target=server_id, server_id=server_id)
    try:
        result = asyncio.run(awaitable)
    except (MCPClientError, MCPExtensionError, MCPTaskError, ValueError) as error:
        audit.write(
            "mcp.request_failed",
            action=operation,
            target=server_id,
            server_id=server_id,
            result_status="failed",
            error_type=type(error).__name__,
        )
        raise typer.BadParameter(str(error)) from error
    connection = result.get("connection", result)
    audit.write(
        "mcp.request_completed",
        action=operation,
        target=server_id,
        server_id=server_id,
        transport=connection.get("transport"),
        protocol_version=connection.get("protocol_version"),
        lifecycle=connection.get("lifecycle"),
        result_status="ok",
    )
    return result


def _json_object(value: str, *, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise typer.BadParameter(f"Invalid {label} JSON: {error.msg}") from error
    if not isinstance(parsed, dict):
        raise typer.BadParameter(f"{label} must be a JSON object.")
    return parsed


def _approval_manager(config: LoroConfig | None = None) -> ApprovalManager:
    resolved_config = config or load_config()
    try:
        identity = resolve_identity(resolved_config.identity)
    except IdentityConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    audit = AuditLogger(resolved_config.audit, identity, safety_config=resolved_config.safety)
    return ApprovalManager(
        resolved_config.approvals,
        identity,
        event_handler=lambda event_type, payload: audit.write(event_type, **dict(payload)),
    )


def _prompt_for_approval(
    request: ApprovalRequest,
    *,
    allow_session_scope: bool,
) -> ApprovalScope | None:
    console.print("[bold yellow]Approval required[/bold yellow]")
    console.print(f"Action: {request.action}")
    console.print(f"Target: {request.target}")
    console.print(f"Arguments: {request.display_arguments()}")
    console.print(f"Policy: {request.policy_decision} ({request.policy_reason})")
    console.print(f"Policy source: {request.policy_source} @ {request.policy_version}")
    console.print(f"Risk: {request.risk_reason}")
    console.print(
        f"Identity: {request.identity_subject} / {request.identity_tenant} "
        f"(session {request.identity_session_id})"
    )
    choices = "once/session/deny" if allow_session_scope else "once/deny"
    choice = typer.prompt(f"Approval ({choices})", default="deny").strip().casefold()
    if choice == "once":
        return "once"
    if choice == "session" and allow_session_scope:
        return "session"
    return None


def _authorize_cli_action(
    *,
    tool: str,
    action: str,
    target: str,
    arguments: dict[str, Any],
    risk_reason: str,
    non_interactive_approved: bool = False,
    resource: NormalizedResource | None = None,
) -> None:
    config = load_config()
    permission_request = PermissionRequest(
        tool=tool,
        action=action,
        target=target,
        resource=resource,
    )
    result = PermissionEngine(config.permissions).evaluate(permission_request)
    if result.decision == "deny":
        raise typer.BadParameter(f"{tool} is denied by policy: {action}")
    if result.decision == "allow":
        return
    manager = _approval_manager(config)
    request = manager.request(
        action=f"{tool}.{action}",
        target=target,
        arguments=arguments,
        policy_decision=result.decision,
        policy_version=result.policy_version,
        policy_source=result.policy_source,
        policy_reason=result.reason,
        risk_reason=risk_reason,
    )
    if non_interactive_approved:
        try:
            record = manager.grant(request, scope="once", method="non_interactive")
            manager.consume(request, record.approval_id)
        except PermissionError as error:
            manager.deny(request)
            raise typer.BadParameter(str(error)) from error
        return
    if not config.approvals.interactive:
        manager.deny(request)
        raise typer.BadParameter(f"{tool} requires trusted user approval.")
    try:
        scope = _prompt_for_approval(
            request,
            allow_session_scope=config.approvals.allow_session_scope,
        )
    except click.Abort:
        manager.deny(request)
        raise
    if scope is None:
        manager.deny(request)
        raise typer.Abort()
    record = manager.grant(request, scope=scope, method="interactive")
    manager.consume(request, record.approval_id)


def _enforce_safe_content(content: str, context: str, allow_sensitive: bool = False) -> None:
    surface = _surface_for_context(context)
    decision = _data_protection().evaluate(content, surface, allow_sensitive=allow_sensitive)
    findings = list(decision.findings)
    if not findings:
        return
    _audit().write(
        "safety.findings_detected",
        context=context,
        finding_kinds=sorted({finding.kind for finding in findings}),
    )
    if decision.blocked:
        kinds = ", ".join(sorted({finding.kind for finding in findings}))
        raise typer.BadParameter(
            f"Sensitive content detected ({kinds}). Re-run with --allow-sensitive "
            "only if policy allows storing this content."
        )


def _surface_for_context(context: str) -> DataSurface:
    if context.startswith("memory.shared"):
        return "memory_shared"
    if context.startswith("memory."):
        return "memory_local"
    if context.startswith("session"):
        return "session_message"
    return "artifact"


def _print_artifact_result(result: ArtifactResult, prompt: str) -> None:
    provenance_path = write_provenance(result=result, prompt_preview=prompt_preview(prompt))
    _audit().write(
        "artifact.created",
        kind=result.kind,
        title=result.title,
        paths=[str(path) for path in result.paths],
        provenance_path=str(provenance_path),
        prompt_preview=prompt_preview(prompt),
    )
    console.print(result.summary)
    console.print(f"Provenance: {provenance_path}")


def _create_and_print_artifact(
    *,
    prompt: str,
    output_dir: Path,
    allow_sensitive: bool,
    context: str,
    factory: Callable[[str, Path], ArtifactResult],
) -> None:
    _enforce_safe_content(prompt, context=context, allow_sensitive=allow_sensitive)
    result = factory(prompt, output_dir)
    _print_artifact_result(result, prompt)


def _create_and_print_brief(
    *,
    prompt: str,
    output_dir: Path,
    allow_sensitive: bool,
    brief_type: str,
) -> None:
    _create_and_print_artifact(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        context="artifact.brief",
        factory=lambda artifact_prompt, artifact_dir: create_brief_artifact(
            artifact_prompt,
            artifact_dir,
            brief_type=brief_type,
        ),
    )


def _run_polaris_result(result: PolarisResult) -> None:
    _audit().write(
        "polaris.readonly_executed",
        command=result.command,
        returncode=result.returncode,
        sandbox_profile=result.sandbox_profile,
        sandbox_os_enforced=result.sandbox_os_enforced,
        output_truncated=result.output_truncated,
    )
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)
    raise typer.Exit(code=result.returncode)


def _polaris_client() -> PolarisClient:
    config = load_config()
    if not config.polaris.enabled:
        console.print(
            "Polaris is disabled. Enable [polaris] before using this command.",
            markup=False,
        )
        raise typer.Exit(code=2)
    context = click.get_current_context(silent=True)
    action = context.info_name if context is not None and context.info_name else "discovery"
    arguments = dict(context.params) if context is not None else {}
    data_options = context.obj if context is not None and isinstance(context.obj, dict) else {}
    target_parts = [
        str(arguments[key])
        for key in ("catalog", "namespace", "table", "view", "resource", "role", "policy")
        if arguments.get(key) is not None
    ]
    resource = NormalizedResource(
        kind="polaris",
        fields={
            "operation": action,
            "catalog": str(arguments.get("catalog") or config.polaris.catalog or ""),
            "namespace": str(arguments.get("namespace") or ""),
            "table": str(arguments.get("table") or ""),
            "resource": str(arguments.get("resource") or ""),
            "role": str(arguments.get("catalog_role") or arguments.get("principal_role") or ""),
            "policy": str(arguments.get("policy") or ""),
        },
    )
    _authorize_cli_action(
        tool="governed_data",
        action=action,
        target="/".join(target_parts) or config.polaris.catalog or "catalog",
        arguments=arguments,
        risk_reason="Read metadata from the governed Apache Polaris catalog.",
        non_interactive_approved=bool(data_options.get("yes", False)),
        resource=resource,
    )
    return PolarisClient(
        config.polaris,
        config.sandbox,
        workspace_roots=config.permissions.workspace_roots,
    )


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", help="Show Loro version.")] = False,
) -> None:
    if version:
        console.print(f"loro {__version__}")
        raise typer.Exit()


@app.command()
def run(
    prompt: Annotated[str, typer.Argument(help="Task prompt for Loro.")],
    resume_session: Annotated[
        str | None,
        typer.Option("--resume-session", help="Resume a saved session and deliver its inbox."),
    ] = None,
    stream: Annotated[
        bool, typer.Option("--stream", help="Render model output token by token as it arrives.")
    ] = False,
) -> None:
    """Run an agent task, optionally resuming a durable session."""
    try:
        result = _run_task(prompt, mode="run", session_id=resume_session, stream=stream)
    except FileNotFoundError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(result.summary)


def _run_task(prompt: str, *, mode: str, session_id: str | None, stream: bool):
    """Run one agent task, live-rendering tokens when streaming is requested."""

    runtime = _runtime()
    if not stream:
        return runtime.run(prompt, mode=mode, session_id=session_id)
    with Live(Text(""), console=console, refresh_per_second=12, transient=True) as live:
        buffer: list[str] = []

        def on_token(chunk: str) -> None:
            buffer.append(chunk)
            live.update(Text("".join(buffer)))

        result = runtime.run(prompt, mode=mode, session_id=session_id, on_token=on_token)
    return result


@app.command()
def plan(
    prompt: Annotated[str, typer.Argument(help="Planning prompt for Loro.")],
    resume_session: Annotated[
        str | None,
        typer.Option("--resume-session", help="Resume a saved session and deliver its inbox."),
    ] = None,
    format: Annotated[
        str, typer.Option("--format", help="Output format: text or agraph.")
    ] = "text",
    out: Annotated[Path, typer.Option("--out", help="Path for --format agraph output.")] = Path(
        "generated.agraph.yaml"
    ),
    stream: Annotated[
        bool, typer.Option("--stream", help="Render model output token by token as it arrives.")
    ] = False,
) -> None:
    """Run a read-only planning task."""
    if format == "agraph":
        from loro.agraph.generate import write_generated_graph

        try:
            console.print(str(write_generated_graph(prompt, out, load_config())))
        except ValueError as error:
            raise typer.BadParameter(str(error)) from error
        return
    if format != "text":
        raise typer.BadParameter("--format must be text or agraph")
    try:
        result = _run_task(prompt, mode="plan", session_id=resume_session, stream=stream)
    except FileNotFoundError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(result.summary)


@config_app.callback(invoke_without_command=True)
def config_root(ctx: typer.Context) -> None:
    """Show resolved configuration, or run a config subcommand."""
    if ctx.invoked_subcommand is None:
        console.print_json(load_config().model_dump_json(indent=2))


@config_app.command("show")
def show_config() -> None:
    """Show resolved configuration."""
    console.print_json(load_config().model_dump_json(indent=2))


@policy_app.command("explain")
def policy_explain(
    request_json: Annotated[
        str,
        typer.Argument(
            help=("JSON request with tool, action, optional target, and optional resource.")
        ),
    ],
) -> None:
    """Explain the policy decision for a normalized request fixture."""
    try:
        fixture = json.loads(request_json)
    except json.JSONDecodeError as error:
        raise typer.BadParameter(f"Invalid request JSON: {error.msg}") from error
    if not isinstance(fixture, dict):
        raise typer.BadParameter("Policy request fixture must be a JSON object.")
    tool = fixture.get("tool")
    action = fixture.get("action")
    if not isinstance(tool, str) or not tool.strip():
        raise typer.BadParameter("Policy request requires a non-empty tool.")
    if not isinstance(action, str) or not action.strip():
        raise typer.BadParameter("Policy request requires a non-empty action.")
    resource_payload = fixture.get("resource")
    resource = None
    if resource_payload is not None:
        if not isinstance(resource_payload, dict):
            raise typer.BadParameter("Policy request resource must be a JSON object.")
        if isinstance(resource_payload.get("fields"), dict):
            resource_payload = {
                "kind": resource_payload.get("kind"),
                **resource_payload["fields"],
            }
        try:
            resource = resource_from_payload(resource_payload)
        except PermissionError as error:
            raise typer.BadParameter(str(error)) from error
    target = fixture.get("target")
    if target is not None and not isinstance(target, str):
        raise typer.BadParameter("Policy request target must be a string.")
    result = _permissions().evaluate(
        PermissionRequest(
            tool=tool.strip(),
            action=action.strip(),
            target=target,
            resource=resource,
        )
    )
    console.print_json(
        data={
            "decision": result.decision,
            "reason": result.reason,
            "policy_version": result.policy_version,
            "policy_source": result.policy_source,
            "matched_rule": result.matched_rule,
            "normalized_resource": resource.to_payload() if resource else None,
        }
    )


@app.command()
def configure(
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name. Omit for interactive prompt."),
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Primary model name.")] = None,
    small_model: Annotated[
        str | None,
        typer.Option("--small-model", help="Small/fast model name."),
    ] = None,
    api_key_env: Annotated[
        str | None,
        typer.Option("--api-key-env", help="Environment variable containing API key."),
    ] = None,
    credential_ref: Annotated[
        str | None,
        typer.Option("--credential-ref", help="OS-keyring vault reference for the API key."),
    ] = None,
    base_url: Annotated[str | None, typer.Option("--base-url", help="Provider base URL.")] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Create a local provider configuration."""
    chosen_provider = provider
    interactive = chosen_provider is None
    if chosen_provider is None:
        console.print("Available providers:")
        for name in provider_names():
            profile = get_provider_profile(name)
            console.print(f"- {name}: {profile.display_name}")
        chosen_provider = typer.prompt("Provider", default="mock")
    profile = get_provider_profile(chosen_provider)
    chosen_model = model or (
        typer.prompt("Primary model", default=profile.default_model)
        if interactive
        else profile.default_model
    )
    chosen_small = small_model or (
        typer.prompt("Small model", default=profile.small_model)
        if interactive
        else profile.small_model
    )
    chosen_key_env = api_key_env
    if chosen_key_env is None:
        chosen_key_env = (
            typer.prompt("API key env var", default=profile.api_key_env)
            if profile.api_key_env and interactive
            else profile.api_key_env
        )
    chosen_base_url = base_url
    if chosen_base_url is None:
        chosen_base_url = (
            typer.prompt("Base URL", default=profile.base_url)
            if profile.base_url and interactive
            else profile.base_url
        )

    config = load_config()
    config.model = model_config_from_profile(
        profile.name,
        model=chosen_model,
        small_model=chosen_small,
        api_key_env=chosen_key_env,
        credential_ref=credential_ref,
        base_url=chosen_base_url,
    )
    written = write_local_model_config(output, config)
    _audit().write(
        "config.provider_written",
        provider=config.model.provider,
        model=config.model.model,
        path=str(written),
    )
    console.print(f"Wrote provider config: {written}")


@setup_app.command("provider")
def setup_provider(
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name. Omit for interactive prompt."),
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Primary model name.")] = None,
    small_model: Annotated[
        str | None,
        typer.Option("--small-model", help="Small/fast model name."),
    ] = None,
    api_key_env: Annotated[
        str | None,
        typer.Option("--api-key-env", help="Environment variable containing API key."),
    ] = None,
    credential_ref: Annotated[
        str | None,
        typer.Option("--credential-ref", help="OS-keyring vault reference for the API key."),
    ] = None,
    base_url: Annotated[str | None, typer.Option("--base-url", help="Provider base URL.")] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Run the AI provider setup wizard."""
    configure(
        provider=provider,
        model=model,
        small_model=small_model,
        api_key_env=api_key_env,
        credential_ref=credential_ref,
        base_url=base_url,
        output=output,
    )


@setup_app.command("memory")
def setup_memory(
    enabled: Annotated[
        bool | None,
        typer.Option("--enabled/--disabled", help="Enable local memory."),
    ] = None,
    path: Annotated[str | None, typer.Option("--path", help="Local memory directory.")] = None,
    auto_propose: Annotated[
        bool | None,
        typer.Option("--auto-propose/--no-auto-propose", help="Allow local memory proposals."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Configure private local memory."""
    interactive = enabled is None and path is None and auto_propose is None
    config = load_config()
    if enabled is None:
        enabled = (
            typer.confirm("Enable local memory?", default=config.memory.local.enabled)
            if interactive
            else config.memory.local.enabled
        )
    if path is None:
        path = (
            typer.prompt("Local memory path", default=config.memory.local.path)
            if interactive
            else config.memory.local.path
        )
    if auto_propose is None:
        auto_propose = (
            typer.confirm(
                "Enable local memory proposals?",
                default=config.memory.local.auto_propose,
            )
            if interactive
            else config.memory.local.auto_propose
        )
    config.memory.local.enabled = enabled
    config.memory.local.path = path
    config.memory.local.auto_propose = auto_propose
    written = write_config_sections(output, config, ["memory.local"])
    _audit().write("config.local_memory_written", path=str(written), enabled=enabled)
    console.print(f"Wrote local memory config: {written}")


@setup_app.command("shared-memory")
def setup_shared_memory(
    enabled: Annotated[
        bool | None,
        typer.Option("--enabled/--disabled", help="Enable shared enterprise memory."),
    ] = None,
    backend: Annotated[
        str | None,
        typer.Option("--backend", help="Shared memory backend: postgres or iceberg."),
    ] = None,
    postgres_dsn_env: Annotated[
        str | None,
        typer.Option("--postgres-dsn-env", help="Environment variable with Postgres DSN."),
    ] = None,
    postgres_schema: Annotated[
        str | None,
        typer.Option("--postgres-schema", help="Postgres schema for shared memory."),
    ] = None,
    iceberg_catalog_name: Annotated[
        str | None,
        typer.Option("--iceberg-catalog-name", help="PyIceberg catalog name."),
    ] = None,
    iceberg_catalog_uri_env: Annotated[
        str | None,
        typer.Option("--iceberg-catalog-uri-env", help="Env var with Iceberg REST catalog URI."),
    ] = None,
    iceberg_credential_env: Annotated[
        str | None,
        typer.Option("--iceberg-credential-env", help="Env var with Iceberg credential."),
    ] = None,
    iceberg_token_env: Annotated[
        str | None,
        typer.Option("--iceberg-token-env", help="Env var with Iceberg bearer token."),
    ] = None,
    iceberg_warehouse: Annotated[
        str | None,
        typer.Option("--iceberg-warehouse", help="Iceberg warehouse/catalog identifier."),
    ] = None,
    iceberg_namespace: Annotated[
        str | None,
        typer.Option("--iceberg-namespace", help="Iceberg namespace for shared memory."),
    ] = None,
    iceberg_table: Annotated[
        str | None,
        typer.Option("--iceberg-table", help="Iceberg table for shared memory."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Configure explicit-only shared enterprise memory."""
    config = load_config()
    interactive = all(
        value is None
        for value in [
            enabled,
            backend,
            postgres_dsn_env,
            postgres_schema,
            iceberg_catalog_name,
            iceberg_catalog_uri_env,
            iceberg_credential_env,
            iceberg_token_env,
            iceberg_warehouse,
            iceberg_namespace,
            iceberg_table,
        ]
    )
    if enabled is None:
        enabled = (
            typer.confirm("Enable shared enterprise memory?", default=config.memory.shared.enabled)
            if interactive
            else config.memory.shared.enabled
        )
    if backend is None:
        backend = (
            typer.prompt("Shared memory backend", default=config.memory.shared.backend)
            if interactive
            else config.memory.shared.backend
        )
    if backend not in {"postgres", "iceberg"}:
        raise typer.BadParameter("Shared memory backend must be postgres or iceberg.")
    shared = config.memory.shared
    shared.enabled = enabled
    shared.backend = backend  # type: ignore[assignment]
    if backend == "postgres":
        shared.postgres_dsn_env = postgres_dsn_env or (
            typer.prompt("Postgres DSN env var", default=shared.postgres_dsn_env)
            if interactive
            else shared.postgres_dsn_env
        )
        shared.postgres_schema = postgres_schema or (
            typer.prompt("Postgres schema", default=shared.postgres_schema)
            if interactive
            else shared.postgres_schema
        )
    else:
        shared.iceberg_catalog_name = iceberg_catalog_name or (
            typer.prompt("Iceberg catalog name", default=shared.iceberg_catalog_name)
            if interactive
            else shared.iceberg_catalog_name
        )
        shared.iceberg_catalog_uri_env = iceberg_catalog_uri_env or (
            typer.prompt("Iceberg REST catalog URI env var", default=shared.iceberg_catalog_uri_env)
            if interactive
            else shared.iceberg_catalog_uri_env
        )
        shared.iceberg_credential_env = iceberg_credential_env or (
            typer.prompt("Iceberg credential env var", default=shared.iceberg_credential_env)
            if interactive
            else shared.iceberg_credential_env
        )
        shared.iceberg_token_env = iceberg_token_env or (
            typer.prompt("Iceberg token env var", default=shared.iceberg_token_env)
            if interactive
            else shared.iceberg_token_env
        )
        shared.iceberg_warehouse = iceberg_warehouse or (
            typer.prompt("Iceberg warehouse", default=shared.iceberg_warehouse or "")
            if interactive
            else shared.iceberg_warehouse
        )
        shared.iceberg_namespace = iceberg_namespace or (
            typer.prompt("Iceberg namespace", default=shared.iceberg_namespace)
            if interactive
            else shared.iceberg_namespace
        )
        shared.iceberg_table = iceberg_table or (
            typer.prompt("Iceberg table", default=shared.iceberg_table)
            if interactive
            else shared.iceberg_table
        )
        if shared.iceberg_warehouse == "":
            shared.iceberg_warehouse = None
    written = write_config_sections(output, config, ["memory.shared"])
    _audit().write(
        "config.shared_memory_written",
        path=str(written),
        enabled=enabled,
        backend=backend,
    )
    console.print(f"Wrote shared memory config: {written}")
    console.print("Shared memory writes remain explicit-only and draft-gated.")


@setup_app.command("polaris")
def setup_polaris(
    enabled: Annotated[
        bool | None,
        typer.Option("--enabled/--disabled", help="Enable Polaris governed data discovery."),
    ] = None,
    cli_path: Annotated[str | None, typer.Option("--cli-path", help="Polaris CLI path.")] = None,
    realm: Annotated[str | None, typer.Option("--realm", help="Polaris realm.")] = None,
    catalog: Annotated[
        str | None,
        typer.Option("--catalog", help="Default Polaris catalog."),
    ] = None,
    require_role_inspection: Annotated[
        bool | None,
        typer.Option(
            "--require-role-inspection/--no-require-role-inspection",
            help="Require role inspection when explaining access.",
        ),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Configure Apache Polaris governed data discovery."""
    interactive = all(
        value is None for value in [enabled, cli_path, realm, catalog, require_role_inspection]
    )
    config = load_config()
    polaris = config.polaris
    if enabled is None:
        enabled = (
            typer.confirm("Enable Polaris governed data discovery?", default=polaris.enabled)
            if interactive
            else polaris.enabled
        )
    polaris.enabled = enabled
    polaris.cli_path = cli_path or (
        typer.prompt("Polaris CLI path", default=polaris.cli_path)
        if interactive
        else polaris.cli_path
    )
    polaris.realm = (
        realm
        if realm is not None
        else (
            typer.prompt("Polaris realm", default=polaris.realm or "")
            if interactive
            else polaris.realm
        )
    )
    polaris.catalog = (
        catalog
        if catalog is not None
        else (
            typer.prompt("Default Polaris catalog", default=polaris.catalog or "")
            if interactive
            else polaris.catalog
        )
    )
    if require_role_inspection is None:
        require_role_inspection = (
            typer.confirm("Require role inspection?", default=polaris.require_role_inspection)
            if interactive
            else polaris.require_role_inspection
        )
    polaris.require_role_inspection = require_role_inspection
    if polaris.realm == "":
        polaris.realm = None
    if polaris.catalog == "":
        polaris.catalog = None
    written = write_config_sections(output, config, ["polaris"])
    _audit().write("config.polaris_written", path=str(written), enabled=enabled)
    console.print(f"Wrote Polaris config: {written}")


@setup_app.command("identity")
def setup_identity(
    subject: Annotated[
        str | None, typer.Option("--subject", help="Stable identity subject.")
    ] = None,
    display_name: Annotated[
        str | None, typer.Option("--display-name", help="Human-readable identity name.")
    ] = None,
    organization: Annotated[
        str | None, typer.Option("--organization", help="Enterprise organization identifier.")
    ] = None,
    tenant: Annotated[
        str | None, typer.Option("--tenant", help="Default enterprise tenant.")
    ] = None,
    groups: Annotated[
        str | None, typer.Option("--groups", help="Comma-separated identity groups.")
    ] = None,
    roles: Annotated[
        str | None, typer.Option("--roles", help="Comma-separated identity roles.")
    ] = None,
    auth_method: Annotated[
        str | None, typer.Option("--auth-method", help="Authentication method label.")
    ] = None,
    source: Annotated[
        str | None, typer.Option("--source", help="Trusted identity assertion source.")
    ] = None,
    environment_enabled: Annotated[
        bool | None,
        typer.Option(
            "--environment/--no-environment",
            help="Allow identity fields from environment variables.",
        ),
    ] = None,
    environment_prefix: Annotated[
        str | None, typer.Option("--environment-prefix", help="Identity environment prefix.")
    ] = None,
    required_fields: Annotated[
        str | None,
        typer.Option("--required-fields", help="Comma-separated fields required to run."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Configure local or enterprise-provided identity context."""
    values = [
        subject,
        display_name,
        organization,
        tenant,
        groups,
        roles,
        auth_method,
        source,
        environment_enabled,
        environment_prefix,
        required_fields,
    ]
    interactive = all(value is None for value in values)
    config = load_config()
    identity = config.identity
    if interactive:
        subject = typer.prompt("Identity subject", default=identity.subject or "")
        display_name = typer.prompt("Display name", default=identity.display_name or subject)
        organization = typer.prompt("Organization", default=identity.organization or "")
        tenant = typer.prompt("Tenant", default=identity.tenant or "default")
        groups = typer.prompt("Groups (comma-separated)", default=",".join(identity.groups))
        roles = typer.prompt("Roles (comma-separated)", default=",".join(identity.roles))
        auth_method = typer.prompt(
            "Authentication method",
            default=identity.auth_method or "os_user",
        )
        source = typer.prompt("Identity source", default=identity.source or "config")
        environment_enabled = typer.confirm(
            "Allow identity environment variables?",
            default=identity.environment_enabled,
        )
        environment_prefix = typer.prompt(
            "Identity environment prefix",
            default=identity.environment_prefix,
        )
        required_fields = typer.prompt(
            "Required fields (comma-separated)",
            default=",".join(identity.required_fields),
        )
    for field, value in {
        "subject": subject,
        "display_name": display_name,
        "organization": organization,
        "tenant": tenant,
        "auth_method": auth_method,
        "source": source,
    }.items():
        if value is not None:
            setattr(identity, field, value or None)
    if environment_prefix is not None:
        if not environment_prefix.strip():
            raise typer.BadParameter("Identity environment prefix cannot be empty.")
        identity.environment_prefix = environment_prefix.strip()
    if groups is not None:
        identity.groups = _comma_separated(groups)
    if roles is not None:
        identity.roles = _comma_separated(roles)
    if required_fields is not None:
        allowed = set(identity.__class__.model_fields) - {
            "environment_enabled",
            "environment_prefix",
            "required_fields",
        }
        parsed_required = _comma_separated(required_fields)
        unknown = sorted(set(parsed_required) - allowed)
        if unknown:
            raise typer.BadParameter(f"Unknown required identity fields: {', '.join(unknown)}")
        identity.required_fields = parsed_required  # type: ignore[assignment]
    if environment_enabled is not None:
        identity.environment_enabled = environment_enabled
    written = write_config_sections(output, config, ["identity"])
    identity_diagnostic = diagnose_identity(config.identity)
    AuditLogger(config.audit, identity_diagnostic.context, safety_config=config.safety).write(
        "config.identity_written",
        path=str(written),
        identity_ready=identity_diagnostic.ok,
        missing_fields=list(identity_diagnostic.missing_fields),
    )
    console.print(f"Wrote identity config: {written}")


@setup_app.command("approvals")
def setup_approvals(
    interactive: Annotated[
        bool | None,
        typer.Option(
            "--interactive/--no-interactive",
            help="Allow trusted terminal approval prompts.",
        ),
    ] = None,
    allow_non_interactive: Annotated[
        bool | None,
        typer.Option(
            "--allow-non-interactive/--deny-non-interactive",
            help="Allow --yes and explicit user-authored approval fields.",
        ),
    ] = None,
    allow_session_scope: Annotated[
        bool | None,
        typer.Option(
            "--allow-session-scope/--deny-session-scope",
            help="Allow exact-match approvals to be reused during one runtime session.",
        ),
    ] = None,
    once_ttl_seconds: Annotated[
        int | None,
        typer.Option("--once-ttl", help="One-time approval lifetime in seconds."),
    ] = None,
    session_ttl_seconds: Annotated[
        int | None,
        typer.Option("--session-ttl", help="Session approval lifetime in seconds."),
    ] = None,
    store: Annotated[
        str | None,
        typer.Option("--store", help="Approval store: memory or json."),
    ] = None,
    store_path: Annotated[
        str | None,
        typer.Option("--store-path", help="Path for the durable JSON approval store."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Configure interactive and non-interactive approval behavior."""
    values = [
        interactive,
        allow_non_interactive,
        allow_session_scope,
        once_ttl_seconds,
        session_ttl_seconds,
        store,
        store_path,
    ]
    wizard = all(value is None for value in values)
    config = load_config()
    approvals = config.approvals
    if wizard:
        interactive = typer.confirm(
            "Enable interactive approval prompts?",
            default=approvals.interactive,
        )
        allow_non_interactive = typer.confirm(
            "Allow non-interactive approvals?",
            default=approvals.allow_non_interactive,
        )
        allow_session_scope = typer.confirm(
            "Allow exact-match session approvals?",
            default=approvals.allow_session_scope,
        )
        once_ttl_seconds = typer.prompt(
            "One-time approval TTL (seconds)",
            default=approvals.once_ttl_seconds,
            type=int,
        )
        session_ttl_seconds = typer.prompt(
            "Session approval TTL (seconds)",
            default=approvals.session_ttl_seconds,
            type=int,
        )
        store = typer.prompt(
            "Approval store (memory/json)",
            default=approvals.store,
        )
        if store == "json":
            store_path = typer.prompt(
                "Durable approval store path",
                default=approvals.store_path,
            )
    if interactive is not None:
        approvals.interactive = interactive
    if allow_non_interactive is not None:
        approvals.allow_non_interactive = allow_non_interactive
    if allow_session_scope is not None:
        approvals.allow_session_scope = allow_session_scope
    if once_ttl_seconds is not None:
        if once_ttl_seconds < 1:
            raise typer.BadParameter("One-time approval TTL must be positive.")
        approvals.once_ttl_seconds = once_ttl_seconds
    if session_ttl_seconds is not None:
        if session_ttl_seconds < 1:
            raise typer.BadParameter("Session approval TTL must be positive.")
        approvals.session_ttl_seconds = session_ttl_seconds
    if store is not None:
        normalized_store = store.strip().casefold()
        if normalized_store not in {"memory", "json"}:
            raise typer.BadParameter("Approval store must be memory or json.")
        approvals.store = normalized_store  # type: ignore[assignment]
    if store_path is not None:
        if not store_path.strip():
            raise typer.BadParameter("Approval store path cannot be empty.")
        approvals.store_path = store_path.strip()
    written = write_config_sections(output, config, ["approvals"])
    _audit().write(
        "config.approvals_written",
        path=str(written),
        interactive=approvals.interactive,
        allow_non_interactive=approvals.allow_non_interactive,
        allow_session_scope=approvals.allow_session_scope,
        store=approvals.store,
    )
    console.print(f"Wrote approval config: {written}")


@setup_app.command("skills")
def setup_skills(
    enabled: Annotated[
        bool | None,
        typer.Option("--enabled/--disabled", help="Enable Agent Skills discovery."),
    ] = None,
    allow_user: Annotated[
        bool | None,
        typer.Option("--allow-user/--deny-user", help="Allow user-scoped skills."),
    ] = None,
    allow_project: Annotated[
        bool | None,
        typer.Option("--allow-project/--deny-project", help="Allow project-scoped skills."),
    ] = None,
    allow_scripts: Annotated[
        bool | None,
        typer.Option(
            "--allow-scripts/--deny-scripts",
            help="Permit reviewed skill scripts to enter shell approval.",
        ),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Configure Agent Skills discovery and script policy."""
    wizard = all(value is None for value in (enabled, allow_user, allow_project, allow_scripts))
    config = load_config()
    skills = config.skills
    if wizard:
        enabled = typer.confirm("Enable Agent Skills?", default=skills.enabled)
        allow_user = typer.confirm("Allow user-scoped skills?", default=skills.allow_user)
        allow_project = typer.confirm("Allow project-scoped skills?", default=skills.allow_project)
        allow_scripts = typer.confirm(
            "Allow reviewed skill scripts to request shell approval?",
            default=skills.allow_scripts,
        )
    if enabled is not None:
        skills.enabled = enabled
    if allow_user is not None:
        skills.allow_user = allow_user
    if allow_project is not None:
        skills.allow_project = allow_project
    if allow_scripts is not None:
        skills.allow_scripts = allow_scripts
    written = write_config_sections(output, config, ["skills"])
    _audit().write(
        "config.skills_written",
        path=str(written),
        enabled=skills.enabled,
        allow_user=skills.allow_user,
        allow_project=skills.allow_project,
        allow_scripts=skills.allow_scripts,
    )
    console.print(f"Wrote Agent Skills config: {written}")


@setup_app.command("sandbox")
def setup_sandbox(
    profile: Annotated[
        str, typer.Option("--profile", help="Named sandbox profile to update.")
    ] = "controlled-shell",
    backend: Annotated[
        str | None, typer.Option("--backend", help="Process backend: process or bubblewrap.")
    ] = None,
    require_os_enforcement: Annotated[
        bool | None,
        typer.Option(
            "--require-os-enforcement/--allow-advisory",
            help="Fail closed unless the selected backend enforces OS isolation.",
        ),
    ] = None,
    network: Annotated[
        str | None, typer.Option("--network", help="Network policy: inherit or deny.")
    ] = None,
    allowed_executables: Annotated[
        str | None,
        typer.Option("--allowed-executables", help="Comma-separated executable path globs."),
    ] = None,
    environment_allowlist: Annotated[
        str | None,
        typer.Option("--environment", help="Comma-separated inherited environment names."),
    ] = None,
    writable_roots: Annotated[
        str | None,
        typer.Option("--writable-roots", help="Comma-separated writable roots for Bubblewrap."),
    ] = None,
    max_seconds: Annotated[
        int | None, typer.Option("--max-seconds", help="Maximum child runtime.")
    ] = None,
    max_output_bytes: Annotated[
        int | None, typer.Option("--max-output-bytes", help="Combined stdout/stderr limit.")
    ] = None,
    output: Annotated[Path, typer.Option("--output", "-o", help="Config file to write.")] = Path(
        ".loro/config.local.toml"
    ),
) -> None:
    """Configure a named subprocess sandbox profile."""
    config = load_config()
    if profile not in config.sandbox.profiles:
        raise typer.BadParameter(f"Unknown sandbox profile: {profile}")
    selected = config.sandbox.profiles[profile]
    if backend is not None:
        if backend not in {"process", "bubblewrap"}:
            raise typer.BadParameter("Sandbox backend must be process or bubblewrap.")
        selected.backend = backend  # type: ignore[assignment]
    if network is not None:
        if network not in {"inherit", "deny"}:
            raise typer.BadParameter("Sandbox network policy must be inherit or deny.")
        selected.network = network  # type: ignore[assignment]
    if require_os_enforcement is not None:
        selected.require_os_enforcement = require_os_enforcement
    if allowed_executables is not None:
        selected.allowed_executables = _comma_separated(allowed_executables)
    if environment_allowlist is not None:
        selected.environment_allowlist = _comma_separated(environment_allowlist)
    if writable_roots is not None:
        selected.writable_roots = _comma_separated(writable_roots)
    if max_seconds is not None:
        if not 1 <= max_seconds <= 3600:
            raise typer.BadParameter("Sandbox max seconds must be between 1 and 3600.")
        selected.max_seconds = max_seconds
    if max_output_bytes is not None:
        if not 1024 <= max_output_bytes <= 100_000_000:
            raise typer.BadParameter("Sandbox max output bytes must be between 1024 and 100000000.")
        selected.max_output_bytes = max_output_bytes
    validated = LoroConfig.model_validate(config.model_dump())
    written = write_config_sections(output, validated, ["sandbox"])
    _audit().write(
        "config.sandbox_written",
        path=str(written),
        profile=profile,
        backend=selected.backend,
        require_os_enforcement=selected.require_os_enforcement,
        network=selected.network,
    )
    console.print(f"Wrote sandbox config: {written}")


@setup_app.command("audit")
def setup_audit(
    sink: Annotated[str | None, typer.Option("--sink", help="Audit sink: jsonl or http.")] = None,
    path: Annotated[str | None, typer.Option("--path", help="Local JSONL audit path.")] = None,
    http_url: Annotated[
        str | None, typer.Option("--http-url", help="External HTTP collector URL.")
    ] = None,
    http_token_env: Annotated[
        str | None,
        typer.Option("--http-token-env", help="Environment variable containing bearer token."),
    ] = None,
    failure_mode: Annotated[
        str | None, typer.Option("--failure-mode", help="Delivery failure mode: warn or fail.")
    ] = None,
    buffer_path: Annotated[
        str | None,
        typer.Option("--buffer-path", help="Bounded local delivery buffer path."),
    ] = None,
    max_buffer_events: Annotated[
        int | None, typer.Option("--max-buffer-events", help="Maximum retained events.")
    ] = None,
    max_retries: Annotated[
        int | None, typer.Option("--max-retries", help="HTTP delivery retries.")
    ] = None,
    backoff_seconds: Annotated[
        float | None, typer.Option("--backoff-seconds", help="Initial retry backoff.")
    ] = None,
    timeout_seconds: Annotated[
        float | None, typer.Option("--timeout-seconds", help="HTTP request timeout.")
    ] = None,
    metrics_enabled: Annotated[
        bool | None,
        typer.Option("--metrics/--no-metrics", help="Enable content-free operational metrics."),
    ] = None,
    metrics_path: Annotated[
        str | None, typer.Option("--metrics-path", help="Operational metrics state path.")
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Configure local or external audit delivery and buffering."""
    values = [
        sink,
        path,
        http_url,
        http_token_env,
        failure_mode,
        buffer_path,
        max_buffer_events,
        max_retries,
        backoff_seconds,
        timeout_seconds,
        metrics_enabled,
        metrics_path,
    ]
    wizard = all(value is None for value in values)
    config = load_config()
    previous_audit = config.audit.model_copy(deep=True)
    audit = config.audit
    if wizard:
        sink = typer.prompt("Audit sink (jsonl/http)", default=audit.sink)
        path = typer.prompt("Local JSONL audit path", default=audit.path)
        if sink.strip().casefold() == "http":
            http_url = typer.prompt("HTTP collector URL", default=audit.http_url or "")
            http_token_env = typer.prompt(
                "HTTP bearer token environment variable",
                default=audit.http_token_env or "",
            )
            failure_mode = typer.prompt(
                "Delivery failure mode (warn/fail)", default=audit.failure_mode
            )
            buffer_path = typer.prompt("Local buffer path", default=audit.buffer_path)
            max_buffer_events = typer.prompt(
                "Maximum buffered events", default=audit.max_buffer_events, type=int
            )
            max_retries = typer.prompt("HTTP retries", default=audit.max_retries, type=int)
            backoff_seconds = typer.prompt(
                "Initial retry backoff (seconds)",
                default=audit.backoff_seconds,
                type=float,
            )
            timeout_seconds = typer.prompt(
                "HTTP timeout (seconds)", default=audit.timeout_seconds, type=float
            )
        metrics_enabled = typer.confirm(
            "Enable content-free operational metrics?",
            default=audit.metrics_enabled,
        )
        if metrics_enabled:
            metrics_path = typer.prompt(
                "Operational metrics state path", default=audit.metrics_path
            )
    if sink is not None:
        normalized_sink = sink.strip().casefold()
        if normalized_sink not in {"jsonl", "http"}:
            raise typer.BadParameter("Audit sink must be jsonl or http.")
        audit.sink = normalized_sink  # type: ignore[assignment]
    if failure_mode is not None:
        normalized_mode = failure_mode.strip().casefold()
        if normalized_mode not in {"warn", "fail"}:
            raise typer.BadParameter("Audit failure mode must be warn or fail.")
        audit.failure_mode = normalized_mode  # type: ignore[assignment]
    if path is not None:
        audit.path = path
    if http_url is not None:
        audit.http_url = http_url or None
    if http_token_env is not None:
        audit.http_token_env = http_token_env or None
    if buffer_path is not None:
        audit.buffer_path = buffer_path
    if max_buffer_events is not None:
        if max_buffer_events < 1:
            raise typer.BadParameter("Maximum buffered events must be positive.")
        audit.max_buffer_events = max_buffer_events
    if max_retries is not None:
        if not 0 <= max_retries <= 10:
            raise typer.BadParameter("HTTP retries must be between 0 and 10.")
        audit.max_retries = max_retries
    if backoff_seconds is not None:
        if not 0 <= backoff_seconds <= 60:
            raise typer.BadParameter("Retry backoff must be between 0 and 60 seconds.")
        audit.backoff_seconds = backoff_seconds
    if timeout_seconds is not None:
        if not 0 < timeout_seconds <= 300:
            raise typer.BadParameter("HTTP timeout must be between 0 and 300 seconds.")
        audit.timeout_seconds = timeout_seconds
    if metrics_enabled is not None:
        audit.metrics_enabled = metrics_enabled
    if metrics_path is not None:
        audit.metrics_path = metrics_path
    if audit.sink == "http" and not audit.http_url:
        raise typer.BadParameter("HTTP audit sink requires --http-url.")
    written = write_config_sections(output, config, ["audit"])
    AuditLogger(previous_audit, _identity(), safety_config=config.safety).write(
        "config.audit_written",
        path=str(written),
        sink=audit.sink,
        failure_mode=audit.failure_mode,
    )
    console.print(f"Wrote audit config: {written}")


@setup_app.command("quickstart")
def setup_quickstart(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Run provider, identity, memory, Polaris, and MCP setup in sequence."""
    console.print("Loro quickstart setup")
    setup_provider(output=output)
    setup_identity(output=output)
    setup_approvals(output=output)
    setup_audit(output=output)
    setup_memory(output=output)
    setup_shared_memory(output=output)
    setup_polaris(output=output)
    setup_mcp(output=output)
    console.print("Quickstart setup complete.")


@setup_app.command("mcp")
def setup_mcp(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Interactively configure one MCP server without storing secret values."""
    config = load_config()
    enabled = typer.confirm("Enable MCP?", default=config.mcp.enabled)
    config.mcp.enabled = enabled
    if enabled:
        server_id = typer.prompt("Server id", default="example").strip().casefold()
        transport = typer.prompt("Transport (stdio/streamable_http)", default="stdio").strip()
        protocol_mode = typer.prompt(
            "Protocol mode (auto/legacy/2026-07-28)", default="auto"
        ).strip()
        timeout_seconds = float(typer.prompt("Request timeout seconds", default="30"))
        if transport == "stdio":
            command = typer.prompt("Server command").strip()
            arguments = typer.prompt("Arguments separated by spaces", default="").split()
            environment = [
                item.strip()
                for item in typer.prompt(
                    "Environment variable names to allow (comma separated)", default=""
                ).split(",")
                if item.strip()
            ]
            server = MCPServerConfig(
                transport="stdio",
                command=command,
                args=arguments,
                env_allowlist=environment,
                protocol_mode=protocol_mode,
                timeout_seconds=timeout_seconds,
            )
        elif transport == "streamable_http":
            credential_profile = typer.prompt(
                "Credential profile id (blank for none)", default=""
            ).strip()
            if credential_profile and credential_profile not in config.mcp.credential_profiles:
                raise typer.BadParameter(
                    "Unknown credential profile. Create it first with `loro mcp auth-add`."
                )
            server = MCPServerConfig(
                transport="streamable_http",
                url=typer.prompt("MCP endpoint URL").strip(),
                protocol_mode=protocol_mode,
                timeout_seconds=timeout_seconds,
                credential_profile=credential_profile or None,
            )
        else:
            raise typer.BadParameter("Transport must be stdio or streamable_http.")
        if typer.confirm("Enable the experimental MCP Tasks extension?", default=False):
            if protocol_mode == "legacy":
                raise typer.BadParameter(
                    "The Tasks extension requires modern MCP; choose auto or 2026-07-28."
                )
            config.mcp.extensions.setdefault(
                TASKS_EXTENSION_ID,
                MCPExtensionConfig(version="draft", adapter="tasks"),
            )
            server.extensions = list(dict.fromkeys([*server.extensions, TASKS_EXTENSION_ID]))
        config.mcp.servers[server_id] = server
    written = replace_config_section(output, config, "mcp")
    _audit().write(
        "config.mcp_written",
        path=str(written),
        enabled=enabled,
        server_count=len(config.mcp.servers),
    )
    console.print(f"Wrote MCP config: {written}")


@setup_app.command("mcp-server")
def setup_mcp_server(
    enabled: Annotated[
        bool | None,
        typer.Option("--enabled/--disabled", help="Enable Loro MCP server mode."),
    ] = None,
    transport: Annotated[
        str | None,
        typer.Option(help="Server transport: stdio or streamable_http."),
    ] = None,
    exports: Annotated[
        str | None,
        typer.Option(help="Comma-separated read-only Loro tools to export."),
    ] = None,
    port: Annotated[int | None, typer.Option(help="Loopback HTTP port.")] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Configure Loro's least-privilege MCP server role."""
    wizard = all(value is None for value in (enabled, transport, exports, port))
    config = load_config()
    server = config.mcp.server
    if wizard:
        enabled = typer.confirm("Enable Loro MCP server mode?", default=server.enabled)
        transport = typer.prompt("Server transport", default=server.transport)
        exports = typer.prompt(
            "Read-only exports (comma-separated)", default=",".join(server.export_tools)
        )
        port = typer.prompt("Loopback HTTP port", default=server.port, type=int)
    if enabled is not None:
        server.enabled = enabled
    if transport is not None:
        if transport not in {"stdio", "streamable_http"}:
            raise typer.BadParameter("MCP server transport must be stdio or streamable_http.")
        server.transport = transport  # type: ignore[assignment]
    if exports is not None:
        requested = _comma_separated(exports)
        from loro.mcp.server import LoroMCPServerCatalog

        unsupported = sorted(set(requested) - LoroMCPServerCatalog.ALLOWED_TOOLS)
        if unsupported:
            raise typer.BadParameter("Unsupported MCP exports: " + ", ".join(unsupported))
        server.export_tools = requested
    if port is not None:
        if port < 1 or port > 65535:
            raise typer.BadParameter("MCP server port must be between 1 and 65535.")
        server.port = port
    written = write_config_sections(output, config, ["mcp"])
    _audit().write(
        "config.mcp_server_written",
        path=str(written),
        enabled=server.enabled,
        transport=server.transport,
        exports=server.export_tools,
    )
    console.print(f"Wrote MCP server config: {written}")


@identity_app.command("show")
def identity_show() -> None:
    """Show the resolved identity context without exposing credentials."""
    console.print_json(data=_identity().to_payload())


@identity_app.command("doctor")
def identity_doctor() -> None:
    """Check whether the resolved identity satisfies required fields."""
    diagnostic = diagnose_identity(load_config().identity)
    console.print_json(data=diagnostic.to_payload())
    raise typer.Exit(code=0 if diagnostic.ok else 1)


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
    ] = "LORO_AUDIT_COLLECTOR_TOKEN",
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
        raise typer.BadParameter(
            f"Missing DSN env var: {config.memory.shared.postgres_dsn_env}"
        )
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


@mcp_app.command("list")
def mcp_list() -> None:
    """List configured MCP servers without connecting to them."""
    config = load_config().mcp
    console.print_json(
        data={
            "enabled": config.enabled,
            "servers": MCPRegistry(config).payloads(),
        }
    )


@mcp_app.command("extensions")
def mcp_extensions(
    server_id: Annotated[
        str | None, typer.Argument(help="Optional configured MCP server id.")
    ] = None,
) -> None:
    """Show configured extension activation without connecting to a server."""
    try:
        result = _mcp_service().extension_status(server_id)
    except MCPRegistryError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=result)


@mcp_app.command("tasks")
def mcp_tasks(
    server_id: Annotated[
        str | None, typer.Option("--server-id", help="Filter local task handles by server.")
    ] = None,
) -> None:
    """List durable local MCP task handles without connecting."""
    handles = _mcp_service().task_store.list(server_id)
    console.print_json(data={"tasks": [item.model_dump(mode="json") for item in handles]})


@mcp_app.command("inspect")
def mcp_inspect(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
) -> None:
    """Inspect one redacted MCP server configuration without connecting."""
    try:
        payload = MCPRegistry(load_config().mcp).payload(server_id)
    except MCPRegistryError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=payload)


@mcp_app.command("add")
def mcp_add(
    server_id: Annotated[str, typer.Argument(help="Stable lowercase server id.")],
    transport: Annotated[
        str, typer.Option("--transport", help="stdio or streamable_http.")
    ] = "stdio",
    command: Annotated[
        str | None, typer.Option("--command", help="stdio server executable.")
    ] = None,
    args: Annotated[
        list[str] | None, typer.Option("--arg", help="Repeat for each stdio argument.")
    ] = None,
    url: Annotated[str | None, typer.Option("--url", help="Streamable HTTP MCP endpoint.")] = None,
    cwd: Annotated[
        str | None, typer.Option("--cwd", help="stdio server working directory.")
    ] = None,
    env_allowlist: Annotated[
        list[str] | None,
        typer.Option("--env", help="Repeat for each environment variable to expose."),
    ] = None,
    protocol_mode: Annotated[
        str,
        typer.Option("--protocol-mode", help="auto, legacy, or a modern version pin."),
    ] = "auto",
    allowed_versions: Annotated[
        list[str] | None,
        typer.Option("--allowed-version", help="Repeat to replace the default allowlist."),
    ] = None,
    minimum_version: Annotated[
        str | None,
        typer.Option("--minimum-version", help="Reject negotiated versions below this date."),
    ] = None,
    credential_profile: Annotated[
        str | None,
        typer.Option("--credential-profile", help="Configured MCP credential profile id."),
    ] = None,
    extensions: Annotated[
        list[str] | None,
        typer.Option("--extension", help="Repeat for each configured extension id."),
    ] = None,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout", min=0.1, max=300, help="Request timeout seconds."),
    ] = 30,
    disabled: Annotated[
        bool,
        typer.Option("--disabled", help="Save the server but leave it disabled."),
    ] = False,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to update."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Add or replace an MCP server configuration without connecting."""
    config = load_config()
    server_values: dict[str, Any] = {
        "enabled": not disabled,
        "transport": transport,
        "command": command,
        "args": args or [],
        "url": url,
        "cwd": cwd,
        "env_allowlist": env_allowlist or [],
        "protocol_mode": protocol_mode,
        "minimum_protocol_version": minimum_version,
        "timeout_seconds": timeout_seconds,
        "credential_profile": credential_profile,
        "extensions": extensions or [],
    }
    if allowed_versions:
        server_values["allowed_protocol_versions"] = allowed_versions
    try:
        server = MCPServerConfig.model_validate(server_values)
        normalized_id = server_id.strip().casefold()
        candidate_servers = {**config.mcp.servers, normalized_id: server}
        config.mcp = config.mcp.model_copy(update={"enabled": True, "servers": candidate_servers})
        config.mcp = type(config.mcp).model_validate(config.mcp.model_dump())
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    written = write_config_sections(output, config, ["mcp"])
    _audit().write(
        "config.mcp_server_written",
        path=str(written),
        server_id=normalized_id,
        transport=server.transport,
        enabled=server.enabled,
    )
    console.print(f"Configured MCP server {normalized_id}: {written}")


@mcp_app.command("extension-add")
def mcp_extension_add(
    extension_id: Annotated[str, typer.Argument(help="Namespaced MCP extension identifier.")],
    version: Annotated[str, typer.Option("--version", help="Extension schema version.")],
    adapter: Annotated[
        str | None, typer.Option("--adapter", help="Trusted Loro adapter, currently tasks.")
    ] = None,
    settings: Annotated[
        str, typer.Option("--settings", help="Extension settings as a JSON object.")
    ] = "{}",
    settings_schema: Annotated[
        str | None,
        typer.Option("--settings-schema", help="Optional JSON Schema as a JSON object."),
    ] = None,
    disabled: Annotated[
        bool, typer.Option("--disabled", help="Register without activating the extension.")
    ] = False,
    output: Annotated[Path, typer.Option("--output", "-o", help="Config file to update.")] = Path(
        ".loro/config.local.toml"
    ),
) -> None:
    """Register a versioned MCP extension; unknown adapters remain inert."""
    config = load_config()
    parsed_settings = _json_object(settings, label="extension settings")
    parsed_schema = (
        _json_object(settings_schema, label="extension settings schema")
        if settings_schema is not None
        else None
    )
    try:
        extension = MCPExtensionConfig.model_validate(
            {
                "enabled": not disabled,
                "version": version,
                "adapter": adapter,
                "settings": parsed_settings,
                "settings_schema": parsed_schema,
            }
        )
        configured = {**config.mcp.extensions, extension_id: extension}
        config.mcp = type(config.mcp).model_validate(
            config.mcp.model_copy(update={"extensions": configured}).model_dump()
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    written = write_config_sections(output, config, ["mcp"])
    _audit().write(
        "config.mcp_extension_written",
        path=str(written),
        extension_id=extension_id,
        adapter=adapter,
        enabled=not disabled,
    )
    console.print(f"Configured MCP extension {extension_id}: {written}")


@mcp_app.command("auth-add")
def mcp_auth_add(
    profile_id: Annotated[str, typer.Argument(help="Stable lowercase credential profile id.")],
    profile_type: Annotated[
        str,
        typer.Option(
            "--type",
            help="bearer, oauth_client_credentials, or oauth_authorization_code.",
        ),
    ],
    token_env: Annotated[
        str | None, typer.Option("--token-env", help="Bearer token environment variable.")
    ] = None,
    client_id_env: Annotated[
        str | None, typer.Option("--client-id-env", help="OAuth client id environment variable.")
    ] = None,
    client_secret_env: Annotated[
        str | None,
        typer.Option("--client-secret-env", help="OAuth client secret environment variable."),
    ] = None,
    scopes: Annotated[
        list[str] | None, typer.Option("--scope", help="Repeat for each OAuth scope.")
    ] = None,
    redirect_uri: Annotated[
        str,
        typer.Option("--redirect-uri", help="Authorization-code OAuth callback URI."),
    ] = "http://127.0.0.1:8765/callback",
    client_metadata_url: Annotated[
        str | None,
        typer.Option("--client-metadata-url", help="HTTPS Client ID Metadata Document URL."),
    ] = None,
    allow_dynamic_registration: Annotated[
        bool,
        typer.Option(
            "--allow-dynamic-registration",
            help="Allow legacy OAuth Dynamic Client Registration fallback.",
        ),
    ] = False,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to update."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Add an environment-backed MCP credential profile without storing secret values."""
    config = load_config()
    normalized_id = profile_id.strip().casefold()
    try:
        profile = MCPCredentialProfileConfig.model_validate(
            {
                "type": profile_type,
                "token_env": token_env,
                "client_id_env": client_id_env,
                "client_secret_env": client_secret_env,
                "scopes": scopes or [],
                "redirect_uri": redirect_uri,
                "client_metadata_url": client_metadata_url,
                "allow_dynamic_client_registration": allow_dynamic_registration,
            }
        )
        profiles = {**config.mcp.credential_profiles, normalized_id: profile}
        config.mcp = type(config.mcp).model_validate(
            config.mcp.model_copy(update={"credential_profiles": profiles}).model_dump()
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    written = write_config_sections(output, config, ["mcp"])
    _audit().write(
        "config.mcp_credential_profile_written",
        path=str(written),
        profile_id=normalized_id,
        profile_type=profile.type,
    )
    console.print(f"Configured MCP credential profile {normalized_id}: {written}")


@mcp_app.command("auth-list")
def mcp_auth_list() -> None:
    """List MCP credential profiles and environment references without secret values."""
    profiles = load_config().mcp.credential_profiles
    console.print_json(
        data={
            profile_id: profile.model_dump(exclude_none=True)
            for profile_id, profile in sorted(profiles.items())
        }
    )


@mcp_app.command("auth-remove")
def mcp_auth_remove(
    profile_id: Annotated[str, typer.Argument(help="Credential profile id to remove.")],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to update."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Remove an unused MCP credential profile."""
    config = load_config()
    if profile_id not in config.mcp.credential_profiles:
        raise typer.BadParameter(f"Unknown MCP credential profile: {profile_id}")
    attached = sorted(
        server_id
        for server_id, server in config.mcp.servers.items()
        if server.credential_profile == profile_id
    )
    if attached:
        raise typer.BadParameter(
            "Credential profile is still used by MCP servers: " + ", ".join(attached)
        )
    del config.mcp.credential_profiles[profile_id]
    written = replace_config_section(output, config, "mcp")
    _audit().write(
        "config.mcp_credential_profile_removed",
        path=str(written),
        profile_id=profile_id,
    )
    console.print(f"Removed MCP credential profile {profile_id}: {written}")


@mcp_app.command("remove")
def mcp_remove(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to update."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Remove an MCP server from local configuration."""
    config = load_config()
    if server_id not in config.mcp.servers:
        raise typer.BadParameter(f"Unknown MCP server: {server_id}")
    del config.mcp.servers[server_id]
    written = replace_config_section(output, config, "mcp")
    _audit().write("config.mcp_server_removed", path=str(written), server_id=server_id)
    console.print(f"Removed MCP server {server_id}: {written}")


@mcp_app.command("doctor")
def mcp_doctor(
    server_id: Annotated[
        str | None, typer.Argument(help="Optional configured MCP server id.")
    ] = None,
) -> None:
    """Validate SDK, server configuration, commands, and allowlisted environment names."""
    diagnostic = diagnose_mcp(load_config().mcp, server_id)
    console.print_json(data=diagnostic)
    raise typer.Exit(code=0 if diagnostic["ok"] else 1)


@mcp_app.command("test")
def mcp_test(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
) -> None:
    """Connect, negotiate, and list capability counts without invoking a tool."""
    _authorize_explicit_mcp_read(server_id, action="test connection", operation="test_connection")
    result = _run_mcp_operation(
        "test connection", server_id, _mcp_service().test_connection(server_id)
    )
    console.print_json(data=result)


@mcp_app.command("tools")
def mcp_tools(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
) -> None:
    """List tools exposed by an MCP server without invoking them."""
    _authorize_explicit_mcp_read(server_id, action="list tools", operation="list_tools")
    result = _run_mcp_operation("list tools", server_id, _mcp_service().list_tools(server_id))
    console.print_json(data=result)


@mcp_app.command("call")
def mcp_call(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
    tool_name: Annotated[str, typer.Argument(help="Remote MCP tool name.")],
    arguments: Annotated[
        str,
        typer.Option("--arguments", "-a", help="Tool arguments as a JSON object."),
    ] = "{}",
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Use an allowed non-interactive approval."),
    ] = False,
) -> None:
    """Invoke an MCP tool after an exact Loro permission and approval decision."""
    parsed_arguments = _json_object(arguments, label="tool arguments")
    config = load_config()
    try:
        server = MCPRegistry(config.mcp).get(server_id)
    except MCPRegistryError as error:
        raise typer.BadParameter(str(error)) from error
    resource = mcp_resource(
        operation="call_tool",
        server_id=server_id,
        transport=server.transport,
        endpoint=_mcp_server_endpoint(server),
        name=tool_name,
        arguments=parsed_arguments,
    )
    _authorize_cli_action(
        tool="mcp",
        action="call tool",
        target=resource.target,
        arguments={
            "server_id": server_id,
            "tool_name": tool_name,
            "arguments": parsed_arguments,
        },
        risk_reason="Invoke a remote MCP tool with the displayed exact arguments.",
        non_interactive_approved=yes,
        resource=resource,
    )
    result = _run_mcp_operation(
        "call tool", server_id, _mcp_service().call_tool(server_id, tool_name, parsed_arguments)
    )
    console.print_json(data=result)


@mcp_app.command("task-start")
def mcp_task_start(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
    tool_name: Annotated[str, typer.Argument(help="Remote task-capable MCP tool name.")],
    arguments: Annotated[
        str, typer.Option("--arguments", "-a", help="Tool arguments as a JSON object.")
    ] = "{}",
    yes: Annotated[
        bool, typer.Option("--yes", help="Use an allowed non-interactive approval.")
    ] = False,
) -> None:
    """Start a task-capable MCP tool call after exact approval."""
    parsed = _json_object(arguments, label="task tool arguments")
    _authorize_mcp_mutation(
        server_id,
        action="start task",
        operation="task_start",
        name=tool_name,
        arguments=parsed,
        yes=yes,
        risk_reason="Start a remote MCP task with the displayed exact arguments.",
    )
    result = _run_mcp_operation(
        "start task", server_id, _mcp_service().start_task(server_id, tool_name, parsed)
    )
    console.print_json(data=result)


@mcp_app.command("task-get")
def mcp_task_get(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
    task_id: Annotated[str, typer.Argument(help="Durable MCP task id.")],
) -> None:
    """Refresh a durable MCP task handle from its server."""
    _authorize_explicit_mcp_read(server_id, action="get task", operation="task_get", name=task_id)
    result = _run_mcp_operation("get task", server_id, _mcp_service().get_task(server_id, task_id))
    console.print_json(data=result)


@mcp_app.command("task-update")
def mcp_task_update(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
    task_id: Annotated[str, typer.Argument(help="Durable MCP task id.")],
    responses: Annotated[
        str, typer.Option("--responses", "-r", help="Input responses as a JSON object.")
    ],
    yes: Annotated[
        bool, typer.Option("--yes", help="Use an allowed non-interactive approval.")
    ] = False,
) -> None:
    """Send explicitly approved input to a waiting MCP task."""
    parsed = _json_object(responses, label="task input responses")
    _authorize_mcp_mutation(
        server_id,
        action="update task",
        operation="task_update",
        name=task_id,
        arguments=parsed,
        yes=yes,
        risk_reason="Send the displayed exact input values to a remote MCP task.",
    )
    result = _run_mcp_operation(
        "update task",
        server_id,
        _mcp_service().update_task(server_id, task_id, parsed, user_approved=True),
    )
    console.print_json(data=result)


@mcp_app.command("task-cancel")
def mcp_task_cancel(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
    task_id: Annotated[str, typer.Argument(help="Durable MCP task id.")],
    yes: Annotated[
        bool, typer.Option("--yes", help="Use an allowed non-interactive approval.")
    ] = False,
) -> None:
    """Request cooperative cancellation of a remote MCP task."""
    _authorize_mcp_mutation(
        server_id,
        action="cancel task",
        operation="task_cancel",
        name=task_id,
        arguments={},
        yes=yes,
        risk_reason="Request cooperative cancellation of the displayed remote MCP task.",
    )
    result = _run_mcp_operation(
        "cancel task",
        server_id,
        _mcp_service().cancel_task(server_id, task_id, user_approved=True),
    )
    console.print_json(data=result)


@mcp_app.command("listen")
def mcp_listen(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
    tools: Annotated[bool, typer.Option("--tools", help="Listen for tool list changes.")] = False,
    prompts: Annotated[
        bool, typer.Option("--prompts", help="Listen for prompt list changes.")
    ] = False,
    resources: Annotated[
        bool, typer.Option("--resources", help="Listen for resource list changes.")
    ] = False,
    resource_uris: Annotated[
        list[str] | None, typer.Option("--resource-uri", help="Repeat for resource updates.")
    ] = None,
    max_events: Annotated[int | None, typer.Option("--max-events", min=1)] = None,
    max_seconds: Annotated[float | None, typer.Option("--max-seconds", min=0.1)] = None,
) -> None:
    """Listen for a bounded set of modern MCP change events."""
    if not any((tools, prompts, resources, resource_uris)):
        raise typer.BadParameter("Select at least one MCP event filter.")
    filters = {
        "tools": tools,
        "prompts": prompts,
        "resources": resources,
        "resource_uris": resource_uris or [],
    }
    _authorize_explicit_mcp_read(
        server_id, action="listen for changes", operation="listen", arguments=filters
    )
    result = _run_mcp_operation(
        "listen for changes",
        server_id,
        _mcp_service().listen_changes(
            server_id,
            tools=tools,
            prompts=prompts,
            resources=resources,
            resource_uris=resource_uris,
            max_events=max_events,
            max_seconds=max_seconds,
        ),
    )
    console.print_json(data=result)


@mcp_app.command("server-inspect")
def mcp_server_inspect() -> None:
    """Show the exact least-privilege surface exported by Loro server mode."""
    from loro.mcp.server import LoroMCPServerCatalog

    try:
        catalog = LoroMCPServerCatalog(load_config())
    except (ValueError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=catalog.manifest())


@mcp_app.command("serve")
def mcp_serve() -> None:
    """Run Loro as an MCP server using the configured transport and export allowlist."""
    from loro.mcp.server import MCPServerModeError, run_mcp_server

    try:
        run_mcp_server(load_config())
    except MCPServerModeError as error:
        raise typer.BadParameter(str(error)) from error


@mcp_app.command("resources")
def mcp_resources(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
) -> None:
    """List resources exposed by an MCP server."""
    _authorize_explicit_mcp_read(server_id, action="list resources", operation="list_resources")
    result = _run_mcp_operation(
        "list resources", server_id, _mcp_service().list_resources(server_id)
    )
    console.print_json(data=result)


@mcp_app.command("read")
def mcp_read(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
    uri: Annotated[str, typer.Argument(help="MCP resource URI.")],
) -> None:
    """Read one MCP resource after enforcing any configured deny rule."""
    _authorize_explicit_mcp_read(
        server_id, action="read resource", operation="read_resource", name=uri
    )
    result = _run_mcp_operation(
        "read resource", server_id, _mcp_service().read_resource(server_id, uri)
    )
    console.print_json(data=result)


@mcp_app.command("prompts")
def mcp_prompts(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
) -> None:
    """List prompts exposed by an MCP server."""
    _authorize_explicit_mcp_read(server_id, action="list prompts", operation="list_prompts")
    result = _run_mcp_operation("list prompts", server_id, _mcp_service().list_prompts(server_id))
    console.print_json(data=result)


@mcp_app.command("prompt")
def mcp_prompt(
    server_id: Annotated[str, typer.Argument(help="Configured MCP server id.")],
    prompt_name: Annotated[str, typer.Argument(help="Remote MCP prompt name.")],
    arguments: Annotated[
        str,
        typer.Option("--arguments", "-a", help="Prompt arguments as a JSON object."),
    ] = "{}",
) -> None:
    """Resolve one MCP prompt; returned content remains untrusted context."""
    parsed = _json_object(arguments, label="prompt arguments")
    if not all(isinstance(value, str) for value in parsed.values()):
        raise typer.BadParameter("MCP prompt argument values must be strings.")
    _authorize_explicit_mcp_read(
        server_id,
        action="get prompt",
        operation="get_prompt",
        name=prompt_name,
        arguments=parsed,
    )
    result = _run_mcp_operation(
        "get prompt",
        server_id,
        _mcp_service().get_prompt(server_id, prompt_name, parsed),
    )
    console.print_json(data=result)


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


@memory_app.command("list")
def memory_list() -> None:
    """List local memories."""
    config = load_config()
    store = LocalMemoryStore.from_config(config.memory.local, config.safety)
    memories = store.list()
    if not memories:
        console.print("No local memories yet.")
        return
    for memory in memories:
        console.print(
            f"- [bold]{memory.memory_id}[/bold] "
            f"({memory.created_at.date().isoformat()}): {memory.content}"
        )


@memory_app.command("search")
def memory_search(query: Annotated[str, typer.Argument(help="Search query.")]) -> None:
    """Search local memories."""
    config = load_config()
    store = LocalMemoryStore.from_config(config.memory.local, config.safety)
    memories = store.search(query)
    if not memories:
        console.print("No matching local memories.")
        return
    for memory in memories:
        console.print(f"- [bold]{memory.memory_id}[/bold]: {memory.content}")


@memory_app.command("shared-search")
def memory_shared_search(
    query: Annotated[str, typer.Argument(help="Search query.")],
    tenant_id: Annotated[
        str | None,
        typer.Option("--tenant-id", help="Shared memory tenant. Defaults to active identity."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum memories to return.")] = 20,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Render backend search SQL without executing."),
    ] = False,
) -> None:
    """Search shared enterprise memory or render the backend search statement."""
    config = load_config()
    resolved_tenant = _shared_memory_tenant(config, tenant_id)
    result = search_shared_memories(
        config,
        query=query,
        tenant_id=resolved_tenant,
        limit=limit,
        execute=not dry_run,
    )
    _audit().write(
        "memory.shared_search",
        backend=result.backend,
        query=prompt_preview(query),
        tenant_id=resolved_tenant,
        executed=result.executed,
        record_count=len(result.records),
    )
    if result.executed:
        if not result.records:
            console.print("No matching shared memories.")
            return
        for record in result.records:
            console.print(f"- [bold]{record.citation}[/bold]: {record.summary}")
        return
    console.print_json(
        data={
            "backend": result.backend,
            "query": result.query,
            "tenant_id": result.tenant_id,
            "executed": result.executed,
            "messages": result.messages,
            "sql": result.statement.sql if result.statement else None,
            "params": jsonable_mapping(result.statement.params) if result.statement else None,
        }
    )


@memory_app.command("propose")
def memory_propose(
    content: Annotated[str, typer.Argument(help="Proposed memory content.")],
    target: Annotated[
        str,
        typer.Option("--target", help="Proposal target: local or shared."),
    ] = "local",
    rationale: Annotated[
        str,
        typer.Option("--rationale", help="Why this should be remembered."),
    ] = "",
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    """Create a local memory proposal record without committing memory."""
    if target not in {"local", "shared"}:
        raise typer.BadParameter("target must be local or shared.")
    _enforce_safe_content(
        content,
        context=f"memory.proposal.{target}",
        allow_sensitive=allow_sensitive,
    )
    proposal = MemoryProposal(content=content, target=target, rationale=rationale)
    MemoryProposalStore(Path(load_config().memory.local.path)).propose(proposal)
    _audit().write(
        "memory.proposal_created",
        proposal_id=proposal.proposal_id,
        target=proposal.target,
        content_preview=prompt_preview(content),
    )
    console.print(f"Created memory proposal: {proposal.proposal_id}")


@memory_app.command("proposals")
def memory_proposals() -> None:
    """List local memory proposal records."""
    proposals = MemoryProposalStore(Path(load_config().memory.local.path)).list()
    if not proposals:
        console.print("No memory proposals yet.")
        return
    for proposal in proposals:
        console.print(
            f"- [bold]{proposal.proposal_id}[/bold] "
            f"({proposal.target}, {proposal.status}): {proposal.content}"
        )


@memory_app.command("accept-proposal")
def memory_accept_proposal(
    proposal_id: Annotated[str, typer.Argument(help="Memory proposal id.")],
    tenant_id: Annotated[
        str | None,
        typer.Option("--tenant-id", help="Shared memory tenant. Defaults to active identity."),
    ] = None,
    scope_type: Annotated[
        str, typer.Option("--scope-type", help="Shared memory scope type.")
    ] = "org",
    scope_key: Annotated[
        str, typer.Option("--scope-key", help="Shared memory scope key.")
    ] = "default",
    created_by: Annotated[
        str | None,
        typer.Option("--created-by", help="Shared memory author. Defaults to active identity."),
    ] = None,
) -> None:
    """Accept a proposal into local memory or a shared-memory draft."""
    config = load_config()
    identity = _identity()
    store = MemoryProposalStore(Path(config.memory.local.path))
    proposal = store.get(proposal_id)
    if proposal is None:
        raise typer.BadParameter(f"Unknown memory proposal id: {proposal_id}")
    if proposal.target == "shared":
        _enforce_safe_content(proposal.content, context="memory.shared.proposal")
        resolved_tenant = _shared_memory_tenant(config, tenant_id)
        draft = create_shared_memory_draft(
            content=proposal.content,
            tenant_id=resolved_tenant,
            scope_type=scope_type,
            scope_key=scope_key,
            memory_type="fact",
            classification="public-internal",
            created_by=created_by or identity.subject,
            retention_days=config.memory.shared.retention_days,
        )
        _shared_draft_store(config).stage(draft)
        store.update_status(proposal_id, "accepted_as_shared_draft")
        _audit().write(
            "memory.proposal_accepted",
            proposal_id=proposal_id,
            target=proposal.target,
            draft_id=draft.draft_id,
        )
        console.print(f"Accepted proposal as shared memory draft: {draft.draft_id}")
        return
    memory = LocalMemoryStore.from_config(config.memory.local, config.safety).remember(
        proposal.content
    )
    store.update_status(proposal_id, "accepted")
    _audit().write(
        "memory.proposal_accepted",
        proposal_id=proposal_id,
        target=proposal.target,
        memory_id=memory.memory_id,
    )
    console.print(f"Accepted proposal as local memory: {memory.memory_id}")


@memory_app.command("remember")
def remember_local(
    content: Annotated[str, typer.Argument(help="Memory content.")],
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    """Explicitly write a local memory."""
    _enforce_safe_content(content, context="memory.local", allow_sensitive=allow_sensitive)
    config = load_config()
    store = LocalMemoryStore.from_config(config.memory.local, config.safety)
    memory = store.remember(content, allow_sensitive=allow_sensitive)
    _audit().write(
        "memory.local_written",
        memory_id=memory.memory_id,
        scope=memory.scope,
        content_preview=prompt_preview(content),
    )
    console.print(f"Saved local memory: {memory.memory_id}")


@memory_app.command("drafts")
def memory_drafts() -> None:
    """List staged shared memory drafts."""
    config = load_config()
    store = _shared_draft_store(config)
    drafts = store.list()
    if not drafts:
        console.print("No shared memory drafts yet.")
        return
    for draft in drafts:
        console.print(
            f"- [bold]{draft.draft_id}[/bold] "
            f"({draft.tenant_id}/{draft.scope_type}/{draft.scope_key}): {draft.summary}"
        )


@memory_app.command("schema")
def memory_schema(
    backend: Annotated[
        str,
        typer.Option("--backend", help="Shared memory backend: postgres or iceberg."),
    ] = "postgres",
) -> None:
    """Print shared memory backend schema SQL."""
    config = load_config()
    try:
        if backend == "postgres":
            console.print(PostgresSharedMemoryStore(config.memory.shared).render_schema())
            return
        console.print(shared_memory_schema(backend, config.memory.shared))  # type: ignore[arg-type]
    except (RuntimeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error


@memory_app.command("apply-schema")
def memory_apply_schema(
    execute: Annotated[
        bool,
        typer.Option(
            "--execute",
            help="Apply schema to the configured backend. Without this flag Loro only renders SQL.",
        ),
    ] = False,
) -> None:
    """Render or apply the configured shared memory backend schema."""
    config = load_config()
    backend = config.memory.shared.backend
    if backend == "postgres":
        store = PostgresSharedMemoryStore(config.memory.shared)
        if execute:
            try:
                store.apply_schema()
            except RuntimeError as error:
                raise typer.BadParameter(str(error)) from error
            _audit().write("memory.shared_schema_applied", backend=backend)
            console.print("Applied Postgres shared memory schema.")
            return
        console.print(store.render_schema())
        return
    if execute:
        raise typer.BadParameter("Live Iceberg schema application is not enabled in this MVP.")
    console.print(shared_memory_schema(backend, config.memory.shared))


@memory_app.command("migration-status")
def memory_migration_status() -> None:
    """Show the applied Postgres shared-memory schema version."""
    config = load_config()
    if config.memory.shared.backend != "postgres":
        raise typer.BadParameter("Migration status is available only for Postgres memory.")
    store = PostgresSharedMemoryStore(config.memory.shared)
    try:
        current = store.schema_version()
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(
        data={
            "backend": "postgres",
            "current_version": current,
            "latest_version": LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
            "up_to_date": current == LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
        }
    )


@memory_app.command("migrate")
def memory_migrate(
    target: Annotated[
        int,
        typer.Option("--target", min=0, help="Target Postgres memory schema version."),
    ] = LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Apply the migration plan to the configured database."),
    ] = False,
    allow_destructive: Annotated[
        bool,
        typer.Option(
            "--allow-destructive",
            help="Authorize rollback below the durable baseline. This can delete memory data.",
        ),
    ] = False,
) -> None:
    """Render or apply versioned Postgres shared-memory migrations."""
    config = load_config()
    if config.memory.shared.backend != "postgres":
        raise typer.BadParameter("Migrations are available only for Postgres memory.")
    if target > LATEST_POSTGRES_MEMORY_SCHEMA_VERSION:
        raise typer.BadParameter(
            f"Latest supported schema is version {LATEST_POSTGRES_MEMORY_SCHEMA_VERSION}."
        )
    store = PostgresSharedMemoryStore(config.memory.shared)
    if not execute:
        migrations = postgres_memory_migrations(
            config.memory.shared.postgres_schema,
            tenant_isolation=config.memory.shared.tenant_isolation == "identity",
        )
        console.print(
            f"Postgres memory migration plan to version {target} "
            "(render only; pass --execute to apply):"
        )
        for migration in migrations:
            if migration.version <= target:
                console.print(f"\n-- {migration.version}: {migration.name}\n{migration.up}")
        return
    try:
        result = store.migrate(target_version=target, allow_destructive=allow_destructive)
    except (RuntimeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "memory.schema_migrated",
        backend="postgres",
        previous_version=result.previous_version,
        current_version=result.current_version,
        applied=list(result.applied),
        rolled_back=list(result.rolled_back),
    )
    console.print_json(data=jsonable_mapping(result.__dict__))


@memory_app.command("reconcile")
def memory_reconcile() -> None:
    """Compare Postgres memory state rows with append-only lifecycle events."""
    config = load_config()
    if config.memory.shared.backend != "postgres":
        raise typer.BadParameter("Reconciliation is available only for Postgres memory.")
    identity = resolve_identity(config.identity)
    store = PostgresSharedMemoryStore(
        config.memory.shared,
        authorized_tenant_id=(
            identity.tenant if config.memory.shared.tenant_isolation == "identity" else None
        ),
    )
    try:
        report = store.reconcile()
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "memory.reconciled",
        backend="postgres",
        tenant=identity.tenant,
        ok=report.ok,
        issues=list(report.issues),
    )
    console.print_json(data=jsonable_mapping(report.__dict__ | {"ok": report.ok}))
    raise typer.Exit(code=0 if report.ok else 1)


@memory_app.command("backend-check")
def memory_backend_check() -> None:
    """Check whether the configured shared memory backend is ready."""
    config = load_config()
    check = check_shared_memory_backend(config.memory.shared)
    console.print_json(data=check.__dict__)
    raise typer.Exit(code=0 if check.ok else 1)


@memory_app.command("snapshots")
def memory_snapshots() -> None:
    """Show content-free Iceberg memory and event snapshot state."""
    config = load_config()
    if config.memory.shared.backend != "iceberg":
        raise typer.BadParameter("Snapshot diagnostics are available only for Iceberg memory.")
    identity = resolve_identity(config.identity)
    store = IcebergSharedMemoryStore(
        config.memory.shared,
        authorized_tenant_id=(
            identity.tenant if config.memory.shared.tenant_isolation == "identity" else None
        ),
    )
    try:
        report = store.snapshot_report()
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    payload = {
        "memory": report.memory.__dict__,
        "events": report.events.__dict__,
        "aligned": report.aligned,
    }
    _audit().write("memory.iceberg_snapshots_inspected", **payload)
    console.print_json(data=jsonable_mapping(payload))


@memory_app.command("commit-draft")
def memory_commit_draft(
    draft_id: Annotated[str, typer.Argument(help="Shared memory draft id.")],
    execute: Annotated[
        bool,
        typer.Option(
            "--execute",
            help="Execute the commit. Without this flag Loro only renders backend SQL.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Non-interactive approval for an ask-gated shared-memory commit.",
        ),
    ] = False,
) -> None:
    """Render or execute an explicit shared memory draft commit."""
    config = load_config()
    draft_store = _shared_draft_store(config)
    draft = draft_store.get(draft_id)
    if draft is None:
        raise typer.BadParameter(f"Unknown shared memory draft id: {draft_id}")
    _enforce_safe_content(draft.content, context="memory.shared.commit")

    if execute:
        resource = memory_resource(
            operation="commit",
            tenant=draft.tenant_id,
            scope_type=draft.scope_type,
            scope_key=draft.scope_key,
            backend=config.memory.shared.backend,
        )
        _authorize_cli_action(
            tool="shared_memory",
            action="commit draft",
            target=f"{draft.tenant_id}/{draft.scope_type}/{draft.scope_key}/{draft.draft_id}",
            arguments={
                "draft_id": draft.draft_id,
                "tenant_id": draft.tenant_id,
                "scope_type": draft.scope_type,
                "scope_key": draft.scope_key,
                "memory_type": draft.memory_type,
                "classification": draft.classification,
                "content": draft.content,
                "created_by": draft.created_by,
            },
            risk_reason="Commit user-dictated content to shared enterprise memory.",
            non_interactive_approved=yes,
            resource=resource,
        )

    try:
        result = render_or_commit_shared_draft(config, draft, execute=execute)
    except (RuntimeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    if result.executed:
        _audit().write(
            "memory.shared_draft_committed",
            backend=result.backend,
            draft_id=draft.draft_id,
            tenant_id=draft.tenant_id,
        )
        console.print(f"Committed shared memory draft: {draft.draft_id}")
        return

    if result.statement is None:
        raise typer.BadParameter("Shared memory dry run did not produce a statement.")

    _audit().write(
        "memory.shared_draft_sql_rendered",
        backend=result.backend,
        draft_id=draft.draft_id,
        tenant_id=draft.tenant_id,
    )
    console.print_json(
        data={
            "backend": result.backend,
            "draft_id": draft.draft_id,
            "execute": False,
            "sql": result.statement.sql,
            "params": jsonable_mapping(result.statement.params),
        }
    )


@memory_app.command("lifecycle")
def memory_lifecycle(
    memory_id: Annotated[str, typer.Argument(help="Shared memory id.")],
    action: Annotated[
        str,
        typer.Option(
            "--action",
            help="Lifecycle action: correct, delete, expire, hold, or release_hold.",
        ),
    ],
    reason: Annotated[str, typer.Option("--reason", help="Required operator reason.")],
    tenant_id: Annotated[
        str | None,
        typer.Option("--tenant-id", help="Shared memory tenant. Defaults to active identity."),
    ] = None,
    content: Annotated[
        str | None,
        typer.Option("--content", help="Replacement content for correction."),
    ] = None,
    expires_at: Annotated[
        str | None,
        typer.Option("--expires-at", help="ISO-8601 expiration time for expire."),
    ] = None,
    operation_id: Annotated[
        str | None,
        typer.Option(
            "--operation-id",
            help="UUID to reuse when retrying a partial lifecycle operation.",
        ),
    ] = None,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Execute instead of rendering the backend operation."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Non-interactive approval when policy permits."),
    ] = False,
) -> None:
    """Correct, delete, expire, hold, or release a shared memory."""
    action = action.replace("-", "_")
    allowed_actions = {"correct", "delete", "expire", "hold", "release_hold"}
    if action not in allowed_actions:
        raise typer.BadParameter("Unsupported lifecycle action.")
    config = load_config()
    identity = _identity()
    resolved_tenant = _shared_memory_tenant(config, tenant_id)
    if content is not None:
        _enforce_safe_content(content, context="memory.shared.lifecycle")
    expiration: datetime | None = None
    if expires_at:
        try:
            expiration = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise typer.BadParameter("expires-at must be ISO-8601.") from error
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=UTC)
    normalized_operation_id: str | None = None
    if operation_id:
        try:
            normalized_operation_id = str(UUID(operation_id))
        except ValueError as error:
            raise typer.BadParameter("operation-id must be a UUID.") from error
    request_values: dict[str, Any] = {}
    if normalized_operation_id is not None:
        request_values["event_id"] = normalized_operation_id
    request = SharedMemoryLifecycleRequest(
        memory_id=memory_id,
        tenant_id=resolved_tenant,
        action=action,  # type: ignore[arg-type]
        actor=identity.subject,
        reason=reason,
        content=content,
        summary=prompt_preview(content, limit=120) if content else None,
        expires_at=expiration,
        **request_values,
    )
    if execute:
        resource = memory_resource(
            operation=action,
            tenant=resolved_tenant,
            scope_type="memory",
            scope_key=memory_id,
            backend=config.memory.shared.backend,
        )
        _authorize_cli_action(
            tool="shared_memory",
            action=action,
            target=f"{resolved_tenant}/{memory_id}",
            arguments={
                "memory_id": memory_id,
                "tenant_id": resolved_tenant,
                "action": action,
                "reason": reason,
                "content": content,
                "expires_at": expiration.isoformat() if expiration else None,
            },
            risk_reason="Change governed shared-memory lifecycle state.",
            non_interactive_approved=yes,
            resource=resource,
        )
    try:
        result = apply_shared_memory_lifecycle(config, request, execute=execute)
    except (PermissionError, RuntimeError, ValueError) as error:
        retry = (
            f" Retry the same operation with --operation-id {request.event_id}."
            if execute and config.memory.shared.backend == "iceberg"
            else ""
        )
        raise typer.BadParameter(f"{error}{retry}") from error
    _audit().write(
        "memory.shared_lifecycle",
        action=action,
        target=f"{resolved_tenant}/{memory_id}",
        tenant_id=resolved_tenant,
        backend=result.backend,
        executed=result.executed,
        reason=reason,
        operation_id=request.event_id,
    )
    if result.executed:
        console.print(f"Applied shared-memory lifecycle action: {action}")
        return
    console.print_json(
        data={
            "backend": result.backend,
            "execute": False,
            "action": action,
            "operation_id": request.event_id,
            "sql": result.statement.sql,
            "params": jsonable_mapping(result.statement.params),
        }
    )


@app.command("remember")
def remember(
    content: Annotated[str, typer.Argument(help="Memory content.")],
    local: Annotated[bool, typer.Option("--local", help="Write local memory.")] = False,
    shared: Annotated[
        bool, typer.Option("--shared", help="Write shared enterprise memory.")
    ] = False,
    tenant_id: Annotated[
        str | None,
        typer.Option("--tenant-id", help="Shared memory tenant. Defaults to active identity."),
    ] = None,
    scope_type: Annotated[
        str, typer.Option("--scope-type", help="Shared memory scope type.")
    ] = "org",
    scope_key: Annotated[
        str, typer.Option("--scope-key", help="Shared memory scope key.")
    ] = "default",
    memory_type: Annotated[str, typer.Option("--memory-type", help="Shared memory type.")] = "fact",
    classification: Annotated[
        str, typer.Option("--classification", help="Shared memory classification.")
    ] = "public-internal",
    created_by: Annotated[
        str | None,
        typer.Option("--created-by", help="Shared memory author. Defaults to active identity."),
    ] = None,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    """Explicitly write a local or shared memory."""
    config = load_config()
    _enforce_safe_content(
        content,
        context="memory.shared" if shared else "memory.local",
        allow_sensitive=allow_sensitive,
    )
    if shared:
        identity = _identity()
        resolved_tenant = _shared_memory_tenant(config, tenant_id)
        draft = create_shared_memory_draft(
            content=content,
            tenant_id=resolved_tenant,
            scope_type=scope_type,
            scope_key=scope_key,
            memory_type=memory_type,
            classification=classification,
            created_by=created_by or identity.subject,
            retention_days=config.memory.shared.retention_days,
        )
        _shared_draft_store(config).stage(draft)
        _audit().write(
            "memory.shared_draft_staged",
            draft_id=draft.draft_id,
            tenant_id=draft.tenant_id,
            scope_type=draft.scope_type,
            scope_key=draft.scope_key,
            prompt_preview=prompt_preview(content),
        )
        console.print(
            f"Staged shared memory draft: {draft.draft_id}\n"
            "Review it with `loro memory drafts`, then explicitly commit it."
        )
        return
    if local or not shared:
        store = LocalMemoryStore.from_config(config.memory.local, config.safety)
        memory = store.remember(content, allow_sensitive=allow_sensitive)
        _audit().write(
            "memory.local_written",
            memory_id=memory.memory_id,
            scope=memory.scope,
            content_preview=prompt_preview(content),
        )
        console.print(f"Saved local memory: {memory.memory_id}")


@docs_app.command("create")
def docs_create(
    prompt: Annotated[str, typer.Argument(help="Document prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _create_and_print_artifact(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        context="artifact.document",
        factory=create_document_artifact,
    )


@slides_app.command("create")
def slides_create(
    prompt: Annotated[str, typer.Argument(help="Presentation prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _create_and_print_artifact(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        context="artifact.presentation",
        factory=create_presentation_artifact,
    )


@sheets_app.command("analyze")
def sheets_analyze(
    prompt: Annotated[str, typer.Argument(help="Spreadsheet prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _create_and_print_artifact(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        context="artifact.spreadsheet",
        factory=create_spreadsheet_artifact,
    )


@sheets_app.command("create")
def sheets_create(
    prompt: Annotated[str, typer.Argument(help="Spreadsheet prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _create_and_print_artifact(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        context="artifact.spreadsheet",
        factory=create_spreadsheet_artifact,
    )


@brief_app.command("meeting")
def brief_meeting(
    prompt: Annotated[str, typer.Argument(help="Meeting brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _create_and_print_brief(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        brief_type="meeting",
    )


@brief_app.command("project")
def brief_project(
    prompt: Annotated[str, typer.Argument(help="Project brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _create_and_print_brief(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        brief_type="project",
    )


@brief_app.command("incident")
def brief_incident(
    prompt: Annotated[str, typer.Argument(help="Incident brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _create_and_print_brief(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        brief_type="incident",
    )


@brief_app.command("executive")
def brief_executive(
    prompt: Annotated[str, typer.Argument(help="Executive brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _create_and_print_brief(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        brief_type="executive",
    )


@data_app.callback()
def data_options(
    context: typer.Context,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Non-interactive approval for ask-gated governed-data discovery.",
        ),
    ] = False,
) -> None:
    """Configure governed-data command approval behavior."""
    context.ensure_object(dict)
    context.obj["yes"] = yes


@data_app.command("catalogs")
def data_catalogs() -> None:
    """List Polaris catalogs through the typed client."""
    _run_polaris_result(_polaris_client().list_catalogs())


@data_app.command("catalog")
def data_catalog(catalog: Annotated[str, typer.Argument(help="Catalog name.")]) -> None:
    """Describe one Polaris catalog through the typed client."""
    _run_polaris_result(_polaris_client().get_catalog(catalog))


@data_app.command("namespaces")
def data_namespaces(
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris namespaces through the typed client."""
    _run_polaris_result(_polaris_client().list_namespaces(catalog=catalog))


@data_app.command("namespace")
def data_namespace(
    namespace: Annotated[str, typer.Argument(help="Namespace name.")],
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Describe one Polaris namespace through the typed client."""
    _run_polaris_result(_polaris_client().get_namespace(namespace, catalog=catalog))


@data_app.command("tables")
def data_tables(
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris tables through the typed client."""
    _run_polaris_result(_polaris_client().list_tables(namespace=namespace, catalog=catalog))


@data_app.command("table")
def data_table(
    table: Annotated[str, typer.Argument(help="Table name.")],
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Describe one Polaris table through the typed client."""
    _run_polaris_result(_polaris_client().get_table(table, namespace=namespace, catalog=catalog))


@data_app.command("schema")
def data_schema(
    table: Annotated[str, typer.Argument(help="Table name.")],
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Inspect a governed table schema through Polaris metadata."""
    result = inspect_table_schema(
        _polaris_client(),
        table=table,
        namespace=namespace,
        catalog=catalog,
    )
    _audit().write(
        "data.schema_inspected",
        table=table,
        namespace=namespace,
        catalog=catalog,
        ok=result.ok,
    )
    console.print_json(data=result.to_payload())
    raise typer.Exit(code=0 if result.ok else 1)


@data_app.command("explain-access")
def data_explain_access(
    resource: Annotated[str, typer.Argument(help="Table or resource identifier.")],
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
    catalog_role: Annotated[
        str | None,
        typer.Option("--catalog-role", help="Catalog role to inspect privileges for."),
    ] = None,
) -> None:
    """Explain read-only Polaris discovery results for a governed resource."""
    result = explain_access(
        _polaris_client(),
        resource=resource,
        namespace=namespace,
        catalog=catalog,
        catalog_role=catalog_role,
    )
    _audit().write(
        "data.access_explained",
        resource=resource,
        namespace=namespace,
        catalog=catalog,
        catalog_role=catalog_role,
        ok=result.ok,
    )
    console.print_json(data=result.to_payload())
    raise typer.Exit(code=0 if result.ok else 1)


@data_app.command("views")
def data_views(
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris views through the typed client."""
    _run_polaris_result(_polaris_client().list_views(namespace=namespace, catalog=catalog))


@data_app.command("view")
def data_view(
    view: Annotated[str, typer.Argument(help="View name.")],
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Describe one Polaris view through the typed client."""
    _run_polaris_result(_polaris_client().get_view(view, namespace=namespace, catalog=catalog))


@data_app.command("principal-roles")
def data_principal_roles() -> None:
    """List Polaris principal roles through the typed client."""
    _run_polaris_result(_polaris_client().list_principal_roles())


@data_app.command("principal-role")
def data_principal_role(role: Annotated[str, typer.Argument(help="Principal role name.")]) -> None:
    """Describe one Polaris principal role through the typed client."""
    _run_polaris_result(_polaris_client().get_principal_role(role))


@data_app.command("catalog-roles")
def data_catalog_roles(
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris catalog roles through the typed client."""
    _run_polaris_result(_polaris_client().list_catalog_roles(catalog=catalog))


@data_app.command("catalog-role")
def data_catalog_role(
    role: Annotated[str, typer.Argument(help="Catalog role name.")],
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Describe one Polaris catalog role through the typed client."""
    _run_polaris_result(_polaris_client().get_catalog_role(role, catalog=catalog))


@data_app.command("privileges")
def data_privileges(
    catalog_role: Annotated[
        str | None,
        typer.Option("--catalog-role", help="Catalog role name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris privileges through the typed client."""
    _run_polaris_result(
        _polaris_client().list_privileges(catalog_role=catalog_role, catalog=catalog)
    )


@data_app.command("policies")
def data_policies(
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris policies through the typed client."""
    _run_polaris_result(_polaris_client().list_policies(catalog=catalog))


@data_app.command("policy")
def data_policy(
    policy: Annotated[str, typer.Argument(help="Policy name.")],
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Describe one Polaris policy through the typed client."""
    _run_polaris_result(_polaris_client().get_policy(policy, catalog=catalog))


@data_app.command("applicable-policies")
def data_applicable_policies(
    resource: Annotated[str, typer.Argument(help="Resource identifier.")],
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
) -> None:
    """List Polaris policies applicable to a resource through the typed client."""
    _run_polaris_result(
        _polaris_client().list_applicable_policies(
            resource,
            catalog=catalog,
            namespace=namespace,
        )
    )


@data_app.command("polaris")
def data_polaris(
    args: Annotated[list[str], typer.Argument(help="Read-only Polaris CLI arguments.")],
) -> None:
    """Run a read-only Polaris CLI operation through Loro's wrapper."""
    _run_polaris_result(_polaris_client().run_readonly(args))


@file_app.command("read")
def file_read(
    path: Annotated[Path, typer.Argument(help="File path to read.")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum characters to print.")] = 20000,
) -> None:
    """Read a text file."""
    config = load_config()
    resource = filesystem_resource(
        path,
        operation="read",
        workspace_roots=config.permissions.workspace_roots,
    )
    path = Path(str(resource.fields["path"]))
    _permissions().require_allowed(
        PermissionRequest(
            tool="edit",
            action="read file",
            target=str(path),
            resource=resource,
        ),
        approved=True,
    )
    text = FileTools().read_text(path, limit=limit)
    _audit().write("file.read", path=str(path), limit=limit)
    console.print(text)


@file_app.command("search")
def file_search(
    query: Annotated[str, typer.Argument(help="Text to search for.")],
    root: Annotated[Path, typer.Option("--root", "-r", help="Directory to search.")] = Path("."),
    limit: Annotated[int, typer.Option("--limit", help="Maximum matches to print.")] = 50,
) -> None:
    """Search local text files."""
    config = load_config()
    resource = filesystem_resource(
        root,
        operation="search",
        workspace_roots=config.permissions.workspace_roots,
    )
    root = Path(str(resource.fields["path"]))
    _permissions().require_allowed(
        PermissionRequest(
            tool="edit",
            action="search files",
            target=str(root),
            resource=resource,
        ),
        approved=True,
    )
    matches = FileTools().search(root=root, query=query, limit=limit)
    _audit().write("file.search", query=query, root=str(root), match_count=len(matches))
    if not matches:
        console.print("No matches.")
        return
    for match in matches:
        console.print(f"{match.path}:{match.line_number}: {match.line}")


@shell_app.command("run")
def shell_run(
    args: Annotated[list[str], typer.Argument(help="Command and arguments to execute.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Approve ask-gated shell execution."),
    ] = False,
    timeout: Annotated[int, typer.Option("--timeout", help="Timeout in seconds.")] = 120,
) -> None:
    """Run a shell command without invoking a shell interpreter."""
    if not args:
        raise typer.BadParameter("Provide a command to execute.")
    resource = shell_resource(args)
    _authorize_cli_action(
        tool="shell",
        action="run command",
        target=args[0],
        arguments={"args": args, "timeout": timeout},
        risk_reason="Execute a subprocess with the displayed arguments.",
        non_interactive_approved=yes,
        resource=resource,
    )
    config = load_config()
    result = ShellTools(
        config.sandbox,
        workspace_roots=config.permissions.workspace_roots,
    ).run(args, timeout=timeout)
    _audit().write(
        "shell.executed",
        args=result.args,
        returncode=result.returncode,
        timeout=timeout,
        sandbox_profile=result.profile,
        sandbox_os_enforced=result.os_enforced,
        output_truncated=result.output_truncated,
    )
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)
    raise typer.Exit(code=result.returncode)


@sandbox_app.command("doctor")
def sandbox_doctor() -> None:
    """Report whether configured subprocess isolation profiles can be enforced."""
    config = load_config()
    report = SandboxRunner(
        config.sandbox,
        workspace_roots=config.permissions.workspace_roots,
    ).diagnose()
    console.print_json(data=report)
    profiles = report["profiles"]
    if isinstance(profiles, dict) and not all(
        isinstance(profile, dict) and profile.get("ready") for profile in profiles.values()
    ):
        raise typer.Exit(code=1)


@safety_app.command("scan")
def safety_scan(
    text: Annotated[str | None, typer.Argument(help="Text to scan.")] = None,
    file: Annotated[Path | None, typer.Option("--file", "-f", help="File to scan.")] = None,
    surface: Annotated[
        str, typer.Option("--surface", help="Managed content surface to evaluate.")
    ] = "artifact",
) -> None:
    """Classify content and evaluate its managed data-protection policy."""
    if text is None and file is None:
        raise typer.BadParameter("Provide text or --file.")
    content = file.read_text(encoding="utf-8") if file else text or ""
    if surface not in load_config().safety.surfaces:
        raise typer.BadParameter(f"Unknown data-protection surface: {surface}")
    decision = _data_protection().evaluate(content, surface)  # type: ignore[arg-type]
    findings = list(decision.findings)
    _audit().write(
        "safety.scan",
        source=str(file) if file else "argument",
        finding_count=len(findings),
        finding_kinds=sorted({finding.kind for finding in findings}),
        data_protection=decision.metadata(),
    )
    console.print(
        f"Classification: {decision.classification}; action: {decision.action}; "
        f"surface: {decision.surface}."
    )
    if not findings:
        console.print("No sensitive patterns detected.")
        if decision.blocked:
            raise typer.Exit(code=1)
        return
    for finding in findings:
        console.print(
            f"- {finding.kind}: {finding.snippet} ({finding.start}-{finding.end})",
            markup=False,
        )
    raise typer.Exit(code=1)


@safety_app.command("doctor")
def safety_doctor() -> None:
    """Report the effective managed data-protection policy."""
    config = load_config().safety
    console.print_json(
        data={
            "enabled": config.enabled,
            "default_classification": config.default_classification,
            "allow_sensitive_override": config.allow_sensitive_override,
            "custom_pattern_count": len(config.custom_patterns),
            "surfaces": {
                name: policy.model_dump() for name, policy in sorted(config.surfaces.items())
            },
        }
    )


@providers_app.command("list")
def providers_list() -> None:
    """List built-in provider profiles."""
    for name in provider_names():
        profile = get_provider_profile(name)
        console.print(
            f"- [bold]{name}[/bold]: {profile.display_name} "
            f"({profile.protocol}, default={profile.default_model})"
        )


@providers_app.command("show")
def providers_show(provider: Annotated[str, typer.Argument(help="Provider name.")]) -> None:
    """Show one provider profile."""
    try:
        profile = get_provider_profile(provider)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(
        data={
            "name": profile.name,
            "display_name": profile.display_name,
            "default_model": profile.default_model,
            "small_model": profile.small_model,
            "aliases": list(profile.aliases),
            "api_key_env": profile.api_key_env,
            "base_url": profile.base_url,
            "protocol": profile.protocol,
            "optional_header_env": dict(profile.optional_header_env),
            "notes": profile.notes,
        }
    )


@providers_app.command("check")
def providers_check(
    provider: Annotated[
        str | None,
        typer.Argument(help="Provider name. Defaults to configured provider."),
    ] = None,
) -> None:
    """Check provider config and required environment variables."""
    config = load_config()
    model_config = config.model
    if provider:
        profile = get_provider_profile(provider)
        model_config = model_config_from_profile(
            profile.name,
            model=profile.default_model,
            small_model=profile.small_model,
        )
    check = check_provider_config(model_config)
    console.print_json(
        data={
            "provider": check.provider,
            "ok": check.ok,
            "api_key_env": check.api_key_env,
            "api_key_present": check.api_key_present,
            "credential_ref": check.credential_ref,
            "credential_present": check.credential_present,
            "base_url": check.base_url,
            "protocol": check.protocol,
            "messages": check.messages,
        }
    )
    raise typer.Exit(code=0 if check.ok else 1)


@providers_app.command("request")
def providers_request(
    prompt: Annotated[str, typer.Argument(help="Prompt to build a request for.")],
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name. Defaults to configured provider."),
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Model override.")] = None,
    base_url: Annotated[str | None, typer.Option("--base-url", help="Base URL override.")] = None,
) -> None:
    """Print a redacted model request without sending it."""
    config = load_config()
    model_config = config.model
    if provider:
        profile = get_provider_profile(provider)
        model_config = model_config_from_profile(
            profile.name,
            model=model or profile.default_model,
            small_model=profile.small_model,
            base_url=base_url,
        )
    elif model or base_url:
        model_config.model = model or model_config.model
        model_config.base_url = base_url or model_config.base_url
    client = create_model_client(model_config)
    request = client.build_request([ModelMessage(role="user", content=prompt)])
    console.print_json(data=redact_model_request(request))


@providers_app.command("smoke")
def providers_smoke(
    prompt: Annotated[
        str,
        typer.Argument(help="Prompt for the smoke request."),
    ] = "Reply with ok.",
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name. Defaults to configured provider."),
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Model override.")] = None,
    base_url: Annotated[str | None, typer.Option("--base-url", help="Base URL override.")] = None,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Actually send the request. Default is dry-run."),
    ] = False,
    stream: Annotated[
        bool,
        typer.Option("--stream", help="Use the streaming interface when executing."),
    ] = False,
) -> None:
    """Build or execute a provider smoke request."""
    config = load_config()
    model_config = config.model
    if provider:
        profile = get_provider_profile(provider)
        model_config = model_config_from_profile(
            profile.name,
            model=model or profile.default_model,
            small_model=profile.small_model,
            base_url=base_url,
        )
    elif model or base_url:
        model_config.model = model or model_config.model
        model_config.base_url = base_url or model_config.base_url
    result = smoke_model_client(model_config, prompt=prompt, execute=execute, stream=stream)
    _audit().write(
        "provider.smoke",
        provider=model_config.provider,
        model=model_config.model,
        execute=execute,
        stream=stream,
        ok=result.get("ok") if execute else None,
    )
    console.print_json(data=result)
    if execute and not result.get("ok", False):
        raise typer.Exit(code=1)


@sessions_app.command("list")
def sessions_list() -> None:
    """List saved sessions."""
    store = SessionStore(load_config().sessions)
    records = store.list()
    if not records:
        console.print("No saved sessions yet.")
        return
    for record in records:
        console.print(
            f"- [bold]{record['session_id']}[/bold] "
            f"({record['mode']}, {record['created_at']}): {record['prompt']}"
        )


@sessions_app.command("show")
def sessions_show(session_id: Annotated[str, typer.Argument(help="Session ID.")]) -> None:
    """Show a saved session."""
    store = SessionStore(load_config().sessions)
    try:
        record = store.get(session_id)
    except FileNotFoundError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=record)


@sessions_app.command("send")
def sessions_send(
    sender_session_id: Annotated[str, typer.Argument(help="Sending session ID.")],
    recipient_session_id: Annotated[str, typer.Argument(help="Recipient session ID.")],
    content: Annotated[str, typer.Argument(help="Coordination message.")],
    yes: Annotated[
        bool, typer.Option("--yes", help="Use an allowed non-interactive approval.")
    ] = False,
    allow_sensitive: Annotated[
        bool, typer.Option("--allow-sensitive", help="Allow policy-approved sensitive content.")
    ] = False,
) -> None:
    """Queue an untrusted, non-authoritative message for another session."""
    _enforce_safe_content(content, "session message", allow_sensitive)
    resource = session_message_resource(
        operation="send",
        sender_session_id=sender_session_id,
        recipient_session_id=recipient_session_id,
        message_digest=message_digest(content),
    )
    _authorize_cli_action(
        tool="session_message",
        action="send",
        target=resource.target,
        arguments={
            "sender_session_id": sender_session_id,
            "recipient_session_id": recipient_session_id,
            "content_digest": message_digest(content),
        },
        risk_reason="Send untrusted coordination context to another Loro session.",
        non_interactive_approved=yes,
        resource=resource,
    )
    try:
        config = load_config()
        message = SessionMailbox(config.sessions, config.safety).send(
            sender_session_id=sender_session_id,
            recipient_session_id=recipient_session_id,
            content=content,
            allow_sensitive=allow_sensitive,
        )
    except (FileNotFoundError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "session.message_queued",
        message_id=message.message_id,
        sender_session_id=sender_session_id,
        recipient_session_id=recipient_session_id,
        content_digest=message_digest(content),
        carries_user_authority=False,
    )
    console.print_json(data=message.to_payload())


@sessions_app.command("inbox")
def sessions_inbox(
    session_id: Annotated[str, typer.Argument(help="Recipient session ID.")],
    include_acknowledged: Annotated[
        bool, typer.Option("--all", help="Include acknowledged messages.")
    ] = False,
) -> None:
    """Inspect queued and delivered cross-session messages."""
    messages = SessionMailbox(load_config().sessions).list(
        session_id, include_acknowledged=include_acknowledged
    )
    console.print_json(data=[message.to_payload() for message in messages])


@sessions_app.command("ack")
def sessions_ack(
    session_id: Annotated[str, typer.Argument(help="Recipient session ID.")],
    message_id: Annotated[str, typer.Argument(help="Delivered message ID.")],
) -> None:
    """Acknowledge a delivered cross-session message."""
    try:
        message = SessionMailbox(load_config().sessions).acknowledge(session_id, message_id)
    except (FileNotFoundError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=message.to_payload())


@sessions_app.command("wake")
def sessions_wake(
    session_id: Annotated[str, typer.Argument(help="Saved recipient session ID.")],
    prompt: Annotated[
        str,
        typer.Option(help="Trusted user instruction accompanying queued messages."),
    ] = "Process the queued coordination messages and continue safely.",
) -> None:
    """Explicitly resume a stopped session and deliver its queued messages."""
    try:
        result = _runtime().run(prompt, mode="run", session_id=session_id)
    except FileNotFoundError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("session.woken", session_id=session_id, stop_reason=result.stop_reason)
    console.print(result.summary)


@skills_app.command("list")
def skills_list() -> None:
    """List validated skill metadata without loading instruction bodies."""
    try:
        skills = SkillRegistry(load_config().skills).discover()
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=[skill.to_payload() for skill in skills])


@skills_app.command("show")
def skills_show(name: Annotated[str, typer.Argument(help="Skill name.")]) -> None:
    """Load one enabled skill after validation and show its provenance."""
    try:
        skill = SkillRegistry(load_config().skills).load(name)
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data={**skill.metadata.to_payload(), "instructions": skill.instructions})


@skills_app.command("validate")
def skills_validate(
    source: Annotated[Path, typer.Argument(help="Skill package directory.")],
) -> None:
    """Validate a skill package and print its review digest."""
    config = load_config().skills.model_copy(
        update={"project_paths": [str(source.parent)], "allow_user": False}
    )
    try:
        skill = next(
            item
            for item in SkillRegistry(config).discover()
            if item.path.resolve() == source.resolve()
        )
    except (SkillError, StopIteration) as error:
        raise typer.BadParameter(str(error) or f"Skill package not found: {source}") from error
    console.print_json(data=skill.to_payload())


def _skills_import_compatibility(
    source: Path,
    *,
    kind: str,
    expected_digest: str | None,
    scope: str,
    include_mcp: bool,
    execute: bool,
    output: Path,
) -> None:
    if scope not in {"user", "project"}:
        raise typer.BadParameter("Skill scope must be user or project.")
    config = load_config()
    try:
        report = inspect_compatibility(source, kind, config.skills)  # type: ignore[arg-type]
    except SkillCompatibilityError as error:
        raise typer.BadParameter(str(error)) from error
    if not execute:
        console.print_json(data=report.to_payload())
        return
    if not expected_digest:
        raise typer.BadParameter("Compatibility import requires --expected-digest with --execute.")
    resolved_config = config.model_copy(deep=True)
    installed = []
    try:
        mcp_servers = apply_mcp_import(resolved_config, report) if include_mcp else []
        installed = import_compatible_skills(
            report,
            SkillRegistry(config.skills),
            expected_digest=expected_digest,
            scope=scope,  # type: ignore[arg-type]
        )
        if mcp_servers:
            write_config_sections(output, resolved_config, ["mcp"])
    except (OSError, SkillError) as error:
        for skill in installed:
            shutil.rmtree(skill.path, ignore_errors=True)
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "skill.compatibility_imported",
        kind=kind,
        source=str(source),
        source_digest=report.digest,
        skills=[skill.name for skill in installed],
        mcp_servers=mcp_servers,
    )
    console.print_json(
        data={
            "kind": kind,
            "source_digest": report.digest,
            "installed_skills": [skill.to_payload() for skill in installed],
            "imported_mcp_servers": mcp_servers,
            "unsupported_components": list(report.unsupported_components),
        }
    )


@skills_app.command("import-claude")
def skills_import_claude(
    source: Annotated[Path, typer.Argument(help="Claude skill or plugin directory.")],
    expected_digest: Annotated[
        str | None,
        typer.Option("--expected-digest", help="Digest from the preview report."),
    ] = None,
    scope: Annotated[str, typer.Option(help="Install scope: user or project.")] = "project",
    include_mcp: Annotated[
        bool,
        typer.Option("--include-mcp", help="Import compatible reviewed MCP definitions."),
    ] = False,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Install after digest review; preview is the default."),
    ] = False,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file updated when MCP import is enabled."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Preview or import compatible skills from a Claude skill or plugin."""
    _skills_import_compatibility(
        source,
        kind="claude",
        expected_digest=expected_digest,
        scope=scope,
        include_mcp=include_mcp,
        execute=execute,
        output=output,
    )


@skills_app.command("import-pi")
def skills_import_pi(
    source: Annotated[Path, typer.Argument(help="Pi skill or package directory.")],
    expected_digest: Annotated[
        str | None,
        typer.Option("--expected-digest", help="Digest from the preview report."),
    ] = None,
    scope: Annotated[str, typer.Option(help="Install scope: user or project.")] = "project",
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Install after digest review; preview is the default."),
    ] = False,
) -> None:
    """Preview or import compatible skills from a Pi skill or package."""
    _skills_import_compatibility(
        source,
        kind="pi",
        expected_digest=expected_digest,
        scope=scope,
        include_mcp=False,
        execute=execute,
        output=Path(".loro/config.local.toml"),
    )


def _set_skill_state(name: str, state: str) -> None:
    try:
        skill = SkillRegistry(load_config().skills).set_state(name, state)  # type: ignore[arg-type]
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("skill.state_changed", name=name, state=state, digest=skill.digest)
    console.print_json(data=skill.to_payload())


@skills_app.command("enable")
def skills_enable(name: Annotated[str, typer.Argument(help="Skill name.")]) -> None:
    """Enable a validated skill package."""
    _set_skill_state(name, "enabled")


@skills_app.command("disable")
def skills_disable(name: Annotated[str, typer.Argument(help="Skill name.")]) -> None:
    """Disable a skill without removing its package."""
    _set_skill_state(name, "disabled")


@skills_app.command("quarantine")
def skills_quarantine(name: Annotated[str, typer.Argument(help="Skill name.")]) -> None:
    """Quarantine a skill until its digest is reviewed again."""
    _set_skill_state(name, "quarantined")


@skills_app.command("install")
def skills_install(
    source: Annotated[Path, typer.Argument(help="Reviewed local skill package.")],
    expected_digest: Annotated[
        str, typer.Option("--expected-digest", help="Digest printed by skills validate.")
    ],
    scope: Annotated[str, typer.Option(help="Install scope: user or project.")] = "project",
) -> None:
    """Install a local package only when its reviewed digest matches."""
    if scope not in {"user", "project"}:
        raise typer.BadParameter("Skill scope must be user or project.")
    try:
        skill = SkillRegistry(load_config().skills).install(
            source,
            expected_digest=expected_digest,
            scope=scope,  # type: ignore[arg-type]
        )
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "skill.installed", name=skill.name, scope=scope, digest=skill.digest, source=str(source)
    )
    console.print_json(data=skill.to_payload())


@skills_app.command("remove")
def skills_remove(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm package removal.")] = False,
) -> None:
    """Remove a user or project skill package."""
    if not yes:
        raise typer.BadParameter("Skill removal requires --yes.")
    try:
        removed = SkillRegistry(load_config().skills).remove(name)
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("skill.removed", name=name, path=str(removed))
    console.print(f"Removed: {removed}")


@skills_app.command("propose")
def skills_propose(
    source: Annotated[Path, typer.Argument(help="Skill package directory.")],
) -> None:
    """Stage an immutable skill proposal for explicit review."""
    try:
        proposal = SkillRegistry(load_config().skills).propose(source)
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("skill.proposed", proposal_id=proposal.name, source=str(source))
    console.print_json(data={"proposal_id": proposal.name, "path": str(proposal)})


@skills_app.command("review")
def skills_review(
    proposal_id: Annotated[str, typer.Argument(help="Proposal ID.")],
    accept: Annotated[
        bool, typer.Option("--accept", help="Install the reviewed proposal.")
    ] = False,
    reject: Annotated[bool, typer.Option("--reject", help="Reject the proposal.")] = False,
) -> None:
    """Accept or reject a staged skill proposal exactly once."""
    if accept == reject:
        raise typer.BadParameter("Choose exactly one of --accept or --reject.")
    try:
        result = SkillRegistry(load_config().skills).review(proposal_id, accept=accept)
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("skill.reviewed", proposal_id=proposal_id, accepted=accept)
    console.print_json(data=result)


def _comma_separated(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
