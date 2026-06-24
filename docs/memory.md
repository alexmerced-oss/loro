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

The MVP includes backend schema generation and draft staging. Live Postgres and Iceberg writes should be added behind the same logical schema.

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
