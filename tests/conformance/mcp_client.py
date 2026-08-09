"""Process adapter used by the official MCP conformance client runner."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from loro.config import MCPConfig, MCPServerConfig
from loro.mcp.client import MCPService


def example_value(schema: dict[str, Any]) -> Any:
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = schema.get("required", properties.keys())
        return {name: example_value(properties[name]) for name in required if name in properties}
    if schema_type == "array":
        return [example_value(schema.get("items", {}))]
    if schema_type in {"integer", "number"}:
        return 1
    if schema_type == "boolean":
        return True
    return "conformance"


async def run(url: str, scenario: str, protocol_version: str) -> dict[str, Any]:
    lifecycle = "legacy" if protocol_version <= "2025-11-25" else protocol_version
    config = MCPConfig(
        enabled=True,
        servers={
            "conformance": MCPServerConfig(
                transport="streamable_http",
                url=url,
                protocol_mode=lifecycle,
                allowed_protocol_versions=[protocol_version],
                minimum_protocol_version=protocol_version,
            )
        },
        allowed_hosts=["127.0.0.1", "localhost"],
    )
    service = MCPService(config)
    result: dict[str, Any] = {"scenario": scenario}
    if "tool" in scenario:
        tools = await service.list_tools("conformance")
        result["tools"] = tools
        if "call" in scenario and tools["tools"]:
            tool = tools["tools"][0]
            schema = tool.get("inputSchema") or tool.get("input_schema") or {}
            result["call"] = await service.call_tool(
                "conformance", str(tool["name"]), example_value(schema)
            )
    elif "resource" in scenario:
        resources = await service.list_resources("conformance")
        result["resources"] = resources
        if "read" in scenario and resources["resources"]:
            result["read"] = await service.read_resource(
                "conformance", str(resources["resources"][0]["uri"])
            )
    elif "prompt" in scenario:
        prompts = await service.list_prompts("conformance")
        result["prompts"] = prompts
        if "get" in scenario and prompts["prompts"]:
            prompt = prompts["prompts"][0]
            arguments = {
                str(item["name"]): "conformance"
                for item in prompt.get("arguments", [])
                if item.get("required")
            }
            result["prompt"] = await service.get_prompt(
                "conformance", str(prompt["name"]), arguments
            )
    else:
        result["connection"] = await service.inspect_connection("conformance")
    return result


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: mcp_client.py <server-url>")
    scenario = os.environ.get("MCP_CONFORMANCE_SCENARIO", "initialize")
    # The published runner currently launches process clients without a protocol-version
    # environment variable and its fixture servers negotiate the classic revision.
    protocol_version = os.environ.get("MCP_CONFORMANCE_PROTOCOL_VERSION", "2025-11-25")
    print(json.dumps(asyncio.run(run(sys.argv[1], scenario, protocol_version)), sort_keys=True))


if __name__ == "__main__":
    main()
