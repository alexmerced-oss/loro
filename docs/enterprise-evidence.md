# Enterprise Readiness Evidence Register

## How To Use This Register

This is the closure checklist for the
[Enterprise Readiness Roadmap](enterprise-readiness-roadmap.md). An item is complete only when
its implementation, automated test, operational proof, documentation, accountable owner, and
review are linked. Repository paths below are current evidence or planned placeholders; `TBD`
means the milestone remains open.

Evidence states:

- **Existing:** evidence is present, though it may prove only alpha/MVP behavior.
- **Partial:** useful evidence exists but does not close the enterprise requirement.
- **Planned:** implementation or proof is not yet present.
- **External:** adopting-enterprise evidence cannot be committed here; record its controlled
  reference, owner, and review date.

## Phase 0: Baseline And Ownership

| ID | Exit item | Owner | Status | Evidence and remaining proof |
| --- | --- | --- | --- | --- |
| E0-01 | Threat model and data flow reviewed | Security (TBD) | Partial | [Threat model](threat-model.md) drafted; security/engineering review record TBD. |
| E0-02 | Data classifications and permitted flows approved | Security/privacy (TBD) | Partial | [Data classification](data-classification.md) drafted; corporate mapping and approval TBD. |
| E0-03 | Reference deployment and supported matrix reproducible | Operations/release (TBD) | Partial | [Reference deployment](reference-deployment.md) drafted; pinned manifests and reproduction result TBD. |
| E0-04 | Accountable owners assigned | Product (TBD) | Planned | Role table exists in reference deployment; named owners and acceptance dates TBD. |
| E0-05 | Every later phase has criteria and evidence placeholders | Release (TBD) | Existing | This register and the enterprise roadmap. |
| E0-06 | Baseline quality, reliability, performance, and cost recorded | Runtime/release (TBD) | Partial | Unit/coverage CI exists; versioned benchmark, provider cost, live reliability, and integration report TBD. |

## Phase 1: Identity, Policy, And Approval

| ID | Exit item | Owner | Status | Evidence and remaining proof |
| --- | --- | --- | --- | --- |
| E1-01 | Trusted identity context is required and propagated | Identity/policy (TBD) | Partial | Typed context, config/environment resolution, managed required fields, CLI diagnostics, propagation, and tests exist in `src/loro/identity.py`, `tests/test_identity.py`, and [Identity Context](identity.md); corporate assertion verification/integration remains TBD. |
| E1-02 | Managed policy is validated, versioned, and fail-closed | Identity/policy (TBD) | Partial | Managed overlay precedence, required-source mode, exact-byte aggregate digest verification, and failure tests exist in `src/loro/config.py` and `tests/test_config.py`; authenticated distribution, signing authority, rotation, and corporate approval remain external. |
| E1-03 | Consequential actions use attributable exact approvals | Identity/policy (TBD) | Partial | [Approvals](approvals.md), `src/loro/approvals.py`, and CLI/runtime tests cover current tool paths, configured policy-version/source binding, and an atomic single-host JSON store with compare-and-set consumption. Corporate identity verification, distributed storage if required, and signed policy artifacts remain external. |
| E1-04 | Model self-approval, replay, mutation, and expiry fail | Security (TBD) | Existing | `tests/test_approvals.py` and `tests/test_tool_runtime.py` cover model-origin rejection, changed arguments, one-time replay, cross-session reuse, expiry, and exact session scope. |
| E1-05 | Resource scopes are normalized and explainable | Runtime/security (TBD) | Existing | `src/loro/resources.py`, `src/loro/permissions.py`, `tests/test_resources.py`, runtime/CLI tests, `loro policy explain`, and [Normalized Resource Policy](policy.md) cover the current tool surface; sandbox and signed-policy review remain later gates. |
| E1-06 | Every consequential event contains actor/policy/approval/target | Release/security (TBD) | Partial | Schema `1.0` promotes identity/session/trace, action/target, policy, approval, result, and redaction fields. The [Audit Event Inventory](audit-event-inventory.md), typed family registry, AST source check, and tests reject unclassified literal events. External event-family review and production collector evidence remain TBD. |
| E1-07 | Limited-pilot permission model approved | Security (TBD) | External | Review record and accepted-risk references TBD. |

