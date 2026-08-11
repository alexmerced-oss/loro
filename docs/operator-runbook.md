# Enterprise Operator Runbook

## Routine Checks

Run `loro doctor`, `loro config check --strict`, `loro audit verify`, `loro audit metrics`,
`loro memory backend-check`, and `loro memory migration-status` on the approved cadence. Alert on
failed required audit delivery, buffer growth or eviction, database health, identity/config
failure, provider budget/route errors, gateway saturation, and benchmark regression.

`loro operations benchmark --strict --output loro-benchmark.json` records only scenario names,
timings, version, Python, operating-system family, and architecture. It records no prompts,
responses, memory content, tool output, hostname, username, or credential values.

## Failure Procedures

| Failure | Immediate action | Recovery evidence |
| --- | --- | --- |
| Provider outage or 429/5xx | Stop or allow bounded retry on the same approved route; do not switch vendors implicitly. | Request IDs, route, status, retry count, budget state, and recovery time. |
| Audit outage | Stop consequential work when policy is `fail`; preserve the bounded buffer and investigate any eviction. | Buffer counts, collector health, flush/dedup result, chain verification, and external anchor. |
| Postgres outage | Stop shared-memory writes, preserve local drafts, restore service or a verified backup, then reconcile. | Incident window, backup manifest, RPO/RTO, migration status, reconciliation, and tenant checks. |
| Policy/config failure | Fail closed, compare managed digest and source inventory, then restore the last approved bundle. | Old/new digests, approver, negative test, rollback result, and affected sessions. |
| Credential compromise | Disable route/ingress, revoke the named vault profile, rotate upstream credentials, and inspect correlated audit events. | Revocation time, replacement reference, access review, affected request IDs, and closure. |
| Gateway overload | Reject new work at the configured queue bound, preserve replay state, and scale only within approved policy. | Queue metrics, duplicate/replay tests, rejected count, and recovery time. |

## Backup And Restore Drill

```bash
loro operations recovery-targets
loro operations backup --output /controlled/loro.dump --execute
loro operations verify-backup /controlled/loro.dump
loro operations restore /controlled/loro.dump
```

The final command is a plan. An executed restore additionally requires `--execute --yes` and a
change/incident authorization. Exercise cross-tenant denial and lifecycle reconciliation after
restore. The declared target is not achieved until the organization records a dated managed
environment result.

## Incident And Offboarding

Preserve audit, policy digest, version, identity, tenant, request/session IDs, provider route,
database timeline, and affected resources without copying prompt or memory content into tickets.
For offboarding, disable remote identities and sessions first, revoke credentials, apply the
tenant retention/legal-hold decision, verify shared-memory disposition, and retain required audit
evidence. Use `external-enterprise-requirements.md` as the controlled evidence checklist.
