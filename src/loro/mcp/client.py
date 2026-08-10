from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from loro.config import MCPConfig, MCPServerConfig, SandboxConfig
from loro.mcp.extensions import (
    TASKS_EXTENSION_ID,
    MCPExtensionError,
    MCPExtensionRegistry,
)
from loro.mcp.registry import MCPRegistry
from loro.mcp.security import (
    MCPTransportPolicyError,
    credential_environment,
    dynamic_registration_guard,
    enforce_server_policy,
    request_policy_hook,
)
from loro.mcp.tasks import MCPTaskError, MCPTaskStore
from loro.sandbox import SandboxLaunch, SandboxRunner


class MCPClientError(RuntimeError):
    """Base error for MCP client failures."""


class MCPDependencyError(MCPClientError):
    """Raised when the optional MCP SDK is unavailable."""


class MCPProtocolError(MCPClientError):
    """Raised when protocol negotiation violates Loro policy."""


class MCPTaskApprovalError(MCPClientError):
    """Raised when a task mutation lacks trusted user approval."""


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

    def listen(self, **kwargs: Any) -> Any: ...


ClientContextFactory = Callable[[MCPServerConfig], Any]


@dataclass(frozen=True)
class MCPConnectionInfo:
    server_id: str
    transport: str
    protocol_version: str
    lifecycle: str
    server_info: dict[str, Any] | None
    capabilities: dict[str, Any]
    extensions: list[str]
    sandbox_profile: str | None = None
    sandbox_os_enforced: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "transport": self.transport,
            "protocol_version": self.protocol_version,
            "lifecycle": self.lifecycle,
            "server_info": self.server_info,
            "capabilities": self.capabilities,
            "extensions": self.extensions,
            "sandbox_profile": self.sandbox_profile,
            "sandbox_os_enforced": self.sandbox_os_enforced,
        }


