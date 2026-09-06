"""Stdio MCP server exposing Loro's exact-origin WebMCP bridge."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from loro.webmcp_bridge import AlexMercedWebMCPBridge, WebMCPBridgeError


def build_server() -> Any:
    try:
        from mcp.server import MCPServer as ServerType
    except ImportError as error:
        try:
            from mcp.server.fastmcp import FastMCP as ServerType
        except ImportError:
            raise WebMCPBridgeError(
                'The WebMCP server requires `pip install "loro-agent[webmcp]"`.'
            ) from error

    bridge = AlexMercedWebMCPBridge()
    server = ServerType(
        "Loro WebMCP",
        instructions=(
            "Exact-origin browser-local tools. Navigate before discovery, bind calls to the "
            "returned registry revision, and apply Loro policy to every invocation."
        ),
    )

    @server.tool(name="webmcp_open")
    async def webmcp_open(path: str = "/", wait_ms: int = 750) -> str:
        """Open an allowlisted HTTPS URL or path and return its live WebMCP tools."""
        return json.dumps(await bridge.open(path, wait_ms), default=str)

    @server.tool(name="webmcp_list_tools")
    async def webmcp_list_tools(path: str = "", wait_ms: int = 750) -> str:
        """List tools on the current page, optionally navigating first."""
        return json.dumps(await bridge.list_tools(path, wait_ms), default=str)

    @server.tool(name="webmcp_call_tool")
    async def webmcp_call_tool(
        name: str,
        arguments: dict[str, Any] | None = None,
        path: str = "",
        wait_ms: int = 750,
        registry_revision: str = "",
    ) -> str:
        """Invoke a discovered tool, optionally requiring an exact registry revision."""
        return json.dumps(
            await bridge.call_tool(
                name, arguments, path, wait_ms, registry_revision=registry_revision
            ),
            default=str,
        )

    @server.tool(name="webmcp_status")
    def webmcp_status() -> str:
        """Return configured origins and live browser-session state."""
        return json.dumps(bridge.status(), default=str)

    @server.tool(name="webmcp_close")
    async def webmcp_close() -> str:
        """Close the persistent WebMCP browser session and release its resources."""
        return json.dumps(await bridge.close(), default=str)

    @server.resource("webmcp://alexmerced-app/about")
    def webmcp_about() -> str:
        """Describe the bridge's scope and persistence contract."""
        return json.dumps(
            {
                "origins": list(bridge.origins),
                "page_scoped": True,
                "persistent_browser_profile": str(bridge.profile_path),
                "live_discovery_required": True,
            }
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Loro's exact-origin WebMCP MCP server.")
    parser.add_argument(
        "--origins",
        default="",
        help="Comma-separated exact HTTPS origins (defaults to https://alexmerced.app).",
    )
    args = parser.parse_args()
    if args.origins:
        os.environ["LORO_WEBMCP_ORIGINS"] = args.origins
    build_server().run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