## Phase 2: Isolation And Data Protection

| ID | Exit item | Owner | Status | Evidence and remaining proof |
| --- | --- | --- | --- | --- |
| E2-01 | Named sandbox profiles enforce workspace, environment, network, time, and output limits | Runtime/security (TBD) | Partial | `src/loro/sandbox.py`, subprocess integrations, adversarial tests, CLI diagnostics, and [sandbox profiles](sandbox.md) cover shell, Git, Polaris, MCP stdio, and Skill execution; production Bubblewrap escape evidence remains. |
| E2-02 | Provider credentials are isolated from tools | Runtime/security (TBD) | Partial | Every current tool subprocess family uses an allowlist-built environment; MCP adds an SDK-default scrubber and explicit-server-variable tests. Deployment inspection and production evidence remain. |
| E2-03 | Classification/DLP controls cover all content surfaces | Security/privacy (TBD) | Partial | `src/loro/data_protection.py`, `tests/test_data_protection.py`, runtime/tool/storage integrations, CLI diagnostics, and [Managed Data Protection](data-protection.md) provide the managed decision contract, custom/pluggable scanners, ceilings, blocking, redaction, and nested audit metadata. Corporate scanner integration, provider-specific flow approval, and production policy evidence remain TBD. |
| E2-04 | Tenant boundaries enforced below CLI/prompt layer | Memory/data (TBD) | Partial | Managed `identity` isolation binds operations, adapters, and local drafts to trusted identity; Postgres forced-RLS tests exercise tenant denial against the ephemeral reference schema; Iceberg pushes tenant/status filters into scans. Production database roles, Polaris authorization, and live cross-tenant evidence remain external. |
| E2-05 | Memory retention, correction, deletion, hold, and provenance work | Memory/data (TBD) | Partial | Retention-derived expiry, correction, deletion, expiration, legal hold/release, retrieval exclusion, versioned Postgres migrations, idempotent lifecycle operations, reconciliation, append-only Iceberg versions, approvals, CLI, and tests exist; production retention jobs, owner review, and legal evidence remain external. |
| E2-06 | Encryption and key ownership documented and verified | Operations/security (TBD) | External | TLS/storage/KMS configuration and verification report TBD. |
| E2-07 | Adversarial injection, poisoning, symlink, archive, and output tests pass | Security/runtime (TBD) | Partial | Workspace/symlink, package collision, secret-environment, executable, timeout, output-limit, malformed directive, and poisoned shared-memory tests exist. Recalled memory is explicitly untrusted and carries no authority. Production-like escape and independent adversarial evidence remain external. |
| E2-08 | MCP and Agent Skills cannot bypass sandbox, credentials, policy, or approval | Security/runtime (TBD) | Partial | MCP normalized policy, exact approval, least-privilege exports, sandboxed stdio, environment scrubbing, managed host/auth controls, and inert extensions exist. Agent Skills add validation/digests, script denial by default, model self-approval rejection, and named script profiles; production proof and enterprise review remain. See [MCP](mcp.md), [Agent Skills](skills.md), and [sandbox profiles](sandbox.md). |

## Phase 3: Audit, Operations, And Reliability

