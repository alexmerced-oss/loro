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

## Iceberg

Iceberg is the target high-scale backend for shared enterprise memory and governed data table access. The initial implementation should treat Iceberg as an append-friendly, auditable storage layer with snapshots and schema evolution.

Current MVP behavior:

- Schema generation for the shared memory table.
- Placeholder backend adapter for future PyIceberg or REST catalog integration.

## Future Typed Operations

- Inspect schemas
- Explain table accessibility
- Draft safe SQL/Spark/PyIceberg snippets
