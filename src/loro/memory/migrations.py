from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LATEST_POSTGRES_MEMORY_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class PostgresMemoryMigration:
    version: int
    name: str
    up: str
    down: str
    rollback: Literal["safe", "destructive"] = "safe"

    @property
    def checksum(self) -> str:
        return "sha256:" + hashlib.sha256(self.up.encode("utf-8")).hexdigest()


def postgres_memory_migrations(
    schema: str,
    *,
    tenant_isolation: bool = False,
) -> tuple[PostgresMemoryMigration, ...]:
    if not IDENTIFIER_PATTERN.fullmatch(schema):
        raise ValueError("postgres_schema must be a simple PostgreSQL identifier.")
    prefix = "" if schema == "public" else f"{schema}."
    create_schema = f"CREATE SCHEMA IF NOT EXISTS {schema};\n" if schema != "public" else ""
    memory_table = f"{prefix}shared_memories"
    events_table = f"{prefix}memory_events"
    versions_table = f"{prefix}loro_memory_schema_migrations"
    index_prefix = "" if schema == "public" else f"{schema}_"
    scope_index = f"idx_{index_prefix}shared_memories_scope"
    type_index = f"idx_{index_prefix}shared_memories_type"
    operation_index = f"idx_{index_prefix}memory_events_operation"
    rls = _tenant_rls(memory_table, events_table) if tenant_isolation else ""
    baseline = f"""
{create_schema}CREATE TABLE IF NOT EXISTS {versions_table} (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {memory_table} (
  memory_id UUID PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  memory_type TEXT NOT NULL,
  content TEXT NOT NULL,
  summary TEXT NOT NULL,
  tags TEXT[] NOT NULL DEFAULT '{{}}',
  classification TEXT NOT NULL,
  source JSONB NOT NULL DEFAULT '{{}}'::jsonb,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'active',
  confidence DOUBLE PRECISION,
  review JSONB,
  embedding_ref TEXT,
  supersedes UUID[] NOT NULL DEFAULT '{{}}',
  expires_at TIMESTAMPTZ,
  legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
  deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS {events_table} (
  event_id UUID PRIMARY KEY,
  memory_id UUID,
  tenant_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  event_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL DEFAULT '{{}}'::jsonb
);

ALTER TABLE {memory_table}
  ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE {memory_table}
  ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS {scope_index}
  ON {memory_table} (tenant_id, scope_type, scope_key, status);
CREATE INDEX IF NOT EXISTS {type_index}
  ON {memory_table} (tenant_id, memory_type, status);
{rls}
""".strip()
    operation_ids = f"""
ALTER TABLE {events_table} ADD COLUMN IF NOT EXISTS operation_id UUID;
CREATE UNIQUE INDEX IF NOT EXISTS {operation_index}
  ON {events_table} (tenant_id, operation_id)
  WHERE operation_id IS NOT NULL;
""".strip()
    return (
        PostgresMemoryMigration(
            version=1,
            name="shared_memory_baseline",
            up=baseline,
            down=f"DROP TABLE IF EXISTS {events_table};\nDROP TABLE IF EXISTS {memory_table};",
            rollback="destructive",
        ),
        PostgresMemoryMigration(
            version=2,
            name="idempotent_operation_ids",
            up=operation_ids,
            down=(
                f"DROP INDEX IF EXISTS {schema}.{operation_index};\n"
                f"ALTER TABLE {events_table} DROP COLUMN IF EXISTS operation_id;"
            ),
        ),
    )


def render_postgres_memory_schema(
    schema: str,
    *,
    tenant_isolation: bool = False,
) -> str:
    statements = []
    for migration in postgres_memory_migrations(
        schema,
        tenant_isolation=tenant_isolation,
    ):
        statements.append(migration.up)
        statements.append(
            _record_migration_sql(schema, migration.version, migration.name, migration.checksum)
        )
    return "\n\n".join(statements)


def migration_table(schema: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(schema):
        raise ValueError("postgres_schema must be a simple PostgreSQL identifier.")
    return f"{schema}.loro_memory_schema_migrations"


def record_migration_sql(schema: str, migration: PostgresMemoryMigration) -> str:
    return _record_migration_sql(schema, migration.version, migration.name, migration.checksum)


def _record_migration_sql(schema: str, version: int, name: str, checksum: str) -> str:
    # Schema is identifier-validated; migration metadata is defined in this module.
    return f"""
INSERT INTO {migration_table(schema)} (version, name, checksum)
VALUES ({version}, '{name}', '{checksum}')
ON CONFLICT (version) DO UPDATE
SET name = EXCLUDED.name, checksum = EXCLUDED.checksum;
""".strip()  # nosec B608


def _tenant_rls(memory_table: str, events_table: str) -> str:
    return f"""
ALTER TABLE {memory_table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {memory_table} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS loro_tenant_isolation ON {memory_table};
CREATE POLICY loro_tenant_isolation ON {memory_table}
  USING (tenant_id = current_setting('loro.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('loro.tenant_id', true));

ALTER TABLE {events_table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {events_table} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS loro_tenant_isolation ON {events_table};
CREATE POLICY loro_tenant_isolation ON {events_table}
  USING (tenant_id = current_setting('loro.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('loro.tenant_id', true));
""".strip()