| ID | Exit item | Owner | Status | Evidence and remaining proof |
| --- | --- | --- | --- | --- |
| E3-01 | Versioned complete audit schema | Release/security (TBD) | Partial | Schema `1.0`, compatibility fields, promoted metadata, tests, and [Audit Events And Delivery](audit.md) exist; event-family completeness policy and external review remain TBD. |
| E3-02 | Durable external sink survives outages without silent loss | Operations (TBD) | Partial | HTTP sink, bearer authentication, retry/backoff, bounded JSONL buffering, an authenticated transactional reference collector, event-ID deduplication, and an executable outage drill exist; production load, TLS, alerting, and managed destination evidence remain external. |
| E3-03 | Audit evidence is immutable or tamper-evident | Operations/security (TBD) | Partial | Local and collector records use verifiable SHA-256 chains; collector tests prove replay handling and tamper detection. Immutable retention and independently retained final-chain anchors remain external because a local chain cannot prove tail non-truncation. |
| E3-04 | Metrics/traces cover latency, denial, approval, cost, memory, and audit | Operations/runtime (TBD) | Partial | Content-free JSON/Prometheus metrics cover task latency, provider usage, policy decisions, approvals, memory operations, gateway backlog, and audit delivery. Production dashboards, SLOs, alerts, and a managed exporter remain external. |
| E3-05 | Gateway resilience and policy-safe fallback are proven | Runtime (TBD) | Partial | Provider adapters add bounded timeout/network/429/5xx retries plus environment-backed CA/proxy support. Enterprise gateway, residency, rate, load, and approved cross-provider fallback tests remain external. |
| E3-06 | Per-user/tenant budgets are enforced | Runtime/product (TBD) | Partial | Per-task model input/output bytes, tokens, configured cost, tool calls, and steps fail closed and persist content-free usage. Distributed user/tenant concurrency and period quotas remain external. |
| E3-07 | Health checks and runbooks recover documented failures | Operations (TBD) | Partial | `loro doctor`, audit collector health/verify/metrics, memory migration/reconciliation, checksummed Postgres backup/restore, declared RPO/RTO targets, and executable outage/recovery procedures exist; organization-run drills and alert evidence remain external. |

## Phase 4: Verification And Supply Chain

| ID | Exit item | Owner | Status | Evidence and remaining proof |
| --- | --- | --- | --- | --- |
| E4-01 | Postgres and Polaris/Iceberg integration runs automatically in protected CI | Release/data (TBD) | Partial | Weekly/manual CI runs ephemeral Postgres lifecycle/recovery and pinned Polaris `1.6.0` plus RustFS/DuckDB quickstart smoke from the machine-readable data matrix. Credentialed Polaris authorization and full governed Iceberg transactions require a protected external environment. |
| E4-02 | End-to-end reference deployment tests pass | Release/security (TBD) | Partial | Hermetic identity-to-approval-to-tool-to-session-to-hash-chain coverage plus ephemeral Postgres lifecycle/recovery and audit outage drills exist; production gateway, corporate DLP, immutable audit, and protected Polaris reports remain external. |
| E4-03 | Provider contracts use sanitized fixtures and controlled live smoke | Runtime/release (TBD) | Partial | Unit and manually executed smoke coverage exists; fixture governance and protected live schedule TBD. |
| E4-04 | Security-critical branch coverage target passes | Release/security (TBD) | Existing | Overall 70% branch threshold and module-specific floors are enforced by `scripts/check_security_coverage.py`; the August 9 local run passed all floors. |
| E4-05 | Dependency, secret, license, and static scans follow triage policy | Release/security (TBD) | Partial | Pinned weekly/change CI runs pip-audit, Bandit baseline, detect-secrets drift, AGPL policy, CycloneDX, and evidence upload. Existing secret candidates require external line-by-line adjudication and repository SLA/risk ownership. |
| E4-06 | Release has SBOM, provenance, signature, and verification | Release (TBD) | Partial | Security CI emits CycloneDX; tag/manual release CI now bundles a clean-environment CycloneDX SBOM, machine-readable support matrix, artifact-bound release manifest, checksums, and GitHub/Sigstore provenance. Protected-tag policy, trusted publishing administration, and release-owner consumer verification remain external. |
| E4-07 | Upgrade, rollback, migration, support, and disclosure policies work | Release/operations (TBD) | Partial | Root configuration schema `1.0`, automatic migration of the legacy unversioned shape, future-version fail-closed behavior, writer stamping, tests, and the release checklist exist. Full deployment upgrade/rollback drills, storage migrations, support policy, and disclosure exercise remain TBD. |
| E4-08 | Advertised MCP revisions and Agent Skills pass conformance/interoperability | Release/runtime (TBD) | Partial | Dual-era SDK fixtures, server/client conformance workflow, Agent Skills validation tests, and [support matrix](mcp-support-matrix.md) exist. Local runner `0.1.16` scenarios and `skills-ref==0.1.1` validation passed on August 9, 2026; a green protected workflow artifact remains required for each release commit. |
| E4-09 | AGS 1.0 graphs and run records pass pinned conformance | Release/runtime (TBD) | Partial | Vendored schemas/reference validator, upstream examples and negative fixtures, Loro execution tests, strict examples, and the pinned `AGS Conformance` workflow exist. A green protected workflow artifact, production graph-policy approval, sandbox proof, and controlled live-provider run remain release evidence. See [Agentic Graphs](agentic-graphs.md). |
| E4-10 | Remote channel work is authenticated, tenant-bound, replay-safe, and auditable | Runtime/security (TBD) | Partial | Slack, Discord, Telegram, Teams, Signal-bridge, and generic adapters have signature fixtures, identity/channel/workspace policy, durable replay suppression, bounded dispatch, and OS-vaulted credentials. TLS ingress, platform app governance, rotation, outage, and production hostile-event evidence remain external. See [Channel Gateways](channel-gateways.md). |

