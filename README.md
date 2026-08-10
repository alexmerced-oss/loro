# Loro

Loro is a Python CLI agent harness for enterprise coding, governed data work, and productivity tasks.

"Loro" is Spanish for parrot: an intelligent, social bird that listens, learns, repeats useful knowledge, and helps information move across groups.

## Install

```bash
python -m pip install loro-agent
loro --version
```

Optional extras:

```bash
python -m pip install "loro-agent[data]" # Postgres, Iceberg, and PyArrow support
python -m pip install "loro-agent[aws]"  # AWS Bedrock adapter support
python -m pip install "loro-agent[mcp]"  # MCP client support
python -m pip install "loro-agent[dev]"  # Development and test tools
```

## 60-Second Quick Start

Loro ships with a provider setup wizard. Run it with no flags for an interactive setup:

```bash
loro configure
loro doctor
loro plan "Create a release readiness checklist for this project."
```

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

Loro does not currently expose Slack, Discord, or Telegram bot gateways. It is a local CLI and MCP
harness; model-provider gateway support is a separate capability. See
[Channel Gateways](docs/channel-gateways.md) for the exact boundary and planned security model.

For a no-key first run, choose the `mock` provider in the wizard. That lets you verify the CLI,
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

The wizard stores local settings in `.loro/config.local.toml`. API keys stay in environment
variables; Loro does not write them into the config file.

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
loro setup mcp-server
loro setup skills
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
- Artifact provenance sidecars that record prompt previews, generated paths, assumptions, and generator metadata.
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

## Run Agentic Tasks

```bash
loro plan "Create a release readiness checklist"
loro run "Inspect README.md and suggest the next three improvements."
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
loro configure --provider ollama --model llama3.2 --small-model llama3.2
loro data schema events --catalog prod --namespace analytics
loro data explain-access events --catalog prod --namespace analytics --catalog-role reader
```

Generated files are written to `artifacts/` by default. Use `--output-dir` to choose another location. Each generated artifact also gets a `.provenance.json` sidecar.

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

Loro can also serve an explicit read-only capability subset and load digest-tracked Agent Skills:

```bash
loro setup mcp-server
loro mcp server-inspect
loro setup skills
loro skills list
```

Saved sessions exchange durable, non-authoritative coordination messages:

```bash
loro sessions send SENDER_SESSION RECIPIENT_SESSION "Review is ready."
loro run --resume-session RECIPIENT_SESSION "Continue."
```

## Documentation

- [Getting Started](docs/getting-started.md)
- [CLI Guide](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Identity Context](docs/identity.md)
- [Approvals](docs/approvals.md)
- [Subprocess Sandbox Profiles](docs/sandbox.md)
- [Managed Data Protection](docs/data-protection.md)
- [AI Providers](docs/providers.md)
- [Memory](docs/memory.md)
- [Polaris And Iceberg](docs/polaris-iceberg.md)
- [Model Context Protocol](docs/mcp.md)
- [MCP Support Matrix](docs/mcp-support-matrix.md)
- [Agent Skills](docs/skills.md)
- [Cross-Session Messaging](docs/session-messaging.md)
- [Development Roadmap](docs/roadmap.md)
- [MCP And Agent Skills Roadmap](docs/mcp-skills-roadmap.md)
- [Enterprise Readiness Roadmap](docs/enterprise-readiness-roadmap.md)
- [Enterprise Evidence Register](docs/enterprise-evidence.md)

## License

MIT
