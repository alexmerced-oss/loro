"""Origin-restricted WebMCP browser bridge used by Loro's optional MCP server."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

WEBMCP_ORIGIN = "https://alexmerced.app"
MAX_RESULT_BYTES = 2_000_000
INIT_SCRIPT = r"""
(() => {
  const tools = new Map();
  const normalize = (first, second) => typeof first === "string"
    ? {name: first, ...(second || {})}
    : (first || {});
  const registry = {
    registerTool(first, second) {
      const tool = normalize(first, second);
      if (!tool.name || typeof tool.name !== "string") {
        throw new Error("WebMCP tool requires a name");
      }
      tools.set(tool.name, tool);
      return tool.name;
    },
    unregisterTool(name) { tools.delete(name); },
  };
  Object.defineProperty(globalThis, "__loroWebMCPTools", {value: tools, configurable: true});
  for (const host of [document, navigator]) {
    try { Object.defineProperty(host, "modelContext", {value: registry, configurable: true}); }
    catch (_) { /* Native implementations may own this property. */ }
  }
})();
"""


class WebMCPBridgeError(RuntimeError):
    """Raised when WebMCP navigation, discovery, or invocation fails safely."""


def alexmerced_url(path: str = "/") -> str:
    requested = str(path or "/").strip()
    url = urljoin(WEBMCP_ORIGIN + "/", requested.lstrip("/"))
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.casefold() != "alexmerced.app":
        raise WebMCPBridgeError(
            "The bundled WebMCP server is restricted to https://alexmerced.app."
        )
    return url


class AlexMercedWebMCPBridge:
    """Keeps one browser profile and page alive for page-scoped WebMCP tools."""

    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._url = WEBMCP_ORIGIN + "/"

    @property
    def profile_path(self) -> Path:
        configured = os.environ.get("LORO_WEBMCP_PROFILE", "").strip()
        return (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".local" / "share" / "loro" / "webmcp" / "alexmerced-app"
        )

    @property
    def headless(self) -> bool:
        return os.environ.get("LORO_WEBMCP_HEADLESS", "0").strip().casefold() in {
            "1",
            "true",
            "yes",
        }

    async def _start(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise WebMCPBridgeError(
                'WebMCP support is optional. Install `loro-agent[webmcp]`, then run '
                '`playwright install chromium`.'
            ) from error
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self.profile_path),
                headless=self.headless,
                viewport={"width": 1440, "height": 1000},
            )
            await self._context.add_init_script(INIT_SCRIPT)
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
        except Exception:
            await self.close()
            raise

    async def open(self, path: str = "/", wait_ms: int = 750) -> dict[str, Any]:
        await self._start()
        self._url = alexmerced_url(path)
        await self._page.goto(self._url, wait_until="domcontentloaded")
        if wait_ms:
            await self._page.wait_for_timeout(max(0, min(int(wait_ms), 10_000)))
        return await self.list_tools()

    async def list_tools(self, path: str = "", wait_ms: int = 750) -> dict[str, Any]:
        if path or self._page is None:
            return await self.open(path or "/", wait_ms=wait_ms)
        tools = await self._page.evaluate(
            """() => Array.from(globalThis.__loroWebMCPTools?.values?.() || []).map((tool) => ({
              name: tool.name,
              description: tool.description || "",
              inputSchema: tool.inputSchema || {type: "object", properties: {}},
              annotations: tool.annotations || null,
            }))"""
        )
        return {
            "ok": True,
            "url": self._page.url,
            "title": await self._page.title(),
            "tool_count": len(tools),
            "tools": tools,
            "browser_profile": str(self.profile_path),
            "visible": not self.headless,
        }

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        path: str = "",
        wait_ms: int = 750,
    ) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            raise WebMCPBridgeError("WebMCP tool name must be a non-empty string.")
        if path or self._page is None:
            await self.open(path or "/", wait_ms=wait_ms)
        result = await self._page.evaluate(
            """async ({name, arguments}) => {
              const tool = globalThis.__loroWebMCPTools?.get?.(name);
              if (!tool) return {
                __bridgeError: `No WebMCP tool named ${name} is registered on this page.`,
                available: Array.from(globalThis.__loroWebMCPTools?.keys?.() || [])
              };
              const handler = tool.execute || tool.handler;
              if (typeof handler !== "function") return {
                __bridgeError: `WebMCP tool ${name} has no callable handler.`
              };
              return await handler(arguments || {});
            }""",
            {"name": name.strip(), "arguments": arguments or {}},
        )
        if isinstance(result, dict) and result.get("__bridgeError"):
            raise WebMCPBridgeError(
                f"{result['__bridgeError']} Available: {', '.join(result.get('available', []))}"
            )
        if len(json.dumps(result, default=str).encode("utf-8")) > MAX_RESULT_BYTES:
            raise WebMCPBridgeError(f"WebMCP result exceeded {MAX_RESULT_BYTES} bytes.")
        return {"ok": True, "url": self._page.url, "tool": name.strip(), "result": result}

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._page = self._context = self._playwright = None
