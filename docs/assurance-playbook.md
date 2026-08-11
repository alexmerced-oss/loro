# Independent Assurance Playbook

Independent testers must receive the frozen release contract, threat model, data classification,
reference deployment, managed policy, supported/experimental matrices, architecture, and a
synthetic environment. They must not receive production credentials or customer data.

## Required Exercises

- Identity forgery, revocation, tenant crossover, managed-policy omission/tamper/rollback, and
  approval mutation/replay/expiry.
- Workspace traversal, symlink races, executable planting, environment leakage, sandbox escape,
  network denial, timeout/output exhaustion, archive/package collision, and tool injection.
- Prompt/model/tool/memory/artifact/session/audit data-protection bypass and shared-memory
  poisoning or unauthorized commit.
- Provider TLS/proxy/host pinning, rate/error behavior, budget limits, route fallback, credential
  rotation, and response confusion.
- MCP hostile server/client behavior, Skill compatibility/script controls, graph retries/gates/
  compensation/resume, and remote gateway signature/freshness/replay/queue boundaries.
- Audit outage, buffer saturation/eviction, replay/deduplication, chain tamper/truncation, database
  outage, backup/restore, migration rollback, reconciliation, offboarding, and disaster recovery.

## Acceptance Record

For every finding, retain title, affected contract surface, severity, reproducibility, impact,
evidence location, owner, remediation commit, retest result, risk acceptance and expiry. Promotion
requires no unresolved Critical/High finding and explicit disposition of all Medium findings.
Penetration testing, legal review, privacy approval, and production exercises are external gates;
repository maintainers must not mark them complete without controlled references.
