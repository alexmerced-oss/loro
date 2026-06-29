# Memory

Loro separates local memory from shared enterprise memory.

## Local Memory

Local memory is private to the current environment. It is currently stored as JSONL and supports exact substring search.

```bash
loro remember --local "Use the product team's status update format."
loro memory list
loro memory search "status"
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
loro memory schema --backend iceberg
loro memory backend-check
```

`loro memory commit-draft <draft-id>` is a dry run by default. It renders the SQL and
bound parameters that would be used for the configured backend. `--execute` is currently
supported only for Postgres and still requires a configured DSN plus `psycopg`.

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
`docs/polaris.md`.

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
