# Model Context Protocol

Loro includes an MCP client foundation for the current stateless MCP `2026-07-28` revision and
classic handshake-based servers. The integration uses the official Python MCP SDK v2; install
the optional extra before connecting:

```bash
python -m pip install "loro-agent[mcp]"
```

Agent Skills, Tasks, subscriptions, MCP Apps, legacy HTTP+SSE, and Loro MCP server mode are not
implemented yet. OAuth and enterprise transport policy are implemented; later capability work
remains in the [MCP And Agent Skills Roadmap](mcp-skills-roadmap.md).

## Configure A Server

Use the wizard:

```bash
loro setup mcp
loro mcp list
loro mcp doctor
```

Or configure a stdio server directly:

```bash
loro mcp add filesystem \
  --transport stdio \
  --command npx \
  --arg=-y \
  --arg @modelcontextprotocol/server-filesystem \
  --arg /work/repos
```

Only environment variables named with repeated `--env` options are exposed beyond the MCP
SDK's minimal platform environment. Values remain in the process environment and are not
written to TOML:

```bash
export MCP_DATABASE_TOKEN="<token>"
loro mcp add database --command database-mcp --env MCP_DATABASE_TOKEN
```

Configure a Streamable HTTP server:

```bash
loro mcp add catalog \
  --transport streamable_http \
  --url https://mcp.example.internal/mcp \
  --credential-profile enterprise
```

URLs must be absolute HTTP(S) URLs and cannot contain embedded credentials. Enterprise OAuth,
issuer validation, SSRF/redirect controls, managed host allowlists, and stronger TLS policy can
be set globally under `[mcp]`.

## Credential Profiles

Credential profiles store only environment variable names and public OAuth metadata. Secret
values are never written to TOML. Create a bearer profile and attach it to a server:

```bash
export MCP_ENTERPRISE_TOKEN="<token>"
loro mcp auth-add enterprise --type bearer --token-env MCP_ENTERPRISE_TOKEN
loro mcp add catalog --transport streamable_http \
  --url https://mcp.example.internal/mcp --credential-profile enterprise
loro mcp auth-list
```

`loro mcp auth-remove enterprise` removes a profile after it has been detached from every
configured server.

Machine-to-machine OAuth client credentials use two environment references:

```bash
export MCP_CLIENT_ID="<client-id>"
export MCP_CLIENT_SECRET="<client-secret>"
loro mcp auth-add workload --type oauth_client_credentials \
  --client-id-env MCP_CLIENT_ID --client-secret-env MCP_CLIENT_SECRET \
  --scope mcp:tools --scope mcp:resources
```

Authorization-code OAuth uses Protected Resource Metadata and Authorization Server Metadata
discovery from the official SDK. The SDK validates resource and issuer relationships. Loro
prefers a URL-based Client ID Metadata Document (CIMD):

```bash
loro mcp auth-add employee --type oauth_authorization_code \
  --client-metadata-url https://agents.example.com/loro/client-metadata.json \
  --redirect-uri http://127.0.0.1:8765/callback \
  --scope mcp:tools
```

On first use, Loro prints the authorization URL and asks for the final callback URL. This flow
requires an interactive terminal. Dynamic Client Registration is blocked by default; enable
`--allow-dynamic-registration` only for a reviewed legacy authorization server. Tokens are held
in process memory for the connection and are not persisted by Loro.

Equivalent TOML:

```toml
[mcp]
enabled = true
require_https = true
allow_loopback_http = true
allowed_hosts = ["mcp.example.internal", "*.mcp.example.internal"]
block_private_networks = true
follow_redirects = false
allowed_stdio_commands = ["/usr/bin/npx", "/opt/loro-mcp/*"]
max_output_bytes = 1000000
max_pagination_pages = 20
allow_input_required = false
input_required_max_rounds = 3

[mcp.credential_profiles.enterprise]
type = "bearer"
token_env = "MCP_ENTERPRISE_TOKEN"

[mcp.servers.filesystem]
enabled = true
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/work/repos"]
env_allowlist = []
protocol_mode = "auto"
allowed_protocol_versions = ["2026-07-28", "2025-11-25", "2024-11-05"]
minimum_protocol_version = "2025-11-25"
timeout_seconds = 30

[mcp.servers.catalog]
transport = "streamable_http"
url = "https://mcp.example.internal/mcp"
credential_profile = "enterprise"
```

