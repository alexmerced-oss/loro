# Memory

Loro separates local memory from shared enterprise memory.

## Local Memory

Local memory is private to the current environment. It is currently stored as JSONL and supports exact substring search.

```bash
loro remember --local "Use the product team's status update format."
loro memory list
loro memory search "status"
loro memory propose "Status briefs should include risks and next steps." --target local
loro memory proposals
loro memory accept-proposal <proposal-id>
```

## Shared Memory

Shared memory is intended for enterprise-wide reuse across users and agents. It must remain explicit-only:

- The user dictates or approves the final text.
- The agent may suggest candidates, but cannot autonomously commit them.
- Records need provenance, scope, classification, status, author, and audit metadata.

The MVP includes backend schema generation, draft staging, backend readiness diagnostics,
Postgres execution, Iceberg SQL rendering, and optional PyIceberg execution for Iceberg
searches and explicit draft commits through a configured governed catalog.

Set `tenant_isolation = "identity"` in the enterprise managed overlay. Loro then derives the
only permitted tenant from trusted identity, rejects caller-selected cross-tenant searches and
commits in the operation and adapter layers, hides other tenants' local drafts, installs forced
Postgres row-level security, and pushes the tenant/status predicate into Iceberg scans.

```bash
loro remember --shared "Use the enterprise launch readiness template" \
  --tenant-id acme --scope-type team --scope-key platform
loro memory drafts
loro memory commit-draft <draft-id>
loro memory commit-draft <draft-id> --execute
loro memory schema --backend postgres
loro memory apply-schema
loro memory apply-schema --execute
loro memory migration-status
loro memory migrate --target 2
loro memory migrate --target 2 --execute
loro memory reconcile
loro memory schema --backend iceberg
loro memory backend-check
loro memory snapshots
loro memory shared-search "launch readiness" --tenant-id acme
loro memory shared-search "launch readiness" --tenant-id acme --dry-run
loro memory lifecycle <memory-id> --action hold --reason "Litigation hold" --execute
loro memory lifecycle <memory-id> --action release-hold --reason "Hold released" --execute
loro memory lifecycle <memory-id> --action correct --content "Corrected text" \
  --reason "Owner-approved correction" --execute
loro memory lifecycle <memory-id> --action delete --reason "Approved erasure" --execute
loro memory propose "Use the enterprise launch readiness template" --target shared
loro memory accept-proposal <proposal-id> --tenant-id acme --scope-type team --scope-key platform
```

`loro memory commit-draft <draft-id>` is a dry run by default. It renders the SQL and
bound parameters that would be used for the configured backend. `--execute` requires the
configured backend client and never bypasses the explicit draft step. `loro memory
apply-schema` is also a dry run by default. `--execute` applies the Postgres shared-memory
schema to the configured DSN; Iceberg schema creation should be handled by the governed
catalog or query engine after reviewing `loro memory schema --backend iceberg`. Postgres schema
application now runs the checksummed migration plan through version 2; existing schema-blob
deployments migrate safely because every baseline DDL statement is idempotent.

`loro memory shared-search` returns active shared memories when the configured backend can
execute the search. If the backend is not ready, Loro returns the backend-neutral SQL and
bound parameters instead of failing silently. Runtime tasks also recall shared memories when
`[memory.shared].enabled = true`; recalled memories are injected with citations such as
`postgres:acme/team/platform/<memory-id>`.

## Memory Proposals

Memory proposals are local review records. They let Loro or the user capture candidate
learnings without committing memory immediately.

```bash
loro memory propose "Use concise incident summaries" --target local
loro memory propose "Use the enterprise incident template" --target shared
loro memory proposals
loro memory accept-proposal <proposal-id>
```

Accepting a local proposal writes local memory. Accepting a shared proposal stages a shared
memory draft only; it does not commit to Postgres or Iceberg. Shared memory still requires
the explicit `loro memory commit-draft <draft-id> --execute` step where that backend supports
execution.

## Postgres Backend

Configure the shared backend with a DSN environment variable:

```toml
[memory.shared]
enabled = true
backend = "postgres"
tenant_isolation = "identity"
postgres_dsn_env = "LORO_POSTGRES_DSN"
postgres_schema = "public"
```

`loro memory backend-check` verifies that the configured DSN environment variable is
present and that `psycopg` is importable. The adapter only commits explicit user-dictated
shared memory drafts.

