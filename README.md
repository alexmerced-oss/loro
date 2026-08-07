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
python -m pip install "loro-agent[dev]"  # Development and test tools
```

## What It Includes

- A Typer-powered CLI entrypoint.
- Configuration loading from system, user, project, local, runtime, and managed enterprise sources.
- Permission decision primitives.
- Local and shared memory interfaces.
- Postgres and Iceberg shared memory adapters with explicit-only shared write flows.
- PyIceberg execution support for governed Iceberg shared-memory search and draft commits.
- Polaris client for read-only governed catalog discovery.
- Artifact generation for Markdown/DOCX documents, PPTX presentations, XLSX/CSV spreadsheets, and Markdown briefs.
- Artifact provenance sidecars that record prompt previews, generated paths, assumptions, and generator metadata.
- JSONL audit logging for runtime tasks, memory writes, and artifact creation.
- Durable session records with `loro sessions list` and `loro sessions show`.
- Permission-gated file and shell tools.
- Shared memory draft staging and Postgres/Iceberg schema output.
- Shared memory backend diagnostics.
- Safety scanner for obvious secrets before memory and artifact writes.
- AI provider profiles and `loro configure` setup wizard.
- Native tool-call normalization for OpenAI-compatible, Anthropic, Gemini, and Bedrock providers.

## Configure A Provider

Use the setup wizard or pass options directly:

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

For shell commands, use `--` before the command when passing flags to the child process.

Memory and artifact commands scan for obvious secrets by default. Use `--allow-sensitive` only when enterprise policy allows that content to be persisted.

## Documentation

- [Getting Started](docs/getting-started.md)
- [CLI Guide](docs/cli.md)
- [Configuration](docs/configuration.md)
- [AI Providers](docs/providers.md)
- [Memory](docs/memory.md)
- [Polaris And Iceberg](docs/polaris-iceberg.md)

## License

MIT
