from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from loro.config import MCPConfig, MCPCredentialProfileConfig, MCPServerConfig
from loro.mcp.client import MCPProtocolError, MCPService
from loro.mcp.registry import MCPRegistry, MCPRegistryError, diagnose_mcp
from loro.mcp.security import (
    MCPTransportPolicyError,
    credential_environment,
    dynamic_registration_guard,
    enforce_server_policy,
)


class FakeMCPClient:
    def __init__(self, protocol_version: str = "2026-07-28") -> None:
        self.protocol_version = protocol_version
        self.server_info = {"name": "fixture", "version": "1.0"}
        self.server_capabilities = {"tools": {}, "resources": {}, "prompts": {}}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self, *, cursor: str | None = None) -> Any:
        if cursor is None:
            return SimpleNamespace(
                tools=[{"name": "echo", "inputSchema": {"type": "object"}}],
                next_cursor="page-2",
            )
        return SimpleNamespace(tools=[{"name": "status"}], next_cursor=None)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, arguments or {}))
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    async def list_resources(self, *, cursor: str | None = None) -> Any:
        return SimpleNamespace(resources=[{"uri": "fixture://readme"}], next_cursor=None)

    async def read_resource(self, uri: str) -> Any:
        return {"contents": [{"uri": uri, "text": "untrusted fixture"}]}

    async def list_prompts(self, *, cursor: str | None = None) -> Any:
        return SimpleNamespace(prompts=[{"name": "summarize"}], next_cursor=None)

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> Any:
        return {"description": name, "messages": [], "arguments": arguments or {}}


class EndlessPaginationClient(FakeMCPClient):
    async def list_tools(self, *, cursor: str | None = None) -> Any:
        return SimpleNamespace(tools=[], next_cursor="again")


def fake_factory(client: FakeMCPClient):
    @asynccontextmanager
    async def factory(_server: MCPServerConfig):
        yield client

    return factory


def mcp_config(
    *,
    protocol_version: str = "2026-07-28",
    minimum: str | None = None,
    allowed: list[str] | None = None,
) -> MCPConfig:
    return MCPConfig(
        enabled=True,
        servers={
            "fixture": MCPServerConfig(
                command="fixture-server",
                allowed_protocol_versions=allowed or ["2026-07-28", "2025-11-25", "2024-11-05"],
                minimum_protocol_version=minimum,
                protocol_mode="auto" if protocol_version == "2026-07-28" else "legacy",
            )
        },
    )


def test_mcp_config_validates_transport_and_server_ids() -> None:
    with pytest.raises(ValueError, match="requires command"):
        MCPServerConfig(transport="stdio")
    with pytest.raises(ValueError, match="requires url"):
        MCPServerConfig(transport="streamable_http")
    with pytest.raises(ValueError, match="server ids"):
        MCPConfig(servers={"Bad Server": MCPServerConfig(command="server")})


def test_mcp_registry_requires_enabled_server() -> None:
    registry = MCPRegistry(
        MCPConfig(enabled=False, servers={"fixture": MCPServerConfig(command="server")})
    )
    assert registry.payload("fixture")["endpoint"] == "server"
    with pytest.raises(MCPRegistryError, match="MCP is disabled"):
        registry.get("fixture")


def test_mcp_registry_redacts_endpoint_query_and_sensitive_args() -> None:
    config = MCPConfig(
        servers={
            "remote": MCPServerConfig(
                transport="streamable_http",
                url="https://mcp.example/mcp?token=secret-value&tenant=acme",
            ),
            "local": MCPServerConfig(
                command="server",
                args=["--api-key", "secret-value", "--tenant=acme"],
            ),
        }
    )
    registry = MCPRegistry(config)

    assert "secret-value" not in registry.payload("remote")["endpoint"]
    assert registry.payload("local")["args"] == ["--api-key", "[REDACTED]", "--tenant=acme"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "lifecycle"),
    [("2026-07-28", "stateless"), ("2025-11-25", "classic")],
)
async def test_mcp_service_normalizes_both_lifecycles(version: str, lifecycle: str) -> None:
    client = FakeMCPClient(version)
    service = MCPService(mcp_config(protocol_version=version), client_factory=fake_factory(client))

    result = await service.test_connection("fixture")

    assert result["connection"]["protocol_version"] == version
    assert result["connection"]["lifecycle"] == lifecycle
    assert result["counts"] == {"tools": 2, "resources": 1, "prompts": 1}