`retention_days` assigns an expiry when a shared draft is staged; the expiry round-trips through
the staged draft record and is written with the committed memory. Search excludes deleted and
expired records, and `loro memory sweep` retires memories whose expiry has passed (reporting by
default, retiring with `--apply`, always skipping records under legal hold). Lifecycle changes require an explicit actor, reason, normalized permission,
approval, and audit event. Legal hold blocks delete and expire until a separate release action.
Postgres updates current state and appends an immutable memory event; Iceberg appends a new
memory version plus an event so governed snapshot history remains available.

Postgres draft commits are explicit:

```bash
loro memory apply-schema
loro memory apply-schema --execute
loro memory commit-draft <draft-id>
loro memory commit-draft <draft-id> --execute
```

Draft IDs and lifecycle operation IDs are persisted in `memory_events` under a tenant-scoped
unique index. An exact retry returns success without duplicating state or events. Reusing an ID
with changed content, action, target, or actor fails. `loro memory reconcile` compares current
state with append-only events and exits nonzero for orphan events, missing creation events,
tenant mismatches, or lifecycle state without its corresponding event. Migration, rollback,
backup, and restore procedures are in [Backup, Restore, and Recovery](recovery.md).

## Iceberg Backend

Iceberg shared memory uses the same logical schema and is intended for enterprise-wide
analytics, governance, and retention workflows.

```toml
[memory.shared]
enabled = true
backend = "iceberg"
tenant_isolation = "identity"
iceberg_catalog_name = "polaris_catalog"
iceberg_catalog_uri_env = "LORO_ICEBERG_CATALOG_URI"
iceberg_credential_env = "LORO_ICEBERG_CREDENTIAL"
iceberg_token_env = "LORO_ICEBERG_TOKEN"
iceberg_warehouse = "enterprise_catalog"
iceberg_namespace = "agent_memory"
iceberg_table = "shared_memories"
```

Loro can load the configured PyIceberg catalog by name. If `LORO_ICEBERG_CATALOG_URI` is
present, Loro passes REST catalog properties including `type = rest`, `uri`, optional
`warehouse`, optional `credential`, optional `token`, and the Iceberg access-delegation
header used by governed catalogs such as Polaris. You can also rely on PyIceberg's own
configuration files or `PYICEBERG_...` environment variables by leaving Loro's Iceberg env
vars unset.

Install optional data dependencies for execution:

```bash
python -m pip install "loro-agent[data]"
```

Iceberg `shared-search` pushes tenant filtering into the PyIceberg scan as a typed
expression (`EqualTo("tenant_id", ...)`, never an interpolated filter string), then selects the
newest version per `memory_id` in Python. Status and expiry are deliberately *not* pushed down:
they are per-version attributes, so filtering them in the scan could resurrect a memory whose
newest version is deleted or expired. Iceberg `commit-draft --execute` appends one memory row
and one event row to existing governed tables. Draft-derived memory and event IDs and lifecycle
event IDs are stable, so retrying the same object is idempotent.

Iceberg does not provide a transaction spanning both tables. Loro therefore writes the audit
event first and the state version second. If either append fails, the command fails and the same
draft or lifecycle request can be retried: Loro detects the completed append and writes only the
missing row. This prevents an unaudited state mutation and avoids duplicate state or event rows.
Production operators should still alert on failed writes and retry or reconcile them promptly.
All offset-aware timestamps are converted to UTC before storage in Iceberg's timezone-naive
`TIMESTAMP` fields.

For a lifecycle failure, Loro reports the operation UUID. Reuse it on the retry:

```bash
loro memory lifecycle <memory-id> --action hold --reason "Legal request" --execute \
  --operation-id <reported-operation-uuid>
```

DDL still renders through `loro memory schema --backend
iceberg`; table creation should happen through the governed catalog or enterprise query
engine. `loro memory snapshots` reports only snapshot IDs, parent IDs, sequence numbers,
timestamps, and counts for state/event tables; it never reads memory content.

## Logical Shared Memory Fields

- `memory_id`
- `tenant_id`
- `scope_type`
- `scope_key`
- `memory_type`
- `content`
- `summary`
- `tags`
- `classification`
- `source`
- `created_by`
- `created_at`
- `updated_at`
- `status`
- `confidence`
- `review`
- `embedding_ref`
- `supersedes`
- `expires_at`
- `legal_hold`
- `deleted_at`
