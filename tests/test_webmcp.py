import json
import tomllib

import pytest
from typer.testing import CliRunner

from loro.cli import app
from loro.config import SkillsConfig
from loro.skills import SkillRegistry
from loro.webmcp_bridge import AlexMercedWebMCPBridge, WebMCPBridgeError, alexmerced_url


class FakePage:
    def __init__(self) -> None:
        self.url = "https://alexmerced.app/quarry"
        self.opened: list[str] = []
        self.calls: list[dict] = []

    async def goto(self, url: str, wait_until: str) -> None:
        self.url = url
        self.opened.append(url)

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    async def title(self) -> str:
        return "Quarry"

    async def evaluate(self, script: str, arguments=None):
        if "values?." in script:
            return [
                {
                    "name": "quarry_list_tables",
                    "description": "List loaded tables.",
                    "inputSchema": {"type": "object", "properties": {}},
                    "annotations": None,
                }
            ]
        self.calls.append(arguments)
        return {"content": [{"type": "text", "text": "[]"}]}


def test_alexmerced_url_is_origin_restricted() -> None:
    assert alexmerced_url("/quarry") == "https://alexmerced.app/quarry"
    with pytest.raises(WebMCPBridgeError, match="restricted"):
        alexmerced_url("https://example.com/")


@pytest.mark.asyncio
async def test_bridge_keeps_page_state_across_discovery_and_invocation(monkeypatch) -> None:
    bridge = AlexMercedWebMCPBridge()
    page = FakePage()
    bridge._page = page

    tools = await bridge.list_tools()
    result = await bridge.call_tool("quarry_list_tables", {"verbose": True})

    assert tools["tool_count"] == 1
    assert tools["tools"][0]["name"] == "quarry_list_tables"
    assert result["ok"] is True
    assert page.calls == [{"name": "quarry_list_tables", "arguments": {"verbose": True}}]


def test_bundled_webmcp_skill_is_discoverable(tmp_path) -> None:
    config = SkillsConfig(
        include_bundled=True,
        managed_paths=[],
        user_paths=[],
        project_paths=[],
        state_path=str(tmp_path / "state.json"),
        proposal_path=str(tmp_path / "proposals"),
    )
    registry = SkillRegistry(config)

    skill = registry.get("alexmerced-webmcp")

    assert skill.scope == "managed"
    assert "mcp.call" in skill.allowed_tools
    assert registry.select("Use WebMCP to merge this PDF")[0].metadata.name == (
        "alexmerced-webmcp"
    )


def test_setup_webmcp_writes_reviewable_origin_restricted_server(tmp_path, monkeypatch) -> None:
    output = tmp_path / "config.toml"
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )

    result = CliRunner().invoke(
        app, ["setup", "webmcp", "--output", str(output)]
    )

    assert result.exit_code == 0, result.stdout
    config = tomllib.loads(output.read_text(encoding="utf-8"))
    assert config["mcp"]["enabled"] is True
    assert config["mcp"]["servers"]["alexmerced-webmcp"]["command"] == "loro-webmcp"
    assert "loro-webmcp" in config["mcp"]["allowed_stdio_commands"]
    audit = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["event_type"] == "config.webmcp_written"
