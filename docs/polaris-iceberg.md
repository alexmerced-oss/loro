# Polaris And Iceberg

Loro's governed data layer is centered on Apache Polaris and Apache Iceberg.

## Polaris

Polaris provides governed catalog concepts such as catalogs, namespaces, tables, views, principals, roles, privileges, and policies. Loro should use Polaris to discover what the user can access and explain access denials when allowed.

Current MVP behavior:

```bash
loro data catalogs
loro data catalog prod
loro data namespaces --catalog prod
loro data namespace analytics --catalog prod
loro data tables --catalog prod --namespace analytics
loro data table events --catalog prod --namespace analytics
loro data schema events --catalog prod --namespace analytics
loro data explain-access events --catalog prod --namespace analytics --catalog-role reader
loro data views --catalog prod --namespace analytics
loro data view daily_events --catalog prod --namespace analytics
loro data principal-roles
loro data principal-role analyst
loro data catalog-roles --catalog prod
loro data catalog-role reader --catalog prod
loro data privileges --catalog prod --catalog-role reader
loro data policies --catalog prod
loro data policy pii-mask --catalog prod
loro data applicable-policies events --catalog prod --namespace analytics
loro data polaris catalogs list
```

Typed `data` commands call the Polaris CLI through a constrained client. The lower-level
`data polaris` command remains available for read-only operations and validates the requested
operation against an allowlist before executing the Polaris CLI.

`loro data schema` returns the read-only Polaris table metadata payload for a table. `loro data
explain-access` runs read-only table, applicable-policy, and privilege discovery checks and
returns a JSON explanation. These commands do not grant access or query data; they help explain
what the configured identity can discover through Polaris.

## Iceberg

Iceberg is the target high-scale backend for shared enterprise memory and governed data table access. The initial implementation should treat Iceberg as an append-friendly, auditable storage layer with snapshots and schema evolution.

Current MVP behavior:

- Schema generation for the shared memory table.
- Identifier validation for configured Iceberg namespace/table names.
- Optional `pyiceberg` import readiness checks through `loro memory backend-check`.
- SQL rendering for shared-memory append/search.
- Polaris-governed discovery commands for tables, policies, and privileges.

## Local Testing

Use [local-polaris-iceberg.md](./local-polaris-iceberg.md) for the current local Docker
quickstart, Loro configuration, smoke checks, and optional Polaris CLI integration test.

## Future Typed Operations

- Draft safe SQL/Spark/PyIceberg snippets
- Execute Iceberg shared-memory operations through a governed catalog or enterprise query engine
