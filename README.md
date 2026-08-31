# Loro

Loro is a Python CLI agent harness for enterprise coding, governed data work, and productivity tasks.

"Loro" is Spanish for parrot: an intelligent, social bird that listens, learns, repeats useful knowledge, and helps information move across groups.

Loro `0.19.2` is the current release. The deliberately limited `0.10` stable core remains
the stabilization baseline. The OAP and AGS integrations remain pre-1.0 product surfaces, but are
implemented against the published 1.0 specifications and compatible 1.x support libraries.
See [Project Status](docs/project-status.md) for the precise boundary and remaining 1.0 gates.

## Install

```bash
python -m pip install loro-agent
loro --version
loro providers conformance
```

Optional extras:

```bash
python -m pip install "loro-agent[data]" # Postgres, Iceberg, and PyArrow support
python -m pip install "loro-agent[aws]"  # AWS Bedrock adapter support
python -m pip install "loro-agent[mcp]"  # MCP client support
python -m pip install "loro-agent[webmcp]" # MCP + Playwright bridge for alexmerced.app
python -m pip install "loro-agent[gateway]" # Discord and signed chat gateway support
python -m pip install "loro-agent[webui]" # Local multi-conversation Web UI
python -m pip install "loro-agent[dev]"  # Development and test tools
```

## 60-Second Quick Start

Start with the context-aware guide. It checks the current folder's provider, model, profile,
workspace, web, memory, MCP, sandbox, and audit readiness, then recommends the next commands:

```bash
loro get-started
loro get-started --topic setup
```

Loro ships with provider and profile setup wizards. Run them with no flags for interactive setup:

```bash
loro configure
loro setup profile
loro config check --strict
loro doctor
loro
```

Prefer a browser workspace? The optional local Web UI uses the same governed runtime and starts
only when explicitly requested:

```bash
python -m pip install "loro-agent[webui]"
loro web
```

It provides durable conversations, profile-backed bots, safe profile editing, streaming tool
activity and approvals, an Agentic Graph view that validates, plans and runs AGS 1.0 workflows with
human gates held for an explicit decision, and default provider/model/profile settings. Assistant replies render as
markdown, and each launch prints a URL carrying a fresh access token, so the API is closed to other
local processes. See the [Local Web UI guide](docs/webui.md).

Create a specialist from a normal description in the CLI or Profiles page:

```bash
loro agents generate "A cautious release reviewer that cites test evidence"
loro agents generate "A portable documentation specialist" --scope universal
```

The result is compiled into canonical OAP 1.0 and validated before it can be saved. The bundled
`oap-profile-authoring` skill and `profile.create` session tool let an agent propose a profile for
a useful subagent; an autonomous idea remains a reviewable proposal, while explicit saving still
requires trusted edit approval. `~/.agentprofiles` is the universal user root, below Loro's native
user root and project roots in discovery precedence.

The folder REPL opens with a responsive status panel that keeps Loro's ASCII parrot beside the
current folder, provider, model, agent, session, memory, sandbox, and audit state. On narrow
terminals the same information stacks inside one panel. Responses stream directly in a chat-style
turn, while tool calls show concise activity and timing before a compact usage/session footer.

Create a portable, governed AGS 1.0 plan when work needs explicit scheduling and approval:

```bash
loro graph generate "Create a release readiness report" --out release.agraph.yaml
loro graph validate release.agraph.yaml --strict
loro graph plan release.agraph.yaml
loro graph run release.agraph.yaml --dry-run
```

Loro supports AGS conformance level 3 with durable resume, model-tier routing, harness-evaluated
criteria, gates, branches, bounded loops/maps, subgraphs, parallel ready nodes, fallbacks, and
compensation. See the [Agentic Graph guide](docs/agentic-graphs.md).

Loro supports authenticated remote work through Slack, Discord, Telegram, Teams, Signal bridges,
and generic signed webhooks. Platform users map to tenant-scoped Loro identities, while remote
message text never carries approval authority. See [Channel Gateways](docs/channel-gateways.md).

Provider and gateway secrets can live in the operating-system credential vault, including multiple
named accounts for the same provider. Environment variables remain an override for automation. See
the [Credential Vault](docs/credentials.md).

The wizard loads the selected provider's current model catalog, then presents numbered, searchable,
paged model choices. Export the provider key before starting the wizard when its catalog requires
authentication. If discovery is unavailable, Loro falls back to bundled choices and still offers a
custom-model entry. Use `--no-discover-models` for a fully offline setup. For a no-key first run,
choose the `mock` provider. That lets you verify the CLI,
configuration loading, memory paths, artifact folders, and health checks before connecting a
paid model provider.

