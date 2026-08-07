# Polaris, Object Storage, And DuckDB CI

Loro can run a containerized Polaris test environment in GitHub Actions through the manual
`Integration` workflow. This is intentionally opt-in because the stack starts containers,
downloads the current Apache Polaris quickstart compose file, and may take longer than the
default unit-test workflow.

## Workflow Trigger

Open the `Integration` workflow in GitHub Actions and set:

```text
polaris_quickstart = true
```

This job:

- Downloads the official Apache Polaris quickstart Docker Compose file.
- Starts Polaris plus the quickstart S3-compatible object store.
- Waits for the Polaris REST API on `http://localhost:8181`.
- Installs Loro with `.[dev,data]` and `duckdb`.
- Uses DuckDB to create deterministic CSV and Parquet seed files in `/tmp/loro-ci-seed`.
- Runs Loro Iceberg shared-memory schema rendering and dry-run shared-memory search.
- Runs `loro memory backend-check` to verify PyIceberg and env-backed REST catalog settings.

The quickstart currently uses RustFS. Older Polaris guides and MinIO-specific examples are
still useful, but the default CI path should follow the upstream quickstart to reduce
maintenance.

## Why DuckDB Is In The Stack

DuckDB is useful for deterministic, lightweight seed data generation:

```sql
CREATE TABLE seed_memories AS
SELECT
  'seed-1' AS memory_id,
  'acme' AS tenant_id,
  'team' AS scope_type,
  'platform' AS scope_key,
  'Use launch readiness briefs with owners and blockers.' AS content;

COPY seed_memories TO '/tmp/loro-ci-seed/seed_memories.parquet' (FORMAT PARQUET);
```

DuckDB should be treated as a seed-file generator unless the project explicitly adopts a
DuckDB Iceberg writer path. For governed Iceberg table creation and writes, prefer one of:

- PyIceberg against the Polaris REST catalog.
- Spark SQL using the Iceberg Spark runtime.
- Trino using the Iceberg connector pointed at Polaris.

## Test Levels

### Level 1: Service And Loro Dry Runs

This is what the current `polaris_quickstart` job performs. It verifies that the container
stack starts and that Loro can render Iceberg shared-memory SQL and detect PyIceberg readiness.
It does not require committing data to Iceberg.

### Level 2: Polaris CLI Read-Only Tests

The existing `polaris` workflow input runs:

```bash
LORO_INTEGRATION_POLARIS=1 python -m pytest -m integration tests/integration/test_polaris_cli_integration.py
```

That path expects a configured Polaris CLI and is best for self-hosted runners or runners with
preloaded CLI authentication.

### Level 3: Full Governed Iceberg Execution

A future CI job can create the Loro shared-memory Iceberg tables, commit a shared-memory draft,
and search it back through PyIceberg. That requires one additional bootstrapping step:

- Extract or provide Polaris quickstart credentials.
- Configure `LORO_ICEBERG_CREDENTIAL` or `LORO_ICEBERG_TOKEN`.
- Create the Iceberg namespace and tables through PyIceberg, Spark, or Trino before running
  `loro memory commit-draft <draft-id> --execute`.

Until that is automated, Level 3 should stay manual or run only on a protected self-hosted
runner.

## Storage Choice

Use the official quickstart stack for default CI. It currently starts Polaris plus RustFS.
Use MinIO only when specifically testing MinIO-backed catalog behavior or compatibility with
older Polaris examples. The Loro side of the test should not care which S3-compatible object
store backs Polaris as long as the Iceberg REST catalog returns valid metadata and vended
credentials.
