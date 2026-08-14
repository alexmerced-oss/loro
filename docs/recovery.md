# Backup, Restore, And Recovery

The 0.6 reference recovery boundary covers Postgres shared-memory state, append-only lifecycle
events, and migration records. The declared laboratory targets are:

- RPO: 300 seconds.
- RTO: 900 seconds.

An adopting organization must approve targets for its own load, topology, retention policy, and
failure domains.

## Migrations

```bash
loro memory migration-status
loro memory migrate --target 2
loro memory migrate --target 2 --execute
loro memory migrate --target 1 --execute
```

Migration checksums are verified before changes and migration execution is serialized with a
Postgres advisory lock. Version 2 can roll back to version 1 without deleting memory rows.
Rollback below version 1 drops shared-memory tables and is refused unless the caller explicitly
authorizes a destructive operation.

Back up before every migration. A rollback removes schema introduced by the rolled-back version;
it does not undo business operations committed after migration.

## Backup

Install PostgreSQL client tools matching or newer than the server, then run:

```bash
loro operations backup --output /secure/loro-memory.dump
loro operations backup --output /secure/loro-memory.dump --execute
loro operations verify-backup /secure/loro-memory.dump
```

Loro passes the DSN through `PGDATABASE`, not the process argument list. `pg_dump` produces a
custom-format, no-owner/no-privilege backup. The adjacent manifest binds the backup hash, byte
size, Postgres schema, memory schema version, creation time, format, and RPO/RTO targets.

Store the backup and manifest together in approved encrypted, access-controlled storage.

## Restore

Stop writers, provision a clean target, set the configured DSN variable to that target, and run:

```bash
loro operations verify-backup /secure/loro-memory.dump
loro operations restore /secure/loro-memory.dump
loro operations restore /secure/loro-memory.dump --execute --yes
loro memory migration-status
loro memory reconcile
loro memory shared-search "known recovery fixture" --tenant-id <tenant>
```

`--clean` additionally removes conflicting target objects and requires `--yes`. Return service
only after migration status, reconciliation, tenant-negative checks, known-record searches, and
audit delivery checks pass.

## Failure Checks

- **Provider:** run `loro providers check` and the approved mock/live smoke; verify no silent
  cross-provider fallback.
- **Policy:** run `loro config check` and `loro policy explain` for known allow/ask/deny fixtures.
- **Audit:** run `loro audit doctor`, the outage drill, `loro audit flush`, and collector verify.
- **Database:** run backend check, migration status, reconciliation, backup verification, and a
  restore drill.
- **Catalog:** run `loro memory backend-check`, `loro memory snapshots`, and approved Polaris CLI
  positive/negative checks.

The scheduled/dispatchable integration workflow automates Postgres backup/restore and Polaris
quickstart checks. Credentialed protected environments remain separate because their identities,
roles, object stores, and retention controls are deployment-owned.
