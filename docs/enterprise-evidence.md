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
| E1-02 | Managed policy is validated, versioned, and fail-closed | Identity/policy (TBD) | Partial | Managed overlay precedence exists in `src/loro/config.py` and `tests/test_config.py`; integrity/version/failure tests TBD. |
| E1-03 | Consequential actions use attributable exact approvals | Identity/policy (TBD) | Partial | [Approvals](approvals.md), `src/loro/approvals.py`, and CLI/runtime tests cover current tool paths, including configured policy-version/source binding; durable records, corporate identity verification, and signed policy artifacts remain TBD. |
| E1-04 | Model self-approval, replay, mutation, and expiry fail | Security (TBD) | Existing | `tests/test_approvals.py` and `tests/test_tool_runtime.py` cover model-origin rejection, changed arguments, one-time replay, cross-session reuse, expiry, and exact session scope. |
| E1-05 | Resource scopes are normalized and explainable | Runtime/security (TBD) | Existing | `src/loro/resources.py`, `src/loro/permissions.py`, `tests/test_resources.py`, runtime/CLI tests, `loro policy explain`, and [Normalized Resource Policy](policy.md) cover the current tool surface; sandbox and signed-policy review remain later gates. |
| E1-06 | Every consequential event contains actor/policy/approval/target | Release/security (TBD) | Partial | Current CLI/runtime audit events carry actor, tenant, and identity context; policy version, normalized target, attributable approval, and versioned schema remain TBD. |
| E1-07 | Limited-pilot permission model approved | Security (TBD) | External | Review record and accepted-risk references TBD. |

## Phase 2: Isolation And Data Protection

| ID | Exit item | Owner | Status | Evidence and remaining proof |
| --- | --- | --- | --- | --- |
| E2-01 | Named sandbox profiles enforce workspace, environment, network, time, and output limits | Runtime/security (TBD) | Planned | Sandbox implementation and escape tests TBD. |
| E2-02 | Provider credentials are isolated from tools | Runtime/security (TBD) | Planned | Subprocess environment allowlist and leak tests TBD. |
| E2-03 | Classification/DLP controls cover all content surfaces | Security/privacy (TBD) | Partial | `src/loro/safety.py` and safety tests detect a small pattern set; managed DLP interface and flow tests TBD. |
| E2-04 | Tenant boundaries enforced below CLI/prompt layer | Memory/data (TBD) | Partial | Tenant-aware schemas/filtering and adapter tests exist; trusted identity binding, database controls, and cross-tenant suite TBD. |
| E2-05 | Memory retention, correction, deletion, hold, and provenance work | Memory/data (TBD) | Partial | Schema contains provenance/status/expiry fields; lifecycle execution and tests TBD. |
| E2-06 | Encryption and key ownership documented and verified | Operations/security (TBD) | External | TLS/storage/KMS configuration and verification report TBD. |
| E2-07 | Adversarial injection, poisoning, symlink, archive, and output tests pass | Security/runtime (TBD) | Planned | Adversarial suite and production-like result TBD. |

## Phase 3: Audit, Operations, And Reliability

| ID | Exit item | Owner | Status | Evidence and remaining proof |
| --- | --- | --- | --- | --- |
| E3-01 | Versioned complete audit schema | Release/security (TBD) | Partial | Local events and `tests/test_audit.py` exist; schema/version/completeness tests TBD. |
| E3-02 | Durable external sink survives outages without silent loss | Operations (TBD) | Planned | Sink interface, authenticated delivery, bounded buffer, retry, and failure-injection report TBD. |
| E3-03 | Audit evidence is immutable or tamper-evident | Operations/security (TBD) | Planned | Destination control and verification procedure TBD. |
| E3-04 | Metrics/traces cover latency, denial, approval, cost, memory, and audit | Operations/runtime (TBD) | Planned | Telemetry schema, dashboards, and alert tests TBD. |
| E3-05 | Gateway resilience and policy-safe fallback are proven | Runtime (TBD) | Partial | Provider adapters/smoke tests exist; enterprise TLS/proxy/rate/fallback tests TBD. |
| E3-06 | Per-user/tenant budgets are enforced | Runtime/product (TBD) | Partial | Maximum agent steps exists; token/cost/concurrency/output/tool budgets TBD. |
| E3-07 | Health checks and runbooks recover documented failures | Operations (TBD) | Partial | `loro doctor` and backend checks exist; identity/audit health plus exercised runbooks TBD. |

## Phase 4: Verification And Supply Chain

| ID | Exit item | Owner | Status | Evidence and remaining proof |
| --- | --- | --- | --- | --- |
| E4-01 | Postgres and Polaris/Iceberg integration runs automatically in protected CI | Release/data (TBD) | Partial | `.github/workflows/integration.yml` is manual/opt-in; protected scheduled path and full Iceberg transaction TBD. |
| E4-02 | End-to-end reference deployment tests pass | Release/security (TBD) | Planned | Identity-to-audit E2E suite and production-like report TBD. |
| E4-03 | Provider contracts use sanitized fixtures and controlled live smoke | Runtime/release (TBD) | Partial | Unit and manually executed smoke coverage exists; fixture governance and protected live schedule TBD. |
| E4-04 | Security-critical branch coverage target passes | Release/security (TBD) | Partial | Overall 70% branch threshold exists; module-specific target and bypass tests TBD. |
| E4-05 | Dependency, secret, license, and static scans follow triage policy | Release/security (TBD) | Planned | CI jobs, policy, SLA, and exception register TBD. |
| E4-06 | Release has SBOM, provenance, signature, and verification | Release (TBD) | Planned | Build/Twine checklist exists; signed artifact evidence TBD. |
| E4-07 | Upgrade, rollback, migration, support, and disclosure policies work | Release/operations (TBD) | Partial | Release checklist exists; tested upgrade/rollback and disclosure policy TBD. |

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
