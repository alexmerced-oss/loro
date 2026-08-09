from __future__ import annotations

import fnmatch
import ipaddress
import json
import shutil
import socket
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from loro.config import MCPConfig, MCPCredentialProfileConfig, MCPServerConfig


class MCPTransportPolicyError(ValueError):
    """Raised when an MCP endpoint violates managed transport policy."""


def enforce_server_policy(
    policy: MCPConfig,
    server: MCPServerConfig,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> None:
    if server.transport == "stdio":
        _enforce_stdio_policy(policy, server)
        return
    assert server.url is not None
    parsed = urlsplit(server.url)
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    is_loopback = _hostname_is_loopback(hostname)
    if (
        policy.require_https
        and parsed.scheme != "https"
        and not (policy.allow_loopback_http and is_loopback)
    ):
        raise MCPTransportPolicyError("MCP endpoint must use HTTPS by managed policy.")
    if policy.allowed_hosts and not any(
        fnmatch.fnmatchcase(hostname, pattern.rstrip(".").casefold())
        for pattern in policy.allowed_hosts
    ):
        raise MCPTransportPolicyError(f"MCP host is not allowlisted: {hostname}")
    if parsed.fragment:
        raise MCPTransportPolicyError("MCP endpoint URL cannot contain a fragment.")
    if policy.block_private_networks and not is_loopback:
        _reject_unsafe_addresses(hostname, parsed.port, resolver)


def credential_environment(
    profile: MCPCredentialProfileConfig, environ: Mapping[str, str]
) -> dict[str, str]:
    names = [profile.token_env, profile.client_id_env, profile.client_secret_env]
    missing = [name for name in names if name and name not in environ]
    if missing:
        raise MCPTransportPolicyError(
            "MCP credential environment variables are missing: " + ", ".join(missing)
        )
    return {name: environ[name] for name in names if name}


def dynamic_registration_guard(
    allow_dynamic_registration: bool,
) -> Callable[[Any], Awaitable[None]]:
    async def guard(request: Any) -> None:
        if allow_dynamic_registration or request.method.upper() != "POST":
            return
        content_type = request.headers.get("content-type", "").casefold()
        if "application/json" not in content_type:
            return
        try:
            payload = json.loads(request.content)
        except (TypeError, ValueError, UnicodeDecodeError):
            return
        if (
            isinstance(payload, dict)
            and ("redirect_uris" in payload or "grant_types" in payload)
            and "jsonrpc" not in payload
        ):
            raise MCPTransportPolicyError(
                "OAuth Dynamic Client Registration is disabled for this credential profile."
            )

    return guard


def _enforce_stdio_policy(policy: MCPConfig, server: MCPServerConfig) -> None:
    if not policy.allowed_stdio_commands:
        return
    assert server.command is not None
    resolved = shutil.which(server.command) or server.command
    candidates = {server.command, resolved}
    if not any(
        fnmatch.fnmatchcase(candidate, pattern)
        for candidate in candidates
        for pattern in policy.allowed_stdio_commands
    ):
        raise MCPTransportPolicyError(f"MCP stdio command is not allowlisted: {server.command}")


def _hostname_is_loopback(hostname: str) -> bool:
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _reject_unsafe_addresses(hostname: str, port: int | None, resolver: Callable[..., Any]) -> None:
    try:
        addresses = {
            item[4][0] for item in resolver(hostname, port or 443, type=socket.SOCK_STREAM)
        }
    except OSError as error:
        raise MCPTransportPolicyError(f"Unable to resolve MCP host {hostname}: {error}") from error
    if not addresses:
        raise MCPTransportPolicyError(f"MCP host did not resolve: {hostname}")
    unsafe = sorted(address for address in addresses if not ipaddress.ip_address(address).is_global)
    if unsafe:
        raise MCPTransportPolicyError(
            f"MCP host resolves to a non-public address blocked by policy: {hostname}"
        )
