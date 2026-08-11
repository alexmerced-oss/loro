# Loro 0.6 Work Record

This document maps the `0.6` Enterprise Data and Operations milestone to implementation and
verification. It is the execution record for the broader [Roadmap to 1.0](roadmap-1.0.md).

## Batch 1: Postgres Shared Memory

Status: **repository implementation complete; release-commit CI supplies hosted evidence**.

- Versioned, checksummed migrations with an advisory lock and safe/destructive rollback labels.
- Schema version `2` adds tenant-scoped operation IDs for idempotent draft/lifecycle retries.
- State/event reconciliation reports orphan events, missing creation events, tenant mismatches,
  and lifecycle-state mismatches.
- Ephemeral Postgres coverage includes create/search/correct/expire/delete/legal hold, sequential
  retry, concurrent writers, forced RLS denial, rollback, forward migration, and drift detection.

## Batch 2: Polaris And Iceberg

Status: **repository implementation complete; protected authorization evidence external**.

- Machine-readable versions pin Polaris 1.6.0, Iceberg REST v1, Iceberg 1.10.1, PyIceberg
  0.11.1, DuckDB 1.5.5, RustFS alpha.81, and Postgres 16.
- CI replaces the official quickstart's floating Polaris image with `apache/polaris:1.6.0`.
- DuckDB verifies seeded Parquet data and Iceberg exposes content-free snapshot diagnostics.
- Existing tests cover event-first retry recovery, lifecycle idempotency, legal hold, typed tenant
  predicates, timestamp normalization, and operation-ID conflict.
- Credential expiry and cross-tenant Polaris denials require a protected test realm and remain
  external deployment evidence.

## Batch 3: Audit And Observability

Status: **repository implementation complete; production immutability external**.

- Reference bearer-authenticated HTTP collector with transactional SQLite persistence,
  `event_id` deduplication, atomic batch acceptance, and a verifiable SHA-256 chain.
- Health and Prometheus endpoints plus a hardened container/Compose reference deployment.
- Content-free operational metrics derived from bounded audit metadata; prompt, tool, memory,
  artifact, and gateway message content is never persisted to metrics.
- Executable outage drill proves buffered recovery, batching, deduplication, and chain continuity.
- Immutable external storage and independent anchoring remain deployment responsibilities.

## Batch 4: Recovery

Status: **repository implementation complete; release-commit CI supplies hosted restore evidence**.

- Credential-minimized `pg_dump` and `pg_restore` wrappers with checksummed manifests.
- Explicit authorization for execution and destructive clean restore.
- Declared reference targets: RPO 300 seconds and RTO 900 seconds.
- Two-container CI drill backs up committed state/events, restores into a clean database,
  reconciles, and checks the RTO.
- Operator procedures cover migration, backup verification, restore, reconciliation, rollback,
  provider failure, policy failure, audit outage, and catalog failure.

## Release Gate

The release can ship when unit/branch/security suites, Postgres lifecycle/recovery containers,
Polaris quickstart, DuckDB seed validation, audit outage drill, package smoke tests, and release
evidence pass on the release commit. Protected Polaris authorization, production object-store
behavior, immutable audit retention, and organization-approved RPO/RTO remain external and are
not represented as repository-complete.
