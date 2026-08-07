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

Optional integration tests run through the manual `Integration` workflow.

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

Current high-value gaps that need external services or credentials:

- Live Iceberg/Polaris governed execution.
- Bedrock requests with real AWS credentials.

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