When you are ready to use a cloud model, set the provider API key in your shell and rerun the
wizard:

```bash
export OPENAI_API_KEY="<your-key>"
loro configure
loro providers check openai
loro run "Inspect README.md and suggest the next three improvements."
```

The wizard stores local settings in `.loro/config.local.toml`. API keys can remain in environment
variables or be addressed through OS-vault credential references; Loro does not write plaintext
keys into the config file.

Additional setup wizards are available for the enterprise pieces:

```bash
loro setup identity
loro setup approvals
loro setup sandbox
loro setup audit
loro setup memory
loro setup shared-memory
loro setup polaris
loro setup mcp
loro setup webmcp
loro setup mcp-server
loro setup gateway
loro setup skills
loro setup agents
loro setup quickstart
```

## What It Includes

- A Typer-powered CLI entrypoint.
- Configuration loading from system, user, project, local, runtime, and managed enterprise sources.
- Identity context from config/environment with managed required fields and audit/session propagation.
- Identity-bound approval records with interactive once/session/deny prompts and replay protection.
- Permission decision primitives.
- Normalized filesystem, shell, Git, memory, Polaris, provider, MCP, and session-message policy resources.
- Named subprocess profiles with minimized environments, bounded runtime/output, and optional Bubblewrap enforcement.
- Local and shared memory interfaces.
- Postgres and Iceberg shared memory adapters with explicit-only shared write flows.
- PyIceberg execution support for governed Iceberg shared-memory search and draft commits.
- Polaris client for read-only governed catalog discovery.
- Artifact generation for Markdown/DOCX documents, PPTX presentations, XLSX/CSV spreadsheets, and Markdown briefs.
- SHA-256-bound artifact provenance sidecars with `loro artifacts verify` integrity checks.
- Versioned JSONL/HTTP audit delivery with bounded buffering, retry, diagnostics, and flush.
- Durable session records and non-authoritative cross-session message delivery.
- Permission-gated file and shell tools.
- Shared memory draft staging and Postgres/Iceberg schema output.
- Shared memory backend diagnostics.
- Safety scanner for obvious secrets before memory and artifact writes.
- AI provider profiles and `loro configure` setup wizard.
- Native tool-call normalization for OpenAI-compatible, Anthropic, Gemini, and Bedrock providers.
- Dual-era MCP client support for tools, resources, and prompts through stdio or Streamable HTTP.
- Deny-by-default MCP extensions, durable experimental Tasks, and bounded modern subscriptions.
- Least-privilege MCP server mode with an explicit read-only export ceiling.
- Read-only Agentic Graph validation and planning over explicitly exported MCP tools.
- Digest-tracked Agent Skills with progressive loading, lifecycle controls, and reviewed installs.
- OS-keyring credential vault references with named provider and integration accounts.
- Signed, identity-mapped Slack, Discord, Telegram, Teams, Signal-bridge, and generic gateways.
- Open Agent Profile 1.0 Level 3 named agents with fail-closed narrowing, untrusted state,
  digest-bound proposals, canonical `/state`-only atomic writeback, a complete profile wizard, and
  optional default-profile selection. This remains experimental in Loro's pre-1.0 support matrix.
- An optional loopback-first React Web UI with append-only conversations, profile-backed bots,
  streamed runs and approvals, profile revision pinning, and redacted workspace settings. Every
  launch is token-gated, and rendered markdown never evaluates raw HTML from model output.

## Configure A Provider

Use the setup wizard:

```bash
loro configure
```

Or pass options directly for repeatable onboarding scripts:

```bash
loro providers list
loro providers show openai
export OPENAI_API_KEY="<your-key>"
loro configure --provider openai --model gpt-5.6-luna --small-model gpt-5.4-mini
loro providers check openai
```

For local Ollama:

```bash
loro configure --provider ollama --model llama3.2 --small-model llama3.2
```

`loro configure` writes `.loro/config.local.toml` by default. Keep provider secrets in
environment variables.

Additional setup wizards are available through `loro setup`:

```bash
loro setup provider
loro setup identity
loro setup approvals
loro setup audit
loro setup memory
loro setup shared-memory
loro setup polaris
loro setup quickstart
loro policy explain '{"tool":"shell","action":"run command","resource":{"kind":"shell","executable_name":"python"}}'
loro audit doctor
loro audit flush
loro audit verify
```

