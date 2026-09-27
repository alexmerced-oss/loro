"""`loro setup` wizards for memory, Polaris, MCP, and WebMCP services."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from loro.cli._common import _audit, console
from loro.config import (
    MCPExtensionConfig,
    MCPServerConfig,
    load_config,
    replace_config_section,
    write_config_sections,
)
from loro.mcp.extensions import TASKS_EXTENSION_ID


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


def setup_webmcp(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
    origins: Annotated[
        str,
        typer.Option(
            "--origins",
            help="Comma-separated exact HTTPS origins allowed for WebMCP navigation.",
        ),
    ] = "https://alexmerced.app",
) -> None:
    """Enable Loro's exact-origin, browser-backed WebMCP server."""
    from loro.webmcp_bridge import normalize_webmcp_origins

    allowed_origins = normalize_webmcp_origins(origins.split(","))
    config = load_config()
    server_id = "alexmerced-webmcp"
    browser_environment = [
        name
        for name in (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XAUTHORITY",
            "DBUS_SESSION_BUS_ADDRESS",
            "LORO_WEBMCP_HEADLESS",
            "LORO_WEBMCP_PROFILE",
        )
        if os.environ.get(name)
    ]
    servers = {
        **config.mcp.servers,
        server_id: MCPServerConfig(
            transport="stdio",
            command="loro-webmcp",
            args=["--origins", ",".join(allowed_origins)],
            env_allowlist=browser_environment,
            protocol_mode="auto",
            timeout_seconds=120,
        ),
    }
    allowed_commands = list(dict.fromkeys([*config.mcp.allowed_stdio_commands, "loro-webmcp"]))
    config.mcp = config.mcp.model_copy(
        update={
            "enabled": True,
            "servers": servers,
            "allowed_stdio_commands": allowed_commands,
        }
    )
    config.mcp = type(config.mcp).model_validate(config.mcp.model_dump())
    written = write_config_sections(output, config, ["mcp"])
    _audit().write(
        "config.webmcp_written",
        path=str(written),
        server_id=server_id,
        origins=list(allowed_origins),
    )
    console.print(f"Configured {server_id}: {written}")
    console.print("Install the browser once with: playwright install chromium")


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


def _comma_separated(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
