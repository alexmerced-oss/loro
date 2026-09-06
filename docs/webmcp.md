# WebMCP

Loro's optional WebMCP integration is a packaged stdio MCP server. This keeps Playwright outside
the core dependency set and puts every invocation through Loro's existing MCP permissions,
approvals, audit events, profile server allowlists, sandbox, and output limits.

```bash
python -m pip install "loro-agent[webmcp]"
playwright install chromium
loro setup webmcp
loro mcp doctor alexmerced-webmcp
```

The server exposes five stable operations:

- `webmcp_open`: navigate to an alexmerced.app path and capture its live WebMCP tools.
- `webmcp_list_tools`: return exact names, descriptions, schemas, and annotations for the page.
- `webmcp_call_tool`: invoke one exact discovered name with schema-compatible arguments.
- `webmcp_status`: report allowed origins and live browser state.
- `webmcp_close`: close the browser session and release its resources.

The bridge permits only configured exact HTTPS origins and defaults to `alexmerced.app`. Add a
reviewed set while configuring the MCP server:

```bash
loro setup webmcp --origins https://alexmerced.app,https://tools.example.com
```

It keeps one live page and a dedicated persistent browser profile per origin so IndexedDB and
localStorage survive between calls without crossing site boundaries. Tools are
page-scoped, so navigating invalidates the previous page's registry. The static discovery manifest
at `https://alexmerced.app/.well-known/webmcp.json` is useful for routing, but only the live page
registry can be invoked.

The browser is visible by default. `loro setup webmcp` forwards only display and WebMCP override
variables that exist at setup time; set `LORO_WEBMCP_HEADLESS=1` before setup for an unattended
compatibility-mode server.

The Web UI exposes the same boundary on the Extensions page. It displays allowed origins, live
schemas and annotations, requires one-off confirmation for tools not marked read-only, shows the
result, and can explicitly close the session. Every live registry has a SHA-256 revision; stale
invocations fail closed and must rediscover.

Webpage text and tool results are untrusted content. They do not carry user authority and cannot
approve a mutation, alter Loro configuration, widen an OAP profile, or add another origin. Normal
MCP permissions still decide whether a model may discover or invoke the server. Data-URI results
remain subject to the configured MCP output bound.
