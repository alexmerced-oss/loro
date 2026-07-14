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

The MVP includes backend schema generation, draft staging, Postgres readiness diagnostics,
and Postgres/Iceberg SQL adapters that can render insert/search statements. Live Iceberg
commits should be added behind the same logical schema through a governed execution engine.

```bash
loro remember --shared "Use the enterprise launch readiness template" \
  --tenant-id acme --scope-type team --scope-key platform
loro memory drafts
loro memory commit-draft <draft-id>
loro memory commit-draft <draft-id> --execute
loro memory schema --backend postgres
loro memory apply-schema
loro memory apply-schema --execute
loro memory schema --backend iceberg
loro memory backend-check
loro memory shared-search "launch readiness" --tenant-id acme
loro memory shared-search "launch readiness" --tenant-id acme --dry-run
loro memory propose "Use the enterprise launch readiness template" --target shared
loro memory accept-proposal <proposal-id> --tenant-id acme --scope-type team --scope-key platform
```

`loro memory commit-draft <draft-id>` is a dry run by default. It renders the SQL and
bound parameters that would be used for the configured backend. `--execute` is currently
supported only for Postgres and still requires a configured DSN plus `psycopg`.
`loro memory apply-schema` is also a dry run by default. `--execute` applies the Postgres
shared-memory schema to the configured DSN.

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
postgres_dsn_env = "LORO_POSTGRES_DSN"
postgres_schema = "public"
```

`loro memory backend-check` verifies that the configured DSN environment variable is
present and that `psycopg` is importable. The adapter only commits explicit user-dictated
shared memory drafts.

Postgres draft commits are explicit:

```bash
loro memory apply-schema
loro memory apply-schema --execute
loro memory commit-draft <draft-id>
loro memory commit-draft <draft-id> --execute
```

## Iceberg Backend

Iceberg shared memory uses the same logical schema and is intended for enterprise-wide
analytics, governance, and retention workflows.

```toml
[memory.shared]
enabled = true
backend = "iceberg"
iceberg_namespace = "agent_memory"
iceberg_table = "shared_memories"
```

The current MVP emits configured Iceberg DDL through `loro memory schema --backend iceberg`
and provides SQL rendering for future append/search execution.
Polaris catalog access is handled by the governed data commands documented in
`docs/polaris-iceberg.md`.

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
