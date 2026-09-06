"""Origin-restricted WebMCP browser bridge used by Loro's optional MCP server."""

from __future__ import annotations

import hashlib
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


def _canonical_origin(value: str, *, configuration: bool = False) -> str:
    parsed = urlsplit(str(value).strip())
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (configuration and parsed.path not in {"", "/"})
        or (configuration and (parsed.query or parsed.fragment))
    ):
        raise WebMCPBridgeError("WebMCP requires an exact HTTPS origin without credentials.")
    try:
        port = parsed.port
    except ValueError as error:
        raise WebMCPBridgeError("WebMCP origin contains an invalid port.") from error
    host = (
        f"[{parsed.hostname.casefold()}]" if ":" in parsed.hostname else parsed.hostname.casefold()
    )
    return f"https://{host}" + (f":{port}" if port not in {None, 443} else "")


def normalize_webmcp_origins(origins: list[str] | None = None) -> tuple[str, ...]:
    """Return the exact HTTPS origin allowlist, preserving the bundled default."""
    values = origins
    if values is None:
        values = os.environ.get("LORO_WEBMCP_ORIGINS", WEBMCP_ORIGIN).split(",")
    normalized: list[str] = []
    for value in values:
        try:
            origin = _canonical_origin(str(value), configuration=True)
        except WebMCPBridgeError:
            continue
        if origin not in normalized:
            normalized.append(origin)
    if not normalized:
        raise WebMCPBridgeError("WebMCP requires at least one exact HTTPS origin.")
    return tuple(normalized)


def webmcp_url(
    path: str = "/",
    *,
    origins: list[str] | tuple[str, ...] | None = None,
    current_url: str = "",
) -> str:
    allowed = normalize_webmcp_origins(list(origins) if origins is not None else None)
    requested = str(path or "/").strip()
    base = current_url if current_url and urlsplit(current_url).scheme == "https" else allowed[0]
    url = urljoin(base.rstrip("/") + "/", requested)
    try:
        origin = _canonical_origin(url)
    except WebMCPBridgeError:
        origin = ""
    if origin not in allowed:
        raise WebMCPBridgeError(
            "WebMCP navigation is restricted to the configured exact HTTPS origins."
        )
    return url


def alexmerced_url(path: str = "/") -> str:
    """Backward-compatible helper for the bundled alexmerced.app origin."""
    return webmcp_url(path, origins=[WEBMCP_ORIGIN])


def registry_revision(url: str, tools: list[dict[str, Any]]) -> str:
    payload = json.dumps({"url": url, "tools": tools}, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AlexMercedWebMCPBridge:
    """Keeps one browser profile and page alive for page-scoped WebMCP tools."""

    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._url = WEBMCP_ORIGIN + "/"
        self._origin = ""
        self._revision = ""

    @property
    def origins(self) -> tuple[str, ...]:
        return normalize_webmcp_origins()

    @property
    def profile_path(self) -> Path:
        configured = os.environ.get("LORO_WEBMCP_PROFILE", "").strip()
        base = (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".local" / "share" / "loro" / "webmcp"
        )
        origin = self._origin or self.origins[0]
        key = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:16]
        return base / key

    @property
    def headless(self) -> bool:
        return os.environ.get("LORO_WEBMCP_HEADLESS", "0").strip().casefold() in {
            "1",
            "true",
            "yes",
        }

    async def _start(self, origin: str) -> None:
        if self._page is not None and self._origin == origin:
            return
        if self._page is not None:
            await self.close()
        self._origin = origin
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise WebMCPBridgeError(
                "WebMCP support is optional. Install `loro-agent[webmcp]`, then run "
                "`playwright install chromium`."
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
                self._context.pages[0] if self._context.pages else await self._context.new_page()
            )
        except Exception:
            await self.close()
            raise

    async def open(self, path: str = "/", wait_ms: int = 750) -> dict[str, Any]:
        self._url = webmcp_url(path, origins=self.origins, current_url=self._url)
        origin = _canonical_origin(self._url)
        await self._start(origin)
        response = await self._page.goto(self._url, wait_until="domcontentloaded")
        if response is not None and getattr(response, "ok", True) is False:
            raise WebMCPBridgeError(f"WebMCP navigation failed with HTTP {response.status}.")
        webmcp_url(self._page.url, origins=self.origins)
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
        self._revision = registry_revision(self._page.url, tools)
        return {
            "schema_version": "webmcp.runtime.v1",
            "ok": True,
            "url": self._page.url,
            "origin": _canonical_origin(self._page.url),
            "title": await self._page.title(),
            "tool_count": len(tools),
            "tools": tools,
            "registry_revision": self._revision,
            "browser_profile": str(self.profile_path),
            "visible": not self.headless,
        }

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        path: str = "",
        wait_ms: int = 750,
        registry_revision: str = "",
    ) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            raise WebMCPBridgeError("WebMCP tool name must be a non-empty string.")
        if path or self._page is None:
            await self.open(path or "/", wait_ms=wait_ms)
        current = await self.list_tools(wait_ms=0)
        if registry_revision and registry_revision != current["registry_revision"]:
            raise WebMCPBridgeError(
                "WEBMCP_STALE_REGISTRY: the page tool registry changed; discover tools again."
            )
        discovered = next(
            (item for item in current["tools"] if item.get("name") == name.strip()), None
        )
        if discovered is None:
            raise WebMCPBridgeError(f"No WebMCP tool named {name.strip()} is registered.")
        try:
            from jsonschema import Draft202012Validator

            Draft202012Validator(discovered.get("inputSchema") or {}).validate(arguments or {})
        except Exception as error:
            raise WebMCPBridgeError(
                f"WebMCP arguments did not match the live tool schema: {error}"
            ) from error
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
        return {
            "schema_version": "webmcp.runtime.v1",
            "ok": True,
            "url": self._page.url,
            "origin": self._origin,
            "tool": name.strip(),
            "registry_revision": current["registry_revision"],
            "result": result,
        }

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": "webmcp.runtime.v1",
            "ok": True,
            "connected": self._page is not None,
            "url": getattr(self._page, "url", "") if self._page is not None else "",
            "origin": self._origin,
            "origins": list(self.origins),
            "registry_revision": self._revision,
            "visible": not self.headless,
        }

    async def close(self) -> dict[str, Any]:
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._page = self._context = self._playwright = None
        self._origin = ""
        self._revision = ""
        return {"schema_version": "webmcp.runtime.v1", "ok": True, "closed": True}
