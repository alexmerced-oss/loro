import pytest

from loro.config import MCPConfig, MCPServerConfig
from loro.mcp.client import MCPService

mcp = pytest.importorskip("mcp")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_version", "expected_lifecycle"),
    [("auto", "2026-07-28", "stateless"), ("legacy", "2025-11-25", "classic")],
)
async def test_official_sdk_in_process_interoperability(
    mode: str, expected_version: str, expected_lifecycle: str
) -> None:
    from mcp import Client
    from mcp.server import MCPServer

    server = MCPServer("loro-test", version="1.0")

    @server.tool()
    async def echo(value: str) -> str:
        return value

    config = MCPConfig(
        enabled=True,
        servers={
            "fixture": MCPServerConfig(
                command="in-process",
                protocol_mode=mode,
                allowed_protocol_versions=["2026-07-28", "2025-11-25", "2024-11-05"],
            )
        },
    )
    service = MCPService(
        config,
        client_factory=lambda server_config: Client(server, mode=server_config.protocol_mode),
    )

    result = await service.call_tool("fixture", "echo", {"value": "hello"})

    assert result["connection"]["protocol_version"] == expected_version
    assert result["connection"]["lifecycle"] == expected_lifecycle
    assert result["result"]["isError"] is False
