# Testing

Run the standard suite:

```bash
python -m pytest
python -m ruff check .
python -m compileall src tests
```

Install development extras to enable coverage reporting:

```bash
python -m pip install -e ".[dev]"
python -m pytest --cov --cov-report=term-missing
```

CI enforces the coverage threshold configured in `pyproject.toml`. The initial threshold is
set conservatively so it can protect the MVP while the runtime and integration surfaces are
still growing.

## Continuous Integration

The main GitHub Actions workflow runs on pushes and pull requests to `main`:

- install `.[dev]`
- `python -m ruff check .`
- `python -m pytest --cov --cov-report=term-missing --cov-report=xml`
- `python -m compileall src tests`

The `Integration` workflow runs Postgres and the pinned Polaris `1.5.0` quickstart weekly and can
also be dispatched manually. The credentialed Polaris CLI job remains manual for a preconfigured
protected runner.

The `Security Evidence` workflow runs on changes, weekly, and on demand. It enforces dependency,
static, secret-baseline, license, SBOM, overall coverage, and module-specific branch-aware
coverage gates. Artifacts are retained for 30 days. Current module floors are encoded in
`scripts/check_security_coverage.py`.

## Integration Tests

Postgres integration tests use an ephemeral local container through Testcontainers:

```bash
python -m pip install -e ".[dev,integration]"
LORO_INTEGRATION_POSTGRES=1 python -m pytest -m integration tests/integration
```

These tests require Docker or a Docker-compatible runtime. They create a temporary
Postgres container, apply the shared-memory schema through `PostgresSharedMemoryStore`,
commit a real shared-memory draft,
verify rows, and tear the container down.

Run normal tests without integrations:

```bash
python -m pytest -m "not integration"
```

Current high-value gaps that need external services or workflow infrastructure:

- Live Iceberg/Polaris governed execution.
- Bedrock requests with real AWS credentials.
- Production-gateway MCP authorization and hostile remote-server testing.
- Corporate secret-baseline adjudication and risk acceptance.
- Production audit collector load/outage evidence and externally retained hash anchors.

## Agentic Graph Tests

`tests/test_agraph.py` runs the AGS repository's valid examples and all invalid conformance
fixtures, asserting their normative diagnostic codes. It also covers duplicate-key rejection,
canonical digests, strict AGX typing, managed policy, routing refusal, fan-out planning, durable
schema-conformant execution, gate pause/resume, generation, and CLI help paths.

```bash
python -m pytest tests/test_agraph.py -q
for graph in docs/examples/agraph/*.agraph.yaml; do
  loro graph validate "$graph" --strict
done
```

The `AGS Conformance` workflow pins the upstream specification commit and runs these tests on
Python 3.11 and 3.14. Live model execution is intentionally outside this hermetic suite; use a
protected provider smoke environment and a low cost ceiling for that evidence.

## MCP Tests

The `dev` extra includes the official MCP SDK. Standard CI runs hermetic adapter tests and an
in-process server in both `auto` (`2026-07-28`) and `legacy` handshake modes:

```bash
python -m pytest tests/test_mcp.py tests/test_mcp_sdk.py tests/test_mcp_tasks.py
agentskills validate tests/fixtures/skills/python-review
```

These tests never contact a remote MCP endpoint. They cover client policy, inert extensions,
Tasks, server export restrictions, Agent Skills, and cross-session authority boundaries. The
scheduled `MCP Conformance` workflow runs the official MCP runner against Loro's server and
client roles for `2025-11-25`, runs official SDK interoperability for both advertised revisions,
and uploads its results as release evidence. The runner does not yet publish `2026-07-28`
scenarios.

## Live Provider Smoke Checks

Run live provider checks only when credentials, spend controls, and enterprise policy allow
external model calls. These commands exercise Loro's provider adapters; agent-loop smokes can
then be run with `loro run` and an isolated temp workspace.

```bash
NOUS_API_KEY="$NOUS_API_KEY" \
  loro providers smoke "Reply with exactly: ok" \
  --provider nous --model deepseek/deepseek-v4-flash --execute

OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  loro providers smoke "Reply with exactly: ok" \
  --provider openrouter --model deepseek/deepseek-v4-flash --execute

OPENCODE_ZEN_API_KEY="$OPENCODE_ZEN_API_KEY" \
  loro providers smoke "Reply with exactly: ok" \
  --provider opencode-zen --model deepseek-v4-flash --execute

ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  loro providers smoke "Reply with exactly: ok" \
  --provider anthropic --model claude-sonnet-5 --execute

OPENAI_API_KEY="$OPENAI_API_KEY" \
  loro providers smoke "Reply with exactly: ok" \
  --provider openai --model gpt-5.6-luna --execute

GEMINI_API_KEY="$GEMINI_API_KEY" \
  loro providers smoke "Reply with exactly: ok" \
  --provider gemini --model gemini-3.6-flash --execute
```

Loro has live-tested compatibility guards for current model families that reject deprecated
sampling parameters:

- OpenAI `gpt-5*`: omit `temperature`.
- Anthropic `claude-sonnet-5*`: omit `temperature`.
- Gemini `gemini-3.6-flash` and `gemini-3.5-flash-lite`: omit `generationConfig.temperature`.

## Polaris Local Testing

Polaris can be tested against the local quickstart stack documented in
`docs/local-polaris-iceberg.md`. It is heavier than Postgres because it needs a Polaris
server, catalog bootstrap, roles/policies, and an Iceberg-facing catalog configuration.

```bash
LORO_INTEGRATION_POLARIS=1 python -m pytest -m integration tests/integration/test_polaris_cli_integration.py
```

The test expects a working Polaris CLI and at least one catalog available to list.
