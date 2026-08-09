# External Enterprise Requirements

This register lists controls that cannot be completed truthfully by adding more Loro code. Each
adopting organization must assign the owner, run the exercise in its controlled environment,
retain the evidence, and link the result from `enterprise-evidence.md` for a release candidate.

| Area | External owner and action | Required evidence and acceptance |
| --- | --- | --- |
| Governance | Product and security name accountable owners; security/privacy/legal approve the threat model, data mapping, pilot scope, and unsupported uses. | Dated approvals, owner/on-call roster, accepted risks, and review expiry. |
| Identity | Identity team launches Loro with a verified corporate assertion and binds subject, tenant, groups, and session to authorization. | Positive/negative SSO tests, assertion validation design, revocation/offboarding test, and no prompt-derived identity. |
| Managed policy | Configuration-management team distributes the managed TOML and approved digest over an authenticated channel and controls rotation. | Signed change record, digest inventory, missing/tampered startup failures, rollback, and two-person approval. Loro verifies a pinned digest but does not operate the signing authority. |
| Endpoint sandbox | Runtime/security deploy Bubblewrap on the supported Linux image and run hostile escape, filesystem, network, environment, timeout, and output tests. | Pinned OS/package manifest and an independent escape-test report. Process mode alone is not an OS security boundary. |
| Data protection | Privacy/security connect the corporate DLP/classification service and approve every provider/data-class flow. | Scanner contract tests, false-positive procedure, provider retention/training terms, and approved flow matrix. |
| Postgres | Data team provisions TLS, managed keys, least-privilege roles, forced RLS, backup/restore, retention jobs, and legal-hold operations. | Live cross-tenant denial tests, encryption proof, restore drill with RPO/RTO, and lifecycle reconciliation report. Testcontainers proves adapter behavior, not production controls. |
| Polaris/Iceberg | Data-governance team provisions principals, roles, catalogs, warehouses, object-store encryption, and policy denials through Polaris. | Live positive/negative CLI and REST tests, delegated-credential expiry, Iceberg snapshot/lifecycle checks, and catalog audit records. The public quickstart is service smoke only. |
| Audit/SIEM | Operations deploy an authenticated immutable collector, retain final-chain anchors, synchronize clocks, and alert on backlog or integrity failure. | Load/outage/replay/dedup tests, immutable-retention policy, anchor verification, access review, and alert/runbook exercise. A local chain cannot prove tail non-truncation without an external anchor. |
| Model gateway | Platform team supplies enterprise CA/proxy, rate limits, residency routing, provider allowlists, and any fallback policy. | TLS interception/CA tests, 429/5xx/outage tests, residency proof, cost reconciliation, and approved fallback matrix. Loro retries; it does not automatically move data between vendors. |
| Distributed budgets | Platform/product enforce user and tenant concurrency, daily/monthly spend, and emergency stop in a shared gateway or coordinator. | Concurrent load test, quota denial tests, billing reconciliation, and kill-switch exercise. Loro task budgets are process-local. |
| CI/release | Repository admins protect branches/tags, require workflows/reviews, enable artifact attestations and trusted publishing, adjudicate secret candidates, and control PyPI roles. | Ruleset export, green run links, line-by-line `.secrets.baseline` decisions, attestation verification, clean install, rollback, and publisher access review. |
| Live providers/MCP | Runtime/security use protected credentials and approved endpoints for controlled smoke and hostile-server tests. | Sanitized fixtures, spend record, protocol/provider matrix, authorization failures, and data-retention confirmation. No live credentials belong in repository artifacts. |
| Operations | Operations exercise install/upgrade/rollback, provider outage, audit outage, database failure, incident response, offboarding, and disaster recovery. | Dated runbook/tabletop reports, defects and remediation links, support SLA, on-call handoff, and recovery-objective results. |
| Assurance/GA | Independent testers perform penetration testing; executives and product/security/privacy/legal/operations decide pilot and GA readiness. | Pen-test report and disposition, pilot success metrics, privacy notice/telemetry approval, and signed launch decision. |

## Evidence Record

For each row, record the environment, Loro version and commit, policy digest, infrastructure
versions, owner, reviewer, date, result, artifact location, accepted risks, remediation owner,
and next review date. Secrets and customer data must stay in the controlled evidence system,
not in this repository or GitHub Actions artifacts.
