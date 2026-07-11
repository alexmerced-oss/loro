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

Polaris should also be tested with an ephemeral local stack, but it is heavier than
Postgres because it needs a Polaris server, catalog bootstrap, roles/policies, and often
an Iceberg-facing catalog configuration. The recommended next step is a dedicated
`docker-compose.polaris.yml` plus pytest fixtures that:

- wait for Polaris readiness,
- bootstrap a catalog and namespace,
- create or load test roles/policies,
- run Loro's typed `data` commands against the local CLI/server.
