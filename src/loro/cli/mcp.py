"""`loro mcp`: Model Context Protocol servers, tools, tasks, and extensions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated, Any

import typer

from loro.cli._common import _audit, _authorize_cli_action, console
from loro.config import (
    MCPCredentialProfileConfig,
    MCPExtensionConfig,
    MCPServerConfig,
    load_config,
    replace_config_section,
    write_config_sections,
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
from loro.mcp.registry import server_endpoint_for_display
from loro.permissions import PermissionEngine, PermissionRequest
from loro.resources import (
    mcp_resource,
)

mcp_app = typer.Typer(help="Configure and use Model Context Protocol servers.")


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
    awaitable: Coroutine[Any, Any, dict[str, Any]],
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
