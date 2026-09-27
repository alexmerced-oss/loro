"""`loro setup`: guided configuration wizards (provider, identity, policy, audit)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from loro.audit import AuditLogger
from loro.cli._common import _audit, _identity, console
from loro.cli.agents import profile_wizard, setup_agents
from loro.cli.core import configure
from loro.cli.gateway import gateway_setup
from loro.cli.setup_services import (
    _comma_separated,
    setup_mcp,
    setup_mcp_server,
    setup_memory,
    setup_polaris,
    setup_shared_memory,
    setup_webmcp,
)
from loro.config import (
    LoroConfig,
    load_config,
    write_config_sections,
)
from loro.identity import (
    diagnose_identity,
)

setup_app = typer.Typer(help="Guided setup wizards for Loro configuration.")


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
    model_discovery: Annotated[
        bool,
        typer.Option(
            "--discover-models/--no-discover-models",
            help="Load the provider's current model catalog during interactive setup.",
        ),
    ] = True,
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
        model_discovery=model_discovery,
        output=output,
    )


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


# Registered in one block so `loro setup --help` keeps its order.
setup_app.command("gateway")(gateway_setup)
setup_app.command("agents")(setup_agents)
setup_app.command("profile")(profile_wizard)
setup_app.command("provider")(setup_provider)
setup_app.command("memory")(setup_memory)
setup_app.command("shared-memory")(setup_shared_memory)
setup_app.command("polaris")(setup_polaris)
setup_app.command("identity")(setup_identity)
setup_app.command("approvals")(setup_approvals)
setup_app.command("skills")(setup_skills)
setup_app.command("sandbox")(setup_sandbox)
setup_app.command("audit")(setup_audit)
setup_app.command("quickstart")(setup_quickstart)
setup_app.command("mcp")(setup_mcp)
setup_app.command("webmcp")(setup_webmcp)
setup_app.command("mcp-server")(setup_mcp_server)
