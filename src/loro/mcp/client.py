from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from loro.config import MCPConfig, MCPServerConfig
from loro.mcp.registry import MCPRegistry
from loro.mcp.security import (
    MCPTransportPolicyError,
    credential_environment,
    dynamic_registration_guard,
    enforce_server_policy,
)


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
            payload = {
                "connection": connected.info.to_payload(),
                "tools": await connected.list_tools(),
            }
            return _bounded(payload, self.config.max_output_bytes)

    async def call_tool(
        self, server_id: str, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            result = await connected.client.call_tool(name, arguments or {})
            payload = {
                "connection": connected.info.to_payload(),
                "result": _payload(result),
            }
            return _bounded(payload, self.config.max_output_bytes)

    async def list_resources(self, server_id: str) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            payload = {
                "connection": connected.info.to_payload(),
                "resources": await connected.list_resources(),
            }
            return _bounded(payload, self.config.max_output_bytes)

    async def read_resource(self, server_id: str, uri: str) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            result = await connected.client.read_resource(uri)
            payload = {
                "connection": connected.info.to_payload(),
                "result": _payload(result),
            }
            return _bounded(payload, self.config.max_output_bytes)

    async def list_prompts(self, server_id: str) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            payload = {
                "connection": connected.info.to_payload(),
                "prompts": await connected.list_prompts(),
            }
            return _bounded(payload, self.config.max_output_bytes)

    async def get_prompt(
        self, server_id: str, name: str, arguments: dict[str, str] | None = None
    ) -> dict[str, Any]:
        async with self.connect(server_id) as connected:
            result = await connected.client.get_prompt(name, arguments or {})
            payload = {
                "connection": connected.info.to_payload(),
                "result": _payload(result),
            }
            return _bounded(payload, self.config.max_output_bytes)

    @asynccontextmanager
    async def connect(self, server_id: str) -> AsyncIterator[ConnectedMCP]:
        server = self.registry.get(server_id)
        try:
            enforce_server_policy(self.config, server)
            async with self.client_factory(server) as client:
                protocol_version = str(client.protocol_version)
                _enforce_protocol_policy(server, protocol_version)
                info = MCPConnectionInfo(
                    server_id=server_id,
                    transport=server.transport,
                    protocol_version=protocol_version,
                    lifecycle=("stateless" if protocol_version >= "2026-07-28" else "classic"),
                    server_info=_optional_mapping(client.server_info),
                    capabilities=_mapping(client.server_capabilities),
                )
                yield ConnectedMCP(
                    client=client,
                    info=info,
                    max_pagination_pages=self.config.max_pagination_pages,
                    max_output_bytes=self.config.max_output_bytes,
                )
        except (MCPClientError, MCPTransportPolicyError):
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
            "input_required_max_rounds": (
                self.config.input_required_max_rounds
                if self.config.allow_input_required
                else 0
            ),
            "elicitation_callback": (
                self._elicitation_callback if self.config.allow_input_required else None
            ),
        }
        if server.transport == "streamable_http":
            assert server.url is not None
            return self._http_sdk_client(Client, server, kwargs)

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

    @asynccontextmanager
    async def _http_sdk_client(
        self, client_type: Any, server: MCPServerConfig, kwargs: dict[str, Any]
    ) -> AsyncIterator[Any]:
        try:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as error:  # pragma: no cover - covered by the main SDK import
            raise MCPDependencyError("The installed MCP SDK is incomplete.") from error

        assert server.url is not None
        profile = (
            self.config.credential_profiles.get(server.credential_profile)
            if server.credential_profile
            else None
        )
        auth = None
        headers: dict[str, str] = {}
        hooks: list[Callable[[Any], Any]] = []
        if profile is not None:
            values = credential_environment(profile, self.environ)
            if profile.type == "bearer":
                assert profile.token_env is not None
                headers["Authorization"] = f"Bearer {values[profile.token_env]}"
            else:
                auth = self._oauth_provider(server.url, profile, values)
                hooks.append(dynamic_registration_guard(profile.allow_dynamic_client_registration))
        timeout = httpx2.Timeout(server.timeout_seconds, read=server.timeout_seconds)
        http_client = httpx2.AsyncClient(
            headers=headers,
            auth=auth,
            timeout=timeout,
            follow_redirects=self.config.follow_redirects,
            event_hooks={"request": hooks},
        )
        async with http_client:
            transport = streamable_http_client(server.url, http_client=http_client)
            async with client_type(transport, **kwargs) as client:
                yield client

    def _oauth_provider(
        self,
        server_url: str,
        profile: Any,
        values: Mapping[str, str],
    ) -> Any:
        from mcp.client.auth import OAuthClientProvider
        from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
        from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

        storage = _MemoryTokenStorage()
        scope = " ".join(profile.scopes) or None
        if profile.type == "oauth_client_credentials":
            assert profile.client_id_env and profile.client_secret_env
            return ClientCredentialsOAuthProvider(
                server_url=server_url,
                storage=storage,
                client_id=values[profile.client_id_env],
                client_secret=values[profile.client_secret_env],
                scope=scope,
            )

        async def redirect_handler(url: str) -> None:
            if not sys.stdin.isatty():
                raise MCPClientError("MCP OAuth authorization requires an interactive terminal.")
            print(f"Open this MCP authorization URL:\n{url}", file=sys.stderr)

        async def callback_handler() -> Any:
            from urllib.parse import parse_qs, urlsplit

            import anyio

            callback = await anyio.to_thread.run_sync(
                lambda: input("Paste the final callback URL: ").strip()
            )
            query = parse_qs(urlsplit(callback).query)
            if query.get("error"):
                raise MCPClientError(f"MCP OAuth failed: {query['error'][0]}")
            code = query.get("code", [None])[0]
            if not code:
                raise MCPClientError("MCP OAuth callback did not contain a code.")
            return AuthorizationCodeResult(
                code=code,
                state=query.get("state", [None])[0],
            )

        metadata = OAuthClientMetadata(
            redirect_uris=[profile.redirect_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope=scope,
            client_name="Loro CLI",
        )
        return OAuthClientProvider(
            server_url=server_url,
            client_metadata=metadata,
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            client_metadata_url=profile.client_metadata_url,
        )

    async def _elicitation_callback(self, context: Any, params: Any) -> Any:
        from mcp import types

        protocol_version = str(getattr(context.session, "protocol_version", ""))
        if protocol_version < "2026-07-28":
            return types.ErrorData(
                code=types.INVALID_REQUEST,
                message="Classic MCP server callbacks are disabled by Loro policy.",
            )
        if not sys.stdin.isatty():
            return types.ErrorData(
                code=types.INVALID_REQUEST,
                message="MCP input requires an interactive terminal approval.",
            )
        import anyio

        message = str(getattr(params, "message", "MCP server requested input"))
        print(f"MCP input approval required:\n{message}", file=sys.stderr)
        choice = await anyio.to_thread.run_sync(
            lambda: input("Approve this input request? [y/N] ").strip().casefold()
        )
        if choice not in {"y", "yes"}:
            return types.ElicitResult(action="decline")
        if getattr(params, "mode", "form") == "url":
            print(f"Open this URL:\n{params.url}", file=sys.stderr)
            return types.ElicitResult(action="accept")
        raw = await anyio.to_thread.run_sync(
            lambda: input("Enter the requested values as a JSON object: ").strip()
        )
        try:
            content = json.loads(raw)
        except ValueError:
            return types.ErrorData(
                code=types.INVALID_PARAMS,
                message="MCP input response was not valid JSON.",
            )
        if not isinstance(content, dict):
            return types.ErrorData(
                code=types.INVALID_PARAMS,
                message="MCP input response must be a JSON object.",
            )
        return types.ElicitResult(action="accept", content=content)


@dataclass(frozen=True)
class ConnectedMCP:
    client: MCPClientLike
    info: MCPConnectionInfo
    max_pagination_pages: int = 20
    max_output_bytes: int = 1_000_000

    async def list_tools(self) -> list[dict[str, Any]]:
        return await _collect_pages(
            self.client.list_tools,
            "tools",
            self.max_pagination_pages,
            self.max_output_bytes,
        )

    async def list_resources(self) -> list[dict[str, Any]]:
        return await _collect_pages(
            self.client.list_resources,
            "resources",
            self.max_pagination_pages,
            self.max_output_bytes,
        )

    async def list_prompts(self) -> list[dict[str, Any]]:
        return await _collect_pages(
            self.client.list_prompts,
            "prompts",
            self.max_pagination_pages,
            self.max_output_bytes,
        )


async def _collect_pages(
    method: Callable[..., Any], field: str, max_pages: int, max_output_bytes: int
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(max_pages):
        result = await method(cursor=cursor)
        raw_items = getattr(result, field, None)
        if raw_items is None and isinstance(result, Mapping):
            raw_items = result.get(field, [])
        items.extend(_payload(item) for item in (raw_items or []))
        _bounded(items, max_output_bytes)
        cursor = getattr(result, "next_cursor", None)
        if cursor is None and isinstance(result, Mapping):
            cursor = result.get("nextCursor") or result.get("next_cursor")
        if not cursor:
            return items
    raise MCPProtocolError(f"MCP {field} pagination exceeded {max_pages} pages.")


class _MemoryTokenStorage:
    def __init__(self) -> None:
        self.tokens: Any = None
        self.client_info: Any = None

    async def get_tokens(self) -> Any:
        return self.tokens

    async def set_tokens(self, tokens: Any) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> Any:
        return self.client_info

    async def set_client_info(self, client_info: Any) -> None:
        self.client_info = client_info


def _bounded(value: Any, max_output_bytes: int) -> Any:
    size = len(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str).encode("utf-8")
    )
    if size > max_output_bytes:
        raise MCPProtocolError(f"MCP output exceeded managed limit of {max_output_bytes} bytes.")
    return value


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
