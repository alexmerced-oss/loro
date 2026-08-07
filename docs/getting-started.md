# Getting Started

This guide gets a new user from installation to their first Loro agent task.

## Install

Install the CLI from PyPI:

```bash
python -m pip install loro-agent
loro --version
```

Optional extras:

```bash
python -m pip install "loro-agent[data]"      # Postgres, Iceberg, and PyArrow support
python -m pip install "loro-agent[aws]"       # AWS Bedrock adapter support
python -m pip install "loro-agent[dev]"       # Development and test tools
```

For isolated installs, `pipx` works well:

```bash
pipx install loro-agent
```

## Configure An AI Provider

For the fastest path, run the interactive provider wizard:

```bash
loro configure
loro doctor
```

Choose `mock` if you want to verify the CLI without an API key. Choose a cloud provider when
you have the matching API key available in your shell.

List built-in providers:

```bash
loro providers list
loro providers show openai
```

Configure a local provider profile:

```bash
export OPENAI_API_KEY="<your-key>"
loro configure --provider openai --model gpt-5.6-luna --small-model gpt-5.4-mini
loro providers check openai
```

Other live-tested cloud provider examples:

```bash
export NOUS_API_KEY="<your-nous-key>"
loro providers smoke "hello" --provider nous --model deepseek/deepseek-v4-flash --execute

export OPENROUTER_API_KEY="<your-openrouter-key>"
loro providers smoke "hello" --provider openrouter --model deepseek/deepseek-v4-flash --execute

export OPENCODE_ZEN_API_KEY="<your-opencode-key>"
loro providers smoke "hello" --provider opencode-zen --model deepseek-v4-flash --execute

export ANTHROPIC_API_KEY="<your-anthropic-key>"
loro providers smoke "hello" --provider anthropic --model claude-sonnet-5 --execute

export GEMINI_API_KEY="<your-gemini-key>"
loro providers smoke "hello" --provider gemini --model gemini-3.6-flash --execute
```

For local Ollama:

```bash
loro configure --provider ollama --model llama3.2 --small-model llama3.2
loro providers check ollama
```

`loro configure` writes `.loro/config.local.toml` by default. Keep API keys in environment
variables, not in config files.

Current wizard scope: `loro configure` guides AI provider setup. Memory, Postgres/Iceberg, and
Polaris settings are configured through TOML today.

## Run Agentic Tasks

Planning mode:

```bash
loro plan "Create a release readiness checklist for this project."
```

Run mode:

```bash
loro run "Inspect README.md and suggest the next three improvements."
```

Loro can use typed runtime tools when the model asks for them. You can also provide an
explicit tool directive for deterministic local workflows:

```bash
loro run 'Read the README.
@tool {"name": "file.read", "args": {"path": "README.md", "limit": 4000}}'
```

Write-like runtime actions are permission-gated. When policy is `ask`, tool calls must include
`"approved": true`, and `deny` always blocks execution.

## Use Memory

Local memory stays private to the current environment:

```bash
loro remember --local "Status updates should include risks, blockers, next steps, and owner."
loro memory search "status updates"
```

Shared enterprise memory is explicit-only. Loro can propose and stage shared memories, but it
does not autonomously commit them:

```bash
loro remember --shared "Use the enterprise launch readiness template for launches." \
  --tenant-id acme --scope-type team --scope-key platform
loro memory drafts
loro memory commit-draft <draft-id>
loro memory commit-draft <draft-id> --execute
```

## Create Productivity Artifacts

```bash
loro docs create "Draft a project kickoff document"
loro slides create "Quarterly platform update"
loro sheets create "Launch readiness tracker"
loro brief meeting "Prepare for roadmap sync"
```

Generated files go to `artifacts/` by default and include provenance sidecars.

## Governed Data

Enable Polaris when your enterprise catalog is configured:

```toml
[polaris]
enabled = true
cli_path = "polaris"
catalog = "prod"
```

Then inspect governed metadata:

```bash
loro data catalogs
loro data tables --catalog prod --namespace analytics
loro data schema events --catalog prod --namespace analytics
loro data explain-access events --catalog prod --namespace analytics --catalog-role reader
```

Polaris commands are constrained to read-only discovery operations.

## Health Checks

```bash
loro doctor
loro providers smoke "hello" --provider mock --execute --stream
loro memory backend-check
```

Provider smoke checks are dry-run by default. Pass `--execute` only when credentials, budgets,
and enterprise policy allow live provider calls.