@pytest.mark.asyncio
async def test_mcp_service_calls_tool_and_normalizes_result() -> None:
    client = FakeMCPClient()
    service = MCPService(mcp_config(), client_factory=fake_factory(client))

    result = await service.call_tool("fixture", "echo", {"value": "hello"})

    assert client.calls == [("echo", {"value": "hello"})]
    assert result["result"]["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_mcp_service_normalizes_resource_and_prompt_operations() -> None:
    service = MCPService(
        mcp_config(), client_factory=fake_factory(FakeMCPClient())
    )

    connection = await service.inspect_connection("fixture")
    tools = await service.list_tools("fixture")
    resources = await service.list_resources("fixture")
    read = await service.read_resource("fixture", "fixture://readme")
    prompts = await service.list_prompts("fixture")
    prompt = await service.get_prompt("fixture", "summarize", {"audience": "engineering"})

    assert connection["server_id"] == "fixture"
    assert tools["tools"][0]["name"] == "echo"
    assert resources["resources"][0]["uri"] == "fixture://readme"
    assert read["result"]["contents"][0]["text"] == "untrusted fixture"
    assert prompts["prompts"][0]["name"] == "summarize"
    assert prompt["result"]["arguments"] == {"audience": "engineering"}


@pytest.mark.asyncio
async def test_mcp_service_rejects_disallowed_or_downgraded_version() -> None:
    disallowed = MCPService(
        mcp_config(allowed=["2026-07-28"]),
        client_factory=fake_factory(FakeMCPClient("2025-11-25")),
    )
    with pytest.raises(MCPProtocolError, match="not allowed"):
        await disallowed.inspect_connection("fixture")

    downgraded = MCPService(
        mcp_config(minimum="2025-11-25"),
        client_factory=fake_factory(FakeMCPClient("2024-11-05")),
    )
    with pytest.raises(MCPProtocolError, match="below managed minimum"):
        await downgraded.inspect_connection("fixture")


@pytest.mark.asyncio
async def test_mcp_service_bounds_pagination() -> None:
    service = MCPService(mcp_config(), client_factory=fake_factory(EndlessPaginationClient()))

    with pytest.raises(MCPProtocolError, match="exceeded 20 pages"):
        await service.list_tools("fixture")


@pytest.mark.asyncio
async def test_mcp_service_bounds_untrusted_output() -> None:
    class OversizedClient(FakeMCPClient):
        async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
            return {"content": [{"type": "text", "text": "x" * 2000}]}

    client = OversizedClient()
    config = mcp_config().model_copy(update={"max_output_bytes": 1024})
    service = MCPService(config, client_factory=fake_factory(client))

    with pytest.raises(MCPProtocolError, match="managed limit"):
        await service.call_tool("fixture", "echo", {"value": "x" * 2000})


@pytest.mark.asyncio
async def test_mcp_service_normalizes_transport_timeout() -> None:
    @asynccontextmanager
    async def timeout_factory(_server: MCPServerConfig):
        raise TimeoutError("fixture timed out")
        yield  # pragma: no cover

    service = MCPService(mcp_config(), client_factory=timeout_factory)

    with pytest.raises(RuntimeError, match="fixture timed out"):
        await service.inspect_connection("fixture")


def test_mcp_doctor_reports_missing_environment(monkeypatch) -> None:
    monkeypatch.setattr("loro.mcp.registry._mcp_sdk_version", lambda: "2.0.0")
    config = MCPConfig(
        enabled=True,
        servers={
            "fixture": MCPServerConfig(
                command="python",
                env_allowlist=["MCP_FIXTURE_TOKEN"],
            )
        },
    )

    result = diagnose_mcp(config, environ={"PATH": "/usr/bin"})

    assert result["ok"] is False
    assert "MCP_FIXTURE_TOKEN" in result["issues"][0]


def test_mcp_transport_policy_enforces_tls_hosts_and_stdio_commands() -> None:
    remote = MCPServerConfig(transport="streamable_http", url="http://mcp.attacker.example/mcp")
    policy = MCPConfig(
        require_https=True,
        allowed_hosts=["*.trusted.example"],
        allowed_stdio_commands=["/usr/bin/python"],
    )
    with pytest.raises(MCPTransportPolicyError, match="HTTPS"):
        enforce_server_policy(policy, remote)
    with pytest.raises(MCPTransportPolicyError, match="stdio command"):
        enforce_server_policy(policy, MCPServerConfig(command="node"))

    trusted = remote.model_copy(update={"url": "https://mcp.trusted.example/mcp"})
    enforce_server_policy(policy, trusted)


def test_mcp_transport_policy_rejects_private_dns_answers() -> None:
    server = MCPServerConfig(transport="streamable_http", url="https://mcp.example/mcp")

    def private_resolver(*_args, **_kwargs):
        return [(2, 1, 6, "", ("169.254.169.254", 443))]

    with pytest.raises(MCPTransportPolicyError, match="non-public"):
        enforce_server_policy(
            MCPConfig(block_private_networks=True),
            server,
            resolver=private_resolver,
        )


def test_mcp_credentials_are_environment_references() -> None:
    profile = MCPCredentialProfileConfig(type="bearer", token_env="MCP_TOKEN")
    assert credential_environment(profile, {"MCP_TOKEN": "secret"}) == {"MCP_TOKEN": "secret"}
    with pytest.raises(MCPTransportPolicyError, match="MCP_TOKEN"):
        credential_environment(profile, {})


@pytest.mark.asyncio
async def test_mcp_dynamic_registration_is_explicit_legacy_policy() -> None:
    request = SimpleNamespace(
        method="POST",
        headers={"content-type": "application/json"},
        content=b'{"redirect_uris":["http://localhost/callback"]}',
    )
    with pytest.raises(MCPTransportPolicyError, match="Dynamic Client Registration"):
        await dynamic_registration_guard(False)(request)
    await dynamic_registration_guard(True)(request)


def test_mcp_config_requires_declared_credential_profiles() -> None:
    with pytest.raises(ValueError, match="Unknown MCP credential profiles"):
        MCPConfig(
            servers={
                "remote": MCPServerConfig(
                    transport="streamable_http",
                    url="https://mcp.example/mcp",
                    credential_profile="missing",
                )
            }
        )
    with pytest.raises(ValueError, match="client_metadata_url"):
        MCPCredentialProfileConfig(type="oauth_authorization_code")


def test_mcp_cli_add_and_list(tmp_path, monkeypatch) -> None:
    from loro.cli import app

    output = tmp_path / "config.toml"
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    runner = CliRunner()

    add_result = runner.invoke(
        app,
        [
            "mcp",
            "add",
            "demo",
            "--command",
            "python",
            "--arg",
            "server.py",
            "--arg=-y",
            "--env",
            "MCP_DEMO_TOKEN",
            "--output",
            str(output),
        ],
    )

    assert add_result.exit_code == 0, add_result.stdout
    monkeypatch.setenv("LORO_CONFIG", str(output))
    list_result = runner.invoke(app, ["mcp", "list"])
    assert list_result.exit_code == 0
    assert '"id": "demo"' in list_result.stdout
    assert "MCP_DEMO_TOKEN" in list_result.stdout
    assert '"-y"' in list_result.stdout

    remove_result = runner.invoke(
        app, ["mcp", "remove", "demo", "--output", str(output)]
    )
    assert remove_result.exit_code == 0, remove_result.stdout
    assert "[mcp.servers.demo]" not in output.read_text(encoding="utf-8")


def test_mcp_cli_configures_environment_backed_auth_profile(tmp_path, monkeypatch) -> None:
    from loro.cli import app

    output = tmp_path / "config.toml"
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "mcp",
            "auth-add",
            "prod",
            "--type",
            "bearer",
            "--token-env",
            "MCP_PROD_TOKEN",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "secret" not in output.read_text(encoding="utf-8").casefold()

    monkeypatch.setenv("LORO_CONFIG", str(output))
    listed = runner.invoke(app, ["mcp", "auth-list"])
    assert listed.exit_code == 0
    assert "MCP_PROD_TOKEN" in listed.stdout

    removed = runner.invoke(
        app,
        ["mcp", "auth-remove", "prod", "--output", str(output)],
    )
    assert removed.exit_code == 0, removed.stdout
    assert "MCP_PROD_TOKEN" not in output.read_text(encoding="utf-8")


def test_mcp_cli_call_requires_and_records_explicit_approval(tmp_path, monkeypatch) -> None:
    from loro.cli import app

    client = FakeMCPClient()
    service = MCPService(mcp_config(), client_factory=fake_factory(client))
    monkeypatch.setattr("loro.cli._mcp_service", lambda: service)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[mcp]\nenabled = true\n"
        '[mcp.servers.fixture]\ncommand = "fixture-server"\n'
        '[permissions]\nmcp = "ask"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )

    result = CliRunner().invoke(
        app,
        ["mcp", "call", "fixture", "echo", "--arguments", '{"value":"hello"}'],
        input="once\n",
    )

    assert result.exit_code == 0, result.stdout
    assert "Approval required" in result.stdout
    assert client.calls == [("echo", {"value": "hello"})]
    events = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "mcp.request_completed" in events
    assert '"value":"hello"' not in events


def test_mcp_cli_read_honors_deny_rule(tmp_path, monkeypatch) -> None:
    from loro.cli import app

    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[mcp]\nenabled = true\n"
        '[mcp.servers.fixture]\ncommand = "fixture-server"\n'
        "[[permissions.rules]]\n"
        'tool = "mcp"\naction = "read*"\nresource_kind = "mcp"\ndecision = "deny"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )

    result = CliRunner().invoke(app, ["mcp", "read", "fixture", "fixture://secret"])

    assert result.exit_code != 0
    assert "denied by policy" in result.output