class MCPService:
    def __init__(
        self,
        config: MCPConfig,
        *,
        client_factory: ClientContextFactory | None = None,
        environ: Mapping[str, str] | None = None,
        task_store: MCPTaskStore | None = None,
        sandbox_config: SandboxConfig | None = None,
        workspace_roots: list[str] | None = None,
    ) -> None:
        self.config = config
        self.registry = MCPRegistry(config)
        self.environ = dict(environ if environ is not None else os.environ)
        self.extensions = MCPExtensionRegistry(config)
        self.task_store = task_store or MCPTaskStore.from_config(config)
        self.sandbox_config = sandbox_config or SandboxConfig()
        self.sandbox = SandboxRunner(
            self.sandbox_config,
            workspace_roots=workspace_roots,
            environ=self.environ,
        )
        self.client_factory = client_factory or self._sdk_client

    def extension_status(self, server_id: str | None = None) -> dict[str, Any]:
        server = self.registry.get(server_id, require_enabled=False) if server_id else None
        return {
            "server_id": server_id,
            "extensions": [status.to_payload() for status in self.extensions.statuses(server)],
        }

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

    async def start_task(
        self,
        server_id: str,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from loro.mcp.tasks import sdk_start_task

        server = self.registry.get(server_id)
        self.extensions.require_active(server, TASKS_EXTENSION_ID)
        async with self.connect(server_id) as connected:
            _require_remote_extension(connected, TASKS_EXTENSION_ID)
            if hasattr(connected.client, "start_task"):
                result = await connected.client.start_task(name, arguments or {})
            else:
                result = await sdk_start_task(connected.client, name, arguments or {})
            if result.get("resultType") != "task" and result.get("result_type") != "task":
                return _bounded(
                    {
                        "connection": connected.info.to_payload(),
                        "task_created": False,
                        "result": result,
                    },
                    self.config.max_output_bytes,
                )
            handle = self.task_store.record_remote(
                server_id=server_id,
                operation="tools/call",
                name=name,
                remote=result,
            )
            return _bounded(
                {
                    "connection": connected.info.to_payload(),
                    "task_created": True,
                    "task": handle.model_dump(mode="json"),
                },
                self.config.max_output_bytes,
            )

    async def get_task(self, server_id: str, task_id: str) -> dict[str, Any]:
        from loro.mcp.tasks import sdk_get_task

        local = self.task_store.get(server_id, task_id)
        server = self.registry.get(server_id)
        self.extensions.require_active(server, TASKS_EXTENSION_ID)
        async with self.connect(server_id) as connected:
            _require_remote_extension(connected, TASKS_EXTENSION_ID)
            if hasattr(connected.client, "get_task"):
                result = await connected.client.get_task(task_id)
            else:
                result = await sdk_get_task(connected.client, task_id)
            handle = self.task_store.record_remote(
                server_id=server_id,
                operation=local.operation,
                name=local.name,
                remote=result,
            )
            return _bounded(
                {
                    "connection": connected.info.to_payload(),
                    "task": handle.model_dump(mode="json"),
                },
                self.config.max_output_bytes,
            )

    async def update_task(
        self,
        server_id: str,
        task_id: str,
        input_responses: dict[str, Any],
        *,
        user_approved: bool = False,
    ) -> dict[str, Any]:
        from loro.mcp.tasks import sdk_update_task

        if not user_approved:
            raise MCPTaskApprovalError("MCP task input requires trusted user approval.")
        handle = self.task_store.get(server_id, task_id)
        if handle.status != "input_required":
            raise MCPClientError("MCP task is not waiting for input.")
        response_keys = list(input_responses)
        self.task_store.validate_response_keys(handle, response_keys)
        server = self.registry.get(server_id)
        self.extensions.require_active(server, TASKS_EXTENSION_ID)
        async with self.connect(server_id) as connected:
            _require_remote_extension(connected, TASKS_EXTENSION_ID)
            if hasattr(connected.client, "update_task"):
                result = await connected.client.update_task(task_id, input_responses)
            else:
                result = await sdk_update_task(connected.client, task_id, input_responses)
            handle = self.task_store.mark_answered(server_id, task_id, response_keys)
            return _bounded(
                {
                    "connection": connected.info.to_payload(),
                    "acknowledged": True,
                    "result": result,
                    "task": handle.model_dump(mode="json"),
                },
                self.config.max_output_bytes,
            )

    async def cancel_task(
        self,
        server_id: str,
        task_id: str,
        *,
        user_approved: bool = False,
    ) -> dict[str, Any]:
        from loro.mcp.tasks import sdk_cancel_task

        if not user_approved:
            raise MCPTaskApprovalError("MCP task cancellation requires trusted user approval.")
        self.task_store.get(server_id, task_id)
        server = self.registry.get(server_id)
        self.extensions.require_active(server, TASKS_EXTENSION_ID)
        async with self.connect(server_id) as connected:
            _require_remote_extension(connected, TASKS_EXTENSION_ID)
            if hasattr(connected.client, "cancel_task"):
                result = await connected.client.cancel_task(task_id)
            else:
                result = await sdk_cancel_task(connected.client, task_id)
            handle = self.task_store.mark_cancellation_requested(server_id, task_id)
            return _bounded(
                {
                    "connection": connected.info.to_payload(),
                    "acknowledged": True,
                    "cooperative": True,
                    "result": result,
                    "task": handle.model_dump(mode="json"),
                },
                self.config.max_output_bytes,
            )

    async def listen_changes(
        self,
        server_id: str,
        *,
        tools: bool = False,
        prompts: bool = False,
        resources: bool = False,
        resource_uris: list[str] | None = None,
        max_events: int | None = None,
        max_seconds: float | None = None,
    ) -> dict[str, Any]:
        import anyio

        event_limit = self.config.subscription_max_events if max_events is None else max_events
        time_limit = self.config.subscription_max_seconds if max_seconds is None else max_seconds
        if not 1 <= event_limit <= self.config.subscription_max_events:
            raise ValueError("MCP subscription max_events exceeds the configured managed limit.")
        if not 0 < time_limit <= self.config.subscription_max_seconds:
            raise ValueError("MCP subscription max_seconds exceeds the configured managed limit.")
        events: list[dict[str, Any]] = []
        timed_out = False
        async with self.connect(server_id) as connected:
            if connected.info.protocol_version < "2026-07-28":
                raise MCPProtocolError("subscriptions/listen requires MCP 2026-07-28.")
            listen = connected.client.listen(
                tools_list_changed=tools,
                prompts_list_changed=prompts,
                resources_list_changed=resources,
                resource_subscriptions=resource_uris or [],
            )
            with anyio.move_on_after(time_limit) as scope:
                async with listen as subscription:
                    async for event in subscription:
                        events.append(_payload(event))
                        _bounded(events, self.config.max_output_bytes)
                        if len(events) >= event_limit:
                            break
            timed_out = bool(scope.cancel_called)
            return _bounded(
                {
                    "connection": connected.info.to_payload(),
                    "events": events,
                    "event_count": len(events),
                    "timed_out": timed_out,
                    "limits": {"max_events": event_limit, "max_seconds": time_limit},
                },
                self.config.max_output_bytes,
            )

    @asynccontextmanager
    async def connect(self, server_id: str) -> AsyncIterator[ConnectedMCP]:
        server = self.registry.get(server_id)
        # Exceptions raised by the caller's `async with` body are thrown back in at the
        # yield. Only connection setup should be rewritten as MCPClientError; a task error
        # from the body must reach the caller as itself.
        entered = False
        try:
            enforce_server_policy(self.config, server)
            async with self.client_factory(server) as client:
                protocol_version = str(client.protocol_version)
                _enforce_protocol_policy(server, protocol_version)
                capabilities = _mapping(client.server_capabilities)
                info = MCPConnectionInfo(
                    server_id=server_id,
                    transport=server.transport,
                    protocol_version=protocol_version,
                    lifecycle=("stateless" if protocol_version >= "2026-07-28" else "classic"),
                    server_info=_optional_mapping(client.server_info),
                    capabilities=capabilities,
                    extensions=sorted((capabilities.get("extensions") or {}).keys()),
                    sandbox_profile=(
                        self.sandbox_config.mcp_stdio_profile
                        if server.transport == "stdio"
                        else None
                    ),
                    sandbox_os_enforced=(
                        self.sandbox_config.profiles[self.sandbox_config.mcp_stdio_profile].backend
                        == "bubblewrap"
                        if server.transport == "stdio"
                        else None
                    ),
                )
                entered = True
                yield ConnectedMCP(
                    client=client,
                    info=info,
                    max_pagination_pages=self.config.max_pagination_pages,
                    max_output_bytes=self.config.max_output_bytes,
                )
        except (MCPClientError, MCPExtensionError, MCPTransportPolicyError, MCPTaskError):
            raise
        except Exception as error:
            if entered:
                raise
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
                self.config.input_required_max_rounds if self.config.allow_input_required else 0
            ),
            "elicitation_callback": (
                self._elicitation_callback if self.config.allow_input_required else None
            ),
            "extensions": self.extensions.sdk_extensions(
                server,
                self._task_capture_callback(server),
            ),
        }
        if server.transport == "streamable_http":
            if server.url is None:
                raise MCPClientError("MCP HTTP server is missing its validated URL.")
            return self._http_sdk_client(Client, server, kwargs)

        launch = self.prepare_stdio_launch(server)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "loro.mcp.stdio_launcher",
                *sorted(launch.environment),
                "--",
                *launch.args,
            ],
            env=launch.environment,
            cwd=str(launch.cwd),
        )
        return Client(stdio_client(parameters), **kwargs)

    def prepare_stdio_launch(self, server: MCPServerConfig) -> SandboxLaunch:
        """Build the exact sandboxed launch passed to the official stdio transport."""
        if server.transport != "stdio" or server.command is None:
            raise MCPClientError("MCP stdio launch preparation requires a stdio server.")
        missing = [name for name in server.env_allowlist if name not in self.environ]
        if missing:
            raise MCPClientError(
                "Allowlisted MCP environment variables are missing: " + ", ".join(missing)
            )
        environment = {name: self.environ[name] for name in server.env_allowlist}
        return self.sandbox.prepare(
            [server.command, *server.args],
            profile_name=self.sandbox_config.mcp_stdio_profile,
            cwd=Path(server.cwd) if server.cwd else None,
            explicit_environment=environment,
        )

    def _task_capture_callback(self, server: MCPServerConfig) -> Callable[..., Any]:
        server_id = next(
            (
                current_id
                for current_id, candidate in self.config.servers.items()
                if candidate is server
            ),
            "unknown",
        )

        def capture(remote: dict[str, Any], tool_name: str) -> None:
            self.task_store.record_remote(
                server_id=server_id,
                operation="tools/call",
                name=tool_name,
                remote=remote,
            )

        return capture

    @asynccontextmanager
    async def _http_sdk_client(
        self, client_type: Any, server: MCPServerConfig, kwargs: dict[str, Any]
    ) -> AsyncIterator[Any]:
        try:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as error:  # pragma: no cover - covered by the main SDK import
            raise MCPDependencyError("The installed MCP SDK is incomplete.") from error

        if server.url is None:
            raise MCPClientError("MCP HTTP server is missing its validated URL.")
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
                if profile.token_env is None:
                    raise MCPClientError("MCP bearer profile is missing its token environment.")
                headers["Authorization"] = f"Bearer {values[profile.token_env]}"
            else:
                auth = self._oauth_provider(server.url, profile, values)
                hooks.append(dynamic_registration_guard(profile.allow_dynamic_client_registration))
        # Applies to the initial request and to every redirect hop.
        hooks.append(request_policy_hook(self.config))
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
            if not profile.client_id_env or not profile.client_secret_env:
                raise MCPClientError("MCP client-credentials profile is incomplete.")
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
            # "none" is the standardized OAuth public-client authentication method.
            token_endpoint_auth_method="none",  # nosec B106
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
    # Track a running byte count instead of re-serializing everything collected so far on
    # every page, which made a long pagination run quadratic in the accumulated payload.
    total_bytes = 2  # the enclosing "[]"
    cursor: str | None = None
    for _ in range(max_pages):
        result = await method(cursor=cursor)
        raw_items = getattr(result, field, None)
        if raw_items is None and isinstance(result, Mapping):
            raw_items = result.get(field, [])
        for item in raw_items or []:
            payload = _payload(item)
            items.append(payload)
            total_bytes += _encoded_size(payload) + 1  # + the separating comma
            if total_bytes > max_output_bytes:
                raise MCPProtocolError(
                    f"MCP output exceeded managed limit of {max_output_bytes} bytes."
                )
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


def _encoded_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str).encode("utf-8")
    )


def _bounded(value: Any, max_output_bytes: int) -> Any:
    size = _encoded_size(value)
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


def _require_remote_extension(connected: ConnectedMCP, extension_id: str) -> None:
    if connected.info.protocol_version < "2026-07-28":
        raise MCPProtocolError(
            f"MCP extension {extension_id} requires protocol 2026-07-28 or newer."
        )
    if extension_id not in connected.info.extensions:
        raise MCPExtensionError(f"MCP server did not advertise required extension: {extension_id}")


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
