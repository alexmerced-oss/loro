# Polaris And Iceberg

Loro's governed data layer is centered on Apache Polaris and Apache Iceberg.

## Polaris

Polaris provides governed catalog concepts such as catalogs, namespaces, tables, views, principals, roles, privileges, and policies. Loro should use Polaris to discover what the user can access and explain access denials when allowed.

Current MVP behavior:

```bash
loro data catalogs
loro data polaris catalogs list
```

The `data polaris` command validates the requested operation against a read-only allowlist before executing the Polaris CLI.

## Iceberg

Iceberg is the target high-scale backend for shared enterprise memory and governed data table access. The initial implementation should treat Iceberg as an append-friendly, auditable storage layer with snapshots and schema evolution.

Current MVP behavior:

- Schema generation for the shared memory table.
- Placeholder backend adapter for future PyIceberg or REST catalog integration.

## Future Typed Operations

- List/get catalogs
- List/get namespaces
- List/get tables/views
- Inspect schemas
- Inspect principal roles and catalog roles
- Inspect privileges and policies
- Explain table accessibility
- Draft safe SQL/Spark/PyIceberg snippets
