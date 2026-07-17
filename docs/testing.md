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
- Live model-provider completions for cloud providers.
- Bedrock adapter behavior once AWS SDK support is implemented.

## Polaris Local Testing

Polaris can be tested against the local quickstart stack documented in
`docs/local-polaris-iceberg.md`. It is heavier than Postgres because it needs a Polaris
server, catalog bootstrap, roles/policies, and an Iceberg-facing catalog configuration.

```bash
LORO_INTEGRATION_POLARIS=1 python -m pytest -m integration tests/integration/test_polaris_cli_integration.py
```

The test expects a working Polaris CLI and at least one catalog available to list.
