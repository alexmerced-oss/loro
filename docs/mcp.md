# Model Context Protocol

Loro includes an MCP client foundation for the current stateless MCP `2026-07-28` revision and
classic handshake-based servers. The integration uses the official Python MCP SDK v2; install
the optional extra before connecting:

```bash
python -m pip install "loro-agent[mcp]"
```

OAuth, enterprise transport policy, a deny-by-default extension registry, experimental Tasks,
bounded modern subscriptions, and least-privilege Loro server mode are implemented. Agent
Skills use the open filesystem format rather than an MCP extension. MCP Apps and legacy
HTTP+SSE remain unsupported.

## Configure A Server

Use the wizard:

```bash
loro setup mcp
loro mcp list
loro mcp doctor
```

The wizard can register and attach the experimental Tasks extension when the server uses modern
or automatic protocol negotiation.

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
task_store_path = ".loro/mcp-tasks"
subscription_max_events = 100
subscription_max_seconds = 30

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

## Extensions And Tasks

Extensions must be configured globally, attached to a server, accepted by the managed
`allowed_extensions` list when one is present, implemented by a trusted Loro adapter, and
advertised by the remote server. Unknown identifiers and adapters remain visible but inert.

Register and attach the experimental Tasks extension:

```bash
loro mcp extension-add io.modelcontextprotocol/tasks \
  --version draft --adapter tasks
loro mcp add tasks-server --command tasks-mcp \
  --extension io.modelcontextprotocol/tasks
loro mcp extensions tasks-server
```

Equivalent configuration:

```toml
[mcp]
allowed_extensions = ["io.modelcontextprotocol/tasks"]
task_store_path = ".loro/mcp-tasks"

[mcp.extensions."io.modelcontextprotocol/tasks"]
enabled = true
version = "draft"
adapter = "tasks"
settings = {}

[mcp.servers.tasks-server]
command = "tasks-mcp"
extensions = ["io.modelcontextprotocol/tasks"]
allowed_protocol_versions = ["2026-07-28"]
```

Tasks are currently an experimental MCP extension and are available only with modern MCP
`2026-07-28`. Loro persists opaque task handles locally so a later process can reconnect and
poll. The store does not contain MCP credentials. Task input and cooperative cancellation each
require a fresh policy/approval decision; duplicate or unknown input keys fail before network
transmission. Cancellation acknowledgment records intent and does not claim the task is
cancelled until a later `task-get` reports that terminal state.

```bash
loro mcp task-start tasks-server build_report --arguments '{"quarter":"Q2"}'
loro mcp tasks --server-id tasks-server
loro mcp task-get tasks-server TASK_ID
loro mcp task-update tasks-server TASK_ID --responses '{"format":"pptx"}'
loro mcp task-cancel tasks-server TASK_ID
```

Modern change subscriptions are always bounded by configuration and optional lower command
limits:

```bash
loro mcp listen tasks-server --tools --max-events 10 --max-seconds 15
loro mcp listen tasks-server --resource-uri catalog://reports/Q2
```

MCP Apps are intentionally unsupported. Loro will not render extension-provided applications
until a sandboxed application host, capability policy, and adversarial test suite exist.

## Loro MCP Server Mode

Server mode exposes only an explicit subset of Loro's read-only file and Git tools. Shell,
writes, Git mutations, local/shared memory, governed data, credentials, artifacts, and nested
MCP calls cannot be exported.

```bash
loro setup mcp-server
loro mcp server-inspect
loro mcp serve
```

```toml
[mcp.server]
enabled = true
transport = "stdio"
host = "127.0.0.1"
port = 8766
export_tools = ["file.read", "file.search", "git.status", "git.diff", "git.show"]
export_resources = true
export_prompts = true
```

Streamable HTTP binds only to loopback. Put remote deployments behind a reviewed authenticated
enterprise gateway; Loro does not treat network reachability as authentication. The same
official SDK server handles modern stateless and classic initialized clients.

## Agent Runtime

When MCP is enabled, Loro exposes protocol-neutral runtime tools:

- `mcp.tools`: `{"server_id":"filesystem"}`
- `mcp.call`: `{"server_id":"filesystem","tool_name":"read_file","arguments":{"path":"README.md"}}`
- `mcp.resources`: `{"server_id":"filesystem"}`
- `mcp.read`: `{"server_id":"filesystem","uri":"file:///work/repos/README.md"}`
- `mcp.prompts`: `{"server_id":"filesystem"}`
- `mcp.prompt`: `{"server_id":"filesystem","prompt_name":"summarize","arguments":{}}`
- `mcp.task_start`: `{"server_id":"tasks-server","tool_name":"build_report","arguments":{}}`
- `mcp.task_get`: `{"server_id":"tasks-server","task_id":"TASK_ID"}`
- `mcp.task_update`: `{"server_id":"tasks-server","task_id":"TASK_ID","responses":{}}`
- `mcp.task_cancel`: `{"server_id":"tasks-server","task_id":"TASK_ID"}`

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

- Hermetic tests cover configuration, registry behavior, inert extensions, durable task restart,
  input deduplication, cooperative cancellation, bounded subscriptions, pagination, protocol allowlists,
  downgrade rejection, host/TLS/DNS policy, DCR denial, credential isolation, output bounds,
  permission denial, explicit approval, redacted audit, and runtime use.
- Official SDK in-process tests exercise both `auto` stateless and `legacy` handshake modes.
- SDK adapter tests verify the Tasks extension claim and `Mcp-Name` task routing aliases.
- stdio and Streamable HTTP use SDK-provided transports.
- stdio commands are normalized through the selected sandbox profile. An `execve` launcher
  removes environment defaults restored by the SDK, preserving only profile variables and the
  server's explicit `env_allowlist` without placing secret values in argv.
- `2024-11-05` remains a compatibility target; it is not yet an advertised conformance-tested
  combination.
- The scheduled MCP Conformance workflow exercises explicit official server and client scenarios
  for `2025-11-25`, plus official SDK interoperability for `2026-07-28`. The published runner has
  no `2026-07-28` scenarios yet. See the
  [support matrix](mcp-support-matrix.md); only green workflow artifacts count as release proof.
