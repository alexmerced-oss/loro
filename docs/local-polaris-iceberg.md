# Local Polaris And Iceberg Testing

This guide describes the recommended local stack for Batch 3 development.

## Polaris Quickstart

Apache Polaris publishes a Docker Compose quickstart for local evaluation. It starts
Polaris plus object storage and bootstraps a sample catalog and principal.

```bash
curl -s https://raw.githubusercontent.com/apache/polaris/refs/heads/main/site/content/guides/quickstart/docker-compose.yml \
  | docker compose -p polaris-quickstart -f - up -d
```

Useful local URLs:

- Polaris REST API: `http://localhost:8181`
- Object storage console: `http://localhost:9001`

Stop the stack:

```bash
docker compose -p polaris-quickstart down
```

The quickstart logs print credentials and example commands for the bootstrapped principal.

## Loro Configuration

After the quickstart is running and the Polaris CLI is available, configure Loro:

```toml
[polaris]
enabled = true
cli_path = "polaris"
catalog = "quickstart_catalog"
require_role_inspection = true

[memory.shared]
enabled = true
backend = "iceberg"
iceberg_namespace = "agent_memory"
iceberg_table = "shared_memories"
```

Install optional data dependencies for Iceberg/PyIceberg checks:

```bash
python -m pip install -e ".[data]"
```

## Smoke Checks

```bash
loro doctor
loro memory backend-check
loro data catalogs
loro data namespaces --catalog quickstart_catalog
loro data tables --catalog quickstart_catalog --namespace default
loro data schema <table> --catalog quickstart_catalog --namespace default
loro data explain-access <table> --catalog quickstart_catalog --namespace default
```

`loro memory backend-check` validates local Iceberg client readiness. Governed table
discovery still flows through Polaris so Loro can preserve catalog policy boundaries.

## Optional Integration Test Shape

Automated Polaris tests are intentionally gated because they require Docker, Polaris CLI
authentication, and a bootstrapped catalog.

```bash
LORO_INTEGRATION_POLARIS=1 python -m pytest -m integration tests/integration/test_polaris_cli_integration.py
```

The integration test expects:

- `polaris` or the configured CLI binary on `PATH`
- `[polaris].enabled = true`
- A local Polaris server reachable by the CLI
- At least one catalog available to list

## Notes

- The quickstart is for local development, not production.
- Do not place secrets in Iceberg or Polaris table/catalog properties.
- Use Polaris roles, policies, and privileges for governed access explanations.
