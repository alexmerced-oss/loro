from pathlib import Path
from typing import Any

import pytest
import yaml

from loro.agraph.generate import generate_graph
from loro.config import (
    AuditConfig,
    LoroConfig,
    MCPConfig,
    MCPServerConfig,
    MCPServerModeConfig,
    PermissionsConfig,
)
from loro.mcp.client import MCPService
from loro.mcp.server import (
    LoroMCPServerCatalog,
    MCPServerModeError,
    build_mcp_server,
)


def server_config(tmp_path: Path, exports: list[str]) -> LoroConfig:
    return LoroConfig(
        mcp=MCPConfig(
            server=MCPServerModeConfig(enabled=True, export_tools=exports),
        ),
        permissions=PermissionsConfig(workspace_roots=[str(tmp_path)], edit="allow"),
        audit=AuditConfig(path=str(tmp_path / "audit.jsonl")),
    )


def test_mcp_server_mode_rejects_remote_bind_and_forbidden_exports(tmp_path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        MCPServerModeConfig(transport="streamable_http", host="0.0.0.0")

    config = server_config(tmp_path, ["memory.search"])
    with pytest.raises(MCPServerModeError, match="not permitted"):
        LoroMCPServerCatalog(config)


def test_mcp_server_catalog_executes_only_explicit_read_exports(tmp_path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("read-only export\n", encoding="utf-8")
    catalog = LoroMCPServerCatalog(server_config(tmp_path, ["file.read"]))

    assert catalog.execute("file.read", {"path": str(note)}) == "read-only export\n"
    assert catalog.manifest()["tools"] == ["file.read"]
    assert "local and shared memory" in catalog.manifest()["excluded_by_default"]
    with pytest.raises(MCPServerModeError, match="not exported"):
        catalog.execute("file.search", {"query": "secret"})


def test_mcp_server_catalog_bounds_exported_output(tmp_path) -> None:
    note = tmp_path / "large.txt"
    note.write_text("x" * 2000, encoding="utf-8")
    config = server_config(tmp_path, ["file.read"])
    config.mcp.max_output_bytes = 1024

    with pytest.raises(MCPServerModeError, match="managed limit"):
        LoroMCPServerCatalog(config).execute("file.read", {"path": str(note), "limit": 3000})


def test_mcp_server_exports_read_only_graph_validation_and_planning(tmp_path) -> None:
    config = server_config(tmp_path, ["agraph.validate", "agraph.plan"])
    path = tmp_path / "plan.agraph.yaml"
    path.write_text(yaml.safe_dump(generate_graph("Review evidence", config)), encoding="utf-8")
    catalog = LoroMCPServerCatalog(config)

    assert '"ok": true' in catalog.execute("agraph.validate", {"path": str(path)})
    assert '"worst_case_executions": 2' in catalog.execute("agraph.plan", {"path": str(path)})


def test_build_mcp_server_registers_tools_resources_and_prompts(tmp_path, monkeypatch) -> None:
    class FakeServer:
        def __init__(self, name: str, **options: Any) -> None:
            self.name = name
            self.options = options
            self.tools: dict[str, Any] = {}
            self.resources: dict[str, Any] = {}
            self.prompts: dict[str, Any] = {}

        def tool(self, *, name: str):
            def register(function):
                self.tools[name] = function
                return function

            return register

        def resource(self, uri: str):
            def register(function):
                self.resources[uri] = function
                return function

            return register

        def prompt(self, *, name: str):
            def register(function):
                self.prompts[name] = function
                return function

            return register

    monkeypatch.setattr("loro.mcp.server._official_server_type", lambda: FakeServer)
    server = build_mcp_server(server_config(tmp_path, ["file.read", "git.status"]))

    assert set(server.tools) == {"file_read", "git_status"}
    assert set(server.resources) == {"loro://server/manifest"}
    assert set(server.prompts) == {"loro_plan", "loro_review"}
    with pytest.raises(MCPServerModeError, match="prompt argument exceeded"):
        server.prompts["loro_plan"]("x" * 1_000_001)


def test_mcp_server_requires_optional_sdk(tmp_path, monkeypatch) -> None:
    def missing_sdk():
        raise MCPServerModeError("optional dependency")

    monkeypatch.setattr("loro.mcp.server._official_server_type", missing_sdk)
    with pytest.raises(MCPServerModeError, match="optional dependency"):
        build_mcp_server(server_config(tmp_path, []))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_version"),
    [("auto", "2026-07-28"), ("legacy", "2025-11-25")],
)
async def test_loro_server_interoperates_with_official_sdk_client(
    tmp_path, mode, expected_version
) -> None:
    mcp = pytest.importorskip("mcp")
    note = tmp_path / "note.txt"
    note.write_text("official SDK path\n", encoding="utf-8")
    server = build_mcp_server(server_config(tmp_path, ["file.read"]))
    client_config = MCPConfig(
        enabled=True,
        servers={
            "loro": MCPServerConfig(
                command="in-process",
                protocol_mode=mode,
                allowed_protocol_versions=["2026-07-28", "2025-11-25"],
            )
        },
    )
    service = MCPService(
        client_config,
        client_factory=lambda configured: mcp.Client(server, mode=configured.protocol_mode),
    )

    result = await service.call_tool("loro", "file_read", {"path": str(note)})

    assert result["connection"]["protocol_version"] == expected_version
    assert result["result"]["isError"] is False
