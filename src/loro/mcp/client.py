from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from loro.config import MCPConfig, MCPServerConfig
from loro.mcp.registry import MCPRegistry


class MCPClientError(RuntimeError):
    """Base error for MCP client failures."""


class MCPDependencyError(MCPClientError):
    """Raised when the optional MCP SDK is unavailable."""


class MCPProtocolError(MCPClientError):
    """Raised when protocol negotiation violates Loro policy."""


class MCPClientLike(Protocol):
    protocol_version: str
    server_info: Any
    server_capabilities: Any

    async def list_tools(self, *, cursor: str | None = None) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...

    async def list_resources(self, *, cursor: str | None = None) -> Any: ...

    async def read_resource(self, uri: str) -> Any: ...

    async def list_prompts(self, *, cursor: str | None = None) -> Any: ...

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> Any: ...


ClientContextFactory = Callable[[MCPServerConfig], Any]


@dataclass(frozen=True)
class MCPConnectionInfo:
    server_id: str
    transport: str
    protocol_version: str
    lifecycle: str
    server_info: dict[str, Any] | None
    capabilities: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "transport": self.transport,
            "protocol_version": self.protocol_version,
            "lifecycle": self.lifecycle,
            "server_info": self.server_info,
            "capabilities": self.capabilities,
        }


class MCPService:
    def __init__(
        self,
        config: MCPConfig,
        *,
        client_factory: ClientContextFactory | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.registry = MCPRegistry(config)
        self.environ = dict(environ if environ is not None else os.environ)
        self.client_factory = client_factory or self._sdk_client

    async def inspect_connection(self, server_id: str) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            return connected.info.to_payload()

    async def test_connection(self, server_id: str) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            tools = await connected.list_tools()
            resources = await connected.list_resources()
            prompts = await connected.list_prompts()
            return {
                "ok": True,
                "connection": connected.info.to_payload(),
                "counts": {
                    "tools": len(tools),
                    "resources": len(resources),
                    "prompts": len(prompts),
                },
            }

    async def list_tools(self, server_id: str) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            return {
                "connection": connected.info.to_payload(),
                "tools": await connected.list_tools(),
            }

    async def call_tool(
        self, server_id: str, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            result = await connected.client.call_tool(name, arguments or {})
            return {
                "connection": connected.info.to_payload(),
                "result": _payload(result),
            }

    async def list_resources(self, server_id: str) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            return {
                "connection": connected.info.to_payload(),
                "resources": await connected.list_resources(),
            }

    async def read_resource(self, server_id: str, uri: str) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            result = await connected.client.read_resource(uri)
            return {
                "connection": connected.info.to_payload(),
                "result": _payload(result),
            }

    async def list_prompts(self, server_id: str) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            return {
                "connection": connected.info.to_payload(),
                "prompts": await connected.list_prompts(),
            }

    async def get_prompt(
        self, server_id: str, name: str, arguments: dict[str, str] | None = None
    ) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            result = await connected.client.get_prompt(name, arguments or {})
            return {
                "connection": connected.info.to_payload(),
                "result": _payload(result),
            }

    @asynccontextmanager
    async def connect(self, server_id: str) -> AsyncIterator[ConnectedMCP]:
        server = self.registry.get(server_id)
        try:
            async with self.client_factory(server) as client:
                protocol_version = str(client.protocol_version)
                _enforce_protocol_policy(server, protocol_version)
                info = MCPConnectionInfo(
                    server_id=server_id,
                    transport=server.transport,
                    protocol_version=protocol_version,
                    lifecycle=(
                        "stateless" if protocol_version >= "2026-07-28" else "classic"
                    ),
                    server_info=_optional_mapping(client.server_info),
                    capabilities=_mapping(client.server_capabilities),
                )
                yield ConnectedMCP(client=client, info=info)
        except MCPClientError:
            raise
        except Exception as error:
            raise MCPClientError(f"MCP server {server_id} failed: {error}") from error

    def _sdk_client(self, server: MCPServerConfig) -> Any:
        try:
            from mcp import Client, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as error:
            raise MCPDependencyError(
                "MCP SDK is not installed. Install Loro with `pip install loro-agent[mcp]`."
            ) from error

        kwargs = {
            "read_timeout_seconds": server.timeout_seconds,
            "mode": server.protocol_mode,
            "raise_exceptions": True,
        }
        if server.transport == "streamable_http":
            assert server.url is not None
            return Client(server.url, **kwargs)

        assert server.command is not None
        missing = [name for name in server.env_allowlist if name not in self.environ]
        if missing:
            raise MCPClientError(
                "Allowlisted MCP environment variables are missing: " + ", ".join(missing)
            )
        environment = {name: self.environ[name] for name in server.env_allowlist}
        parameters = StdioServerParameters(
            command=server.command,
            args=list(server.args),
            env=environment,
            cwd=server.cwd,
        )
        return Client(stdio_client(parameters), **kwargs)


@dataclass(frozen=True)
class ConnectedMCP:
    client: MCPClientLike
    info: MCPConnectionInfo

    async def list_tools(self) -> list[dict[str, Any]]:
        return await _collect_pages(self.client.list_tools, "tools")

    async def list_resources(self) -> list[dict[str, Any]]:
        return await _collect_pages(self.client.list_resources, "resources")

    async def list_prompts(self) -> list[dict[str, Any]]:
        return await _collect_pages(self.client.list_prompts, "prompts")


async def _collect_pages(method: Callable[..., Any], field: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(20):
        result = await method(cursor=cursor)
        raw_items = getattr(result, field, None)
        if raw_items is None and isinstance(result, Mapping):
            raw_items = result.get(field, [])
        items.extend(_payload(item) for item in (raw_items or []))
        cursor = getattr(result, "next_cursor", None)
        if cursor is None and isinstance(result, Mapping):
            cursor = result.get("nextCursor") or result.get("next_cursor")
        if not cursor:
            return items
    raise MCPProtocolError(f"MCP {field} pagination exceeded 20 pages.")


def _enforce_protocol_policy(server: MCPServerConfig, protocol_version: str) -> None:
    if protocol_version not in server.allowed_protocol_versions:
        raise MCPProtocolError(
            f"Negotiated MCP protocol {protocol_version} is not allowed; allowed versions: "
            + ", ".join(server.allowed_protocol_versions)
        )
    if (
        server.minimum_protocol_version is not None
        and protocol_version < server.minimum_protocol_version
    ):
        raise MCPProtocolError(
            f"Negotiated MCP protocol {protocol_version} is below managed minimum "
            f"{server.minimum_protocol_version}."
        )


def _payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json", exclude_none=True)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {
            str(key): _json_value(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return {"value": _json_value(value)}


def _mapping(value: Any) -> dict[str, Any]:
    return _payload(value)


def _optional_mapping(value: Any) -> dict[str, Any] | None:
    return None if value is None else _mapping(value)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json", exclude_none=True)
    return str(value)