`protocol_mode = "auto"` prefers `2026-07-28` discovery and falls back to classic
initialization. Use `legacy` to force the handshake path or `2026-07-28` to pin the modern path.
`allowed_protocol_versions` rejects unexpected negotiation results. A managed
`minimum_protocol_version` prevents silent downgrade below enterprise policy.

`block_private_networks` rejects HTTP hosts whose preflight DNS answers are private, link-local,
reserved, or otherwise non-public. Loopback endpoints remain available for local container
testing. DNS preflight cannot fully eliminate rebinding between resolution and connection;
enterprise egress controls and internal DNS policy remain the authoritative network boundary.
Redirects are disabled by default because an allowed endpoint could otherwise redirect to a
different trust zone.

## Inspect And Use

Local inspection does not connect:

```bash
loro mcp list
loro mcp inspect filesystem
loro mcp doctor filesystem
```

Connection and discovery commands:

```bash
loro mcp test filesystem
loro mcp tools filesystem
loro mcp resources filesystem
loro mcp read filesystem file:///work/repos/README.md
loro mcp prompts filesystem
loro mcp prompt filesystem summarize --arguments '{"audience":"engineering"}'
```

`mcp test` negotiates a connection and lists capability counts; it never invokes a tool.
Resources, prompts, tool results, server instructions, and metadata are untrusted content even
when the server itself is approved.

New-protocol multi-round input is disabled by default. When `allow_input_required = true`, Loro
permits up to `input_required_max_rounds` retries and requires terminal approval before accepting
each elicitation response.
Sampling, roots, logging opt-in, and classic server callbacks remain unadvertised and denied.

Tool invocation requires the `mcp` permission. Its default is `ask`:

```bash
loro mcp call filesystem read_file --arguments '{"path":"README.md"}'
```

The approval displays the exact call. Audit records contain an argument digest and argument
names, not raw argument values. `--yes` is available only when non-interactive approvals are
allowed; managed enterprise configuration should normally disable it.

## Agent Runtime

When MCP is enabled, Loro exposes protocol-neutral runtime tools:

- `mcp.tools`: `{"server_id":"filesystem"}`
- `mcp.call`: `{"server_id":"filesystem","tool_name":"read_file","arguments":{"path":"README.md"}}`
- `mcp.resources`: `{"server_id":"filesystem"}`
- `mcp.read`: `{"server_id":"filesystem","uri":"file:///work/repos/README.md"}`
- `mcp.prompts`: `{"server_id":"filesystem"}`
- `mcp.prompt`: `{"server_id":"filesystem","prompt_name":"summarize","arguments":{}}`

Every model-originated MCP operation enters the ordinary Loro permission and approval path. A
model-provided `approved=true` value is rejected. Connection metadata in runtime audit events
includes server id, transport, negotiated protocol revision, and lifecycle.

## Permission Rules

MCP normalized resources contain `operation`, `server_id`, `transport`, `endpoint`, `name`,
`argument_names`, and `arguments_digest`. Rules can restrict one server or operation:

```toml
[permissions]
mcp = "ask"

[[permissions.rules]]
tool = "mcp"
action = "list*"
resource_kind = "mcp"
decision = "allow"

[permissions.rules.resource]
server_id = "filesystem"

[[permissions.rules]]
tool = "mcp"
action = "call*"
resource_kind = "mcp"
decision = "deny"

[permissions.rules.resource]
server_id = "unapproved-*"
```

Explicit CLI discovery commands satisfy `ask` because the terminal user directly requested the
read. A matching `deny` still blocks connection. Model-directed operations do not receive that
implicit approval.

## Current Verification

- Hermetic tests cover configuration, registry behavior, pagination, protocol allowlists,
  downgrade rejection, host/TLS/DNS policy, DCR denial, credential isolation, output bounds,
  permission denial, explicit approval, redacted audit, and runtime use.
- Official SDK in-process tests exercise both `auto` stateless and `legacy` handshake modes.
- stdio and Streamable HTTP use SDK-provided transports.
- `2024-11-05` remains a compatibility target; it is not yet an advertised conformance-tested
  combination.
