from __future__ import annotations

import importlib.util
import os
import shutil
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from loro.config import MCPConfig, MCPServerConfig
from loro.mcp.security import MCPTransportPolicyError, credential_environment, enforce_server_policy


class MCPRegistryError(ValueError):
    """Raised when an MCP server cannot be resolved from configuration."""


class MCPRegistry:
    def __init__(self, config: MCPConfig) -> None:
        self.config = config

    def ids(self, *, enabled_only: bool = False) -> list[str]:
        return sorted(
            server_id
            for server_id, server in self.config.servers.items()
            if not enabled_only or server.enabled
        )

    def get(self, server_id: str, *, require_enabled: bool = True) -> MCPServerConfig:
        try:
            server = self.config.servers[server_id]
        except KeyError as error:
            raise MCPRegistryError(f"Unknown MCP server: {server_id}") from error
        if require_enabled and not self.config.enabled:
            raise MCPRegistryError("MCP is disabled. Set [mcp].enabled = true.")
        if require_enabled and not server.enabled:
            raise MCPRegistryError(f"MCP server is disabled: {server_id}")
        return server

    def payload(self, server_id: str) -> dict[str, Any]:
        server = self.get(server_id, require_enabled=False)
        return server_payload(server_id, server)

    def payloads(self) -> list[dict[str, Any]]:
        return [self.payload(server_id) for server_id in self.ids()]


def server_payload(server_id: str, server: MCPServerConfig) -> dict[str, Any]:
    return {
        "id": server_id,
        "enabled": server.enabled,
        "transport": server.transport,
        "endpoint": server_endpoint_for_display(server),
        "args": _redacted_args(server.args),
        "cwd": server.cwd,
        "env_allowlist": list(server.env_allowlist),
        "protocol_mode": server.protocol_mode,
        "allowed_protocol_versions": list(server.allowed_protocol_versions),
        "minimum_protocol_version": server.minimum_protocol_version,
        "timeout_seconds": server.timeout_seconds,
        "credential_profile": server.credential_profile,
        "extensions": list(server.extensions),
    }


def server_endpoint_for_display(server: MCPServerConfig) -> str:
    if server.transport == "stdio":
        return server.command or ""
    parsed = urlsplit(server.url or "")
    query = [
        (
            key,
            "[REDACTED]"
            if any(marker in key.casefold() for marker in ("token", "key", "secret", "password"))
            else value,
        )
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _redacted_args(args: list[str]) -> list[str]:
    result: list[str] = []
    redact_next = False
    for argument in args:
        if redact_next:
            result.append("[REDACTED]")
            redact_next = False
            continue
        normalized = argument.casefold().replace("_", "-")
        sensitive = any(
            marker in normalized
            for marker in ("password", "secret", "token", "api-key", "credential")
        )
        if sensitive and "=" in argument:
            result.append(argument.split("=", 1)[0] + "=[REDACTED]")
        else:
            result.append(argument)
            redact_next = sensitive and argument.startswith("-")
    return result


def diagnose_mcp(
    config: MCPConfig,
    server_id: str | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    from loro.mcp.extensions import MCPExtensionRegistry

    registry = MCPRegistry(config)
    extensions = MCPExtensionRegistry(config)
    environment = environ if environ is not None else dict(os.environ)
    issues: list[str] = []
    sdk_version = _mcp_sdk_version()
    if sdk_version is None:
        issues.append("MCP SDK is not installed. Install Loro with `pip install loro-agent[mcp]`.")

    if server_id is not None:
        try:
            server_ids = [server_id]
            registry.get(server_id, require_enabled=False)
        except MCPRegistryError as error:
            issues.append(str(error))
            server_ids = []
    else:
        server_ids = registry.ids()

    diagnostics: list[dict[str, Any]] = []
    for current_id in server_ids:
        server = registry.get(current_id, require_enabled=False)
        server_issues = _server_issues(config, server, environment)
        extension_statuses = [status.to_payload() for status in extensions.statuses(server)]
        for status in extension_statuses:
            if status["enabled"] and not status["active"]:
                server_issues.append(
                    f"extension {status['identifier']} is inactive: {status['reason']}"
                )
        diagnostics.append(
            {
                **server_payload(current_id, server),
                "extension_statuses": extension_statuses,
                "issues": server_issues,
            }
        )
        issues.extend(f"{current_id}: {issue}" for issue in server_issues)

    if config.enabled and not config.servers:
        issues.append("MCP is enabled but no servers are configured.")

    return {
        "ok": not issues,
        "enabled": config.enabled,
        "sdk_installed": sdk_version is not None,
        "sdk_version": sdk_version,
        "allowed_extensions": list(config.allowed_extensions),
        "servers": diagnostics,
        "issues": issues,
    }


def _server_issues(
    config: MCPConfig, server: MCPServerConfig, environ: dict[str, str]
) -> list[str]:
    issues: list[str] = []
    try:
        enforce_server_policy(config, server)
    except MCPTransportPolicyError as error:
        issues.append(str(error))
    if server.transport == "stdio":
        assert server.command is not None
        command = Path(server.command).expanduser()
        if command.parent != Path("."):
            if not command.exists():
                issues.append(f"stdio command does not exist: {command}")
        elif shutil.which(server.command) is None:
            issues.append(f"stdio command is not on PATH: {server.command}")
        if server.cwd and not Path(server.cwd).expanduser().is_dir():
            issues.append(f"stdio working directory does not exist: {server.cwd}")
        missing = [name for name in server.env_allowlist if name not in environ]
        if missing:
            issues.append(f"allowlisted environment variables are missing: {', '.join(missing)}")
    else:
        assert server.url is not None
        parsed = urlsplit(server.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            issues.append("Streamable HTTP URL must be an absolute http:// or https:// URL.")
        if parsed.username or parsed.password:
            issues.append("Streamable HTTP URL cannot contain credentials.")
        if server.credential_profile:
            profile = config.credential_profiles[server.credential_profile]
            try:
                credential_environment(profile, environ)
            except MCPTransportPolicyError as error:
                issues.append(str(error))
    return issues


def _mcp_sdk_version() -> str | None:
    if importlib.util.find_spec("mcp") is None:
        return None
    try:
        return version("mcp")
    except PackageNotFoundError:  # pragma: no cover - importable editable SDK
        return "unknown"