`loro setup shared-memory` supports Postgres and Iceberg. Shared memory writes remain
explicit-only and draft-gated.

The 0.6 data operations add checksummed Postgres migrations, idempotent operation IDs,
state/event reconciliation, verified backup manifests, an authenticated audit collector, and
content-free operational metrics:

```bash
loro memory migration-status
loro memory migrate --target 2 --execute
loro memory reconcile
loro operations backup --output /secure/loro-memory.dump --execute
loro operations verify-backup /secure/loro-memory.dump
loro audit collector-verify --path /var/lib/loro/audit.sqlite3
```

These are reference controls. Production TLS, immutable audit retention, protected Polaris
authorization, object-store behavior, and organization-approved RPO/RTO evidence remain
deployment-owned.

## Run Agentic Tasks

```bash
loro plan "Create a release readiness checklist"
loro run "Inspect README.md and suggest the next three improvements."
loro setup profile
loro agents create reviewer --instructions "Review changes and cite concrete evidence."
loro agents explain reviewer
loro run --agent reviewer "Review README.md"
```

Loro can use typed tools for file reads/searches, approved edits, approved shell commands,
Git helpers, memory search, Polaris discovery, and artifact creation. Write-like tools are
permission-gated.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
loro --help
python -m pytest
```

## Examples

```bash
loro remember --local "Status briefs should include risks, blockers, next steps, and owner."
loro remember --shared "Use the enterprise launch readiness template for launches."
loro memory drafts
loro memory commit-draft <draft-id>
loro memory shared-search "launch readiness" --tenant-id acme
loro memory lifecycle <memory-id> --action hold --reason "Approved hold" --execute
loro memory propose "Use concise launch summaries" --target local
loro memory propose "Use the enterprise launch readiness template" --target shared
loro memory accept-proposal <proposal-id>
loro memory schema --backend postgres
loro memory backend-check
loro docs create "Draft a project kickoff document"
loro slides create "Quarterly platform update"
loro sheets create "Launch readiness tracker"
loro brief meeting "Prepare for roadmap sync"
loro create docs "Create a practical guide to incident triage"
loro memory search "status briefs"
loro sessions list
loro file search "Polaris" --root .
loro file read PRD.md --limit 1000
loro shell run --yes -- python -c "print('hello from Loro')"
loro safety scan "api_key = 'abc123456789'"
loro safety doctor
loro providers list
loro providers show nous-portal
loro providers check nous
loro providers request "hello" --provider nous --model deepseek/deepseek-v4-flash
loro providers smoke "hello" --provider gemini --model gemini-3.6-flash --execute
loro providers smoke "hello" --provider anthropic --model claude-sonnet-5 --execute
loro providers smoke "hello" --provider opencode-zen --model deepseek-v4-flash --execute
loro providers request "hello" --provider trustedrouter --model trustedrouter/cheap
loro providers request "hello" --provider prime --model openai/gpt-oss-20b
loro configure --provider ollama --model llama3.2 --small-model llama3.2
loro data schema events --catalog prod --namespace analytics
loro data explain-access events --catalog prod --namespace analytics --catalog-role reader
```

Artifact commands contact the configured model by default and require a complete, validated draft
before writing any file. A malformed draft receives one correction attempt. If the resolved
provider is `mock`, the command stops and directs you to `loro configure` instead of silently
creating placeholder content. `--no-ai` is the explicit offline-scaffold mode.

`loro graph generate` and `loro plan --format agraph` likewise author and validate a governed graph
with the configured model. Add `--no-ai` only to request the conservative offline skeleton.

Generated files are written to `artifacts/` by default. Use `--output-dir` to choose another
location. Each generated artifact also gets a `.provenance.json` sidecar.

Configuration can be layered from `.loro/config.toml`, `LORO_CONFIG`, and
`LORO_CONFIG_CONTENT`. Enterprise-managed overlays can be supplied through
`/etc/loro/managed.toml`, `LORO_MANAGED_CONFIG`, or `LORO_MANAGED_CONFIG_CONTENT`; those
managed values are applied last.

Managed policy can be required and pinned with `LORO_MANAGED_CONFIG_REQUIRED` and
`LORO_MANAGED_CONFIG_SHA256`. Runtime task budgets cover model bytes/tokens/cost and tool calls;
provider transport supports bounded retries plus environment-backed enterprise CA/proxy paths.
See [External Enterprise Requirements](docs/external-enterprise-requirements.md) for deployment,
identity, database, catalog, audit, and governance evidence that must be supplied outside Loro.

For shell commands, use `--` before the command when passing flags to the child process.

Managed data protection classifies and scans model, memory, artifact, session, tool-output, and
audit flows. Use `--allow-sensitive` only for development policy that explicitly permits the
override; enterprise overlays can disable it.

## MCP Quick Start

Install the optional SDK, run the wizard, and verify the configured server:

```bash
python -m pip install "loro-agent[mcp]"
loro setup mcp
loro mcp list
loro mcp doctor SERVER_ID
loro mcp test SERVER_ID
```

The wizard can attach experimental modern MCP Tasks. Task handles are durable across Loro
processes, while input and cooperative cancellation remain permission and approval gated:

```bash
loro mcp task-start SERVER_ID TOOL_NAME --arguments '{}'
loro mcp tasks --server-id SERVER_ID
loro mcp task-get SERVER_ID TASK_ID
```

Unknown MCP extensions remain inert. Modern subscriptions are bounded by configured event,
duration, and output limits. See the [MCP guide](docs/mcp.md) for authentication, managed policy,
task input/cancellation, and compatibility details.

For the bundled alexmerced.app integration, install its optional browser boundary and configure
the reviewed stdio server in one command:

```bash
python -m pip install "loro-agent[webmcp]"
playwright install chromium
loro setup webmcp
```

Loro discovers the bundled `alexmerced-webmcp` skill during ordinary PDF, chart, data, media,
notes, planning, and WebMCP tasks. The skill routes calls through Loro's existing MCP
permission/profile boundary. The server is restricted to `https://alexmerced.app`, keeps a
dedicated browser profile under `~/.local/share/loro/webmcp/`, and exposes only navigation, live
tool discovery, and exact invocation. Webpage content cannot approve calls or widen policy.