## Phase 5: Pilot And General Availability

| ID | Exit item | Owner | Status | Evidence and remaining proof |
| --- | --- | --- | --- | --- |
| E5-01 | Restricted pilot scope and success measures approved | Product/security (TBD) | Planned | Pilot charter, users, repositories, data classes, measures, and duration TBD. |
| E5-02 | Admin/operator/user/privacy/incident docs published | Product/operations (TBD) | Partial | Current user docs exist; enterprise role guides and incident materials TBD. |
| E5-03 | Privacy-preserving product telemetry supports pilot evaluation | Product/privacy (TBD) | Planned | Data inventory, consent/notice, event schema, and review TBD. |
| E5-04 | Incident tabletop and offboarding review completed | Operations/security (TBD) | External | Exercise reports and remediation links TBD. |
| E5-05 | Independent penetration test findings resolved or accepted | Security (TBD) | External | Report, remediation evidence, and risk acceptance TBD. |
| E5-06 | GA sign-off and operational ownership complete | Executive/product (TBD) | External | Security/privacy/legal/operations/product approvals and on-call record TBD. |
| E5-07 | Backup/restore and disaster recovery exercised | Operations/data (TBD) | External | Dated drill result, recovery objectives, gaps, and remediation TBD. |
| E5-08 | Supported and unsupported use cases are published | Product (TBD) | Partial | Reference-deployment limitations exist; GA compatibility and support policy TBD. |

## Release Evidence Template

Create one record per enterprise release candidate in a controlled release location and link it
from this register:

```text
Release/version:
Commit and artifact digest:
Deployment manifest/policy version:
Named owners and approvers:
Threat-model/data-flow changes:
Unit, coverage, integration, E2E, adversarial results:
Supported platform/provider/backend matrix:
Open security findings and accepted risks:
SBOM, provenance, signature, and verification:
Backup/restore, rollback, and failure-injection results:
Pilot/GA decision and date:
```

## Current Batch 1 Exit Assessment

The document set now makes the supported pilot shape, threats, data rules, ownership gaps, and
future evidence explicit. Batch 1 is complete as repository documentation, but Phase 0 itself
remains **open** until an adopting organization assigns owners, approves the threat model and
classification mapping, pins the deployment matrix, and attaches baseline/reproduction results.