Loro can also serve an explicit read-only capability subset and load digest-tracked Agent Skills:

```bash
loro setup mcp-server
loro mcp server-inspect
loro setup skills
loro skills list
loro skills import-claude ./plugin
loro skills import-pi ./package
```

Compatibility imports preview skill, MCP, and unsupported host components before any mutation;
execution requires the reviewed source digest. Loro does not execute Claude hooks/agents or Pi
TypeScript extensions as plugins. See [Agent Skills](docs/skills.md).

Saved sessions exchange durable, non-authoritative coordination messages:

```bash
loro sessions send SENDER_SESSION RECIPIENT_SESSION "Review is ready."
loro run --resume-session RECIPIENT_SESSION "Continue."
```

## Documentation

- [Getting Started](docs/getting-started.md)
- [Project Status](docs/project-status.md)
- [CLI Guide](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Identity Context](docs/identity.md)
- [Approvals](docs/approvals.md)
- [Subprocess Sandbox Profiles](docs/sandbox.md)
- [Managed Data Protection](docs/data-protection.md)
- [AI Providers](docs/providers.md)
- [Memory](docs/memory.md)
- [Reference Audit Collector](docs/audit-collector.md)
- [Backup, Restore, And Recovery](docs/recovery.md)
- [Polaris And Iceberg](docs/polaris-iceberg.md)
- [Model Context Protocol](docs/mcp.md)
- [alexmerced.app WebMCP](docs/webmcp.md)
- [MCP Support Matrix](docs/mcp-support-matrix.md)
- [Agent Skills](docs/skills.md)
- [Open Agent Profiles](docs/agent-profiles.md)
- [Cross-Session Messaging](docs/session-messaging.md)
- [Enterprise Beta Guide](docs/enterprise-beta.md)
- [Enterprise Operator Runbook](docs/operator-runbook.md)
- [Stabilization Support Policy](docs/support-policy.md)
- [0.9 Release Candidate Operations (Historical)](docs/release-candidate.md)
- [Independent Assurance Playbook](docs/assurance-playbook.md)
- [Consumer Release Verification](docs/consumer-verification.md)
- [Frozen Release Contract](docs/release-contract.json)
- [Roadmap To Loro 1.0](docs/roadmap-1.0.md)
- [Enterprise Evidence Register](docs/enterprise-evidence.md)

For a restricted enterprise beta, begin with the versioned bundle in `deploy/reference`, assign
the organization-owned controls, then capture a content-free local baseline:

```bash
loro config check --strict
loro doctor
loro operations benchmark --strict --output loro-benchmark.json
loro operations release-readiness --output loro-readiness.json
```

## License

MIT
