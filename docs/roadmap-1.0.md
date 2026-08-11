# Loro Roadmap To 1.0

## Purpose

This is the single authoritative roadmap for work remaining after Loro `0.7.0`. It covers the
`0.8` enterprise beta, the `0.9` release candidate, and the first stable `1.0` release. Completed
milestones belong in release notes; implementation proof belongs in the
[Enterprise Evidence Register](enterprise-evidence.md).

No calendar date is promised. A milestone ships when its repository-owned work is complete and
its required controlled-environment or adopting-organization evidence is linked. Patch releases
may ship between these milestones.

## Current Baseline

Loro `0.7.0` was released on August 11, 2026. The repository currently provides:

- a bounded Python CLI agent loop for coding and productivity work;
- document, presentation, spreadsheet, brief, and Agentic Graph generation;
- local memory and explicit-user-only shared-memory commits through Postgres or Iceberg;
- constrained Apache Polaris REST and CLI access;
- versioned configuration, identity context, policy, approvals, sandbox profiles, data
  protection, audit delivery, metrics, and recovery tooling;
- governed provider contracts, MCP client/server support, Agent Skills, Claude/Pi skill import,
  AGS 1.0 execution, and six authenticated channel adapters;
- OS-vaulted named credentials, security scans, SBOMs, checksums, release manifests, and build
  provenance.

The [0.7 release notes](releases/0.7.0.md), [support matrix](support-matrix.json),
[interoperability matrix](interoperability-matrix.json), and
[data support matrix](data-support-matrix.json) describe the exact shipped boundary.

## The 1.0 Promise

Loro `1.0` will be a stable, enterprise-governed CLI harness whose supported reference
deployment can:

- run approved coding and productivity tasks through governed AI routes;
- keep provider credentials isolated from tools and subprocesses;
- maintain local memory while requiring an explicit user decision for every shared-memory write;
- use tenant-scoped Postgres shared memory and constrained Polaris-governed data access;
- use supported MCP, Agent Skills, graph, and channel integrations without bypassing Loro policy;
- attribute consequential actions to identity, tenant, policy, approval, session, and target;
- survive documented provider, database, audit, and process failures without silent data loss or
  security downgrade;
- provide administrators, operators, reviewers, plugin authors, gateway owners, and users with a
  reproducible deployment and support contract;
- produce verifiable operational, security, compatibility, and release evidence.

The stable promise applies only to the frozen `1.0` support matrix. Integrations without enough
proof remain experimental or are excluded rather than weakening the release gate.

## Release Principles

- **Evidence gates releases.** Code, tests, docs, hosted workflows, and required external proof
  must agree.
- **No silent downgrade.** Required identity, policy, sandbox, data protection, audit, tenant, or
  provider-route controls fail closed.
- **Shared memory stays explicit.** Models and remote messages cannot commit shared memory without
  a distinct user-authorized flow.
- **Compatibility is deliberate.** Supported CLI, config, storage, audit, provider, MCP, Skill,
  graph, and gateway contracts receive documented migration or deprecation handling.
- **Scope may shrink.** Unsupported matrix cells are removed or marked experimental; risk is not
  hidden behind a broad claim.
- **External proof remains external.** Corporate identity, production infrastructure, app
  ownership, legal approval, independent testing, and operational ownership cannot be simulated
  into existence by repository tests.

## Milestones

| Release | Status | Outcome |
| --- | --- | --- |
| `0.8` | Next | A reproducible reference deployment is measurable and operable for a restricted beta. |
| `0.9` | Planned | The stable boundary is frozen and exercised through a controlled pilot and independent assurance. |
| `1.0` | Planned | Approved stable contracts, ownership, evidence, and public release artifacts are complete. |

## 0.8: Enterprise Beta

**Goal:** make one narrow reference deployment reproducible, observable, supportable, and ready
for restricted users.

### Batch 1: Freeze The Reference Deployment

- Freeze proposed `1.0` versions for Linux, Python, Loro, the approved provider gateway,
  Postgres, Polaris/Iceberg, MCP, and channel adapters.
- Publish versioned deployment manifests and managed configuration examples with no embedded
  credentials.
- Make installation, configuration validation, migration, backup, restore, rollback, and
  uninstallation reproducible in a clean environment.
- Exercise upgrade from `0.7` to `0.8` and rollback without losing committed memory, approvals,
  sessions, graph records, or audit continuity.
- Decide whether Polaris/Iceberg shared memory has enough protected evidence for stable `1.0`
  support; otherwise keep it experimental.

### Batch 2: Complete Role-Based Documentation

- Publish focused guides for administrators, operators, security/privacy reviewers, data
  stewards, MCP/Skill authors, gateway administrators, and end users.
- Document data flows, classifications, telemetry, retention, deletion, credential ownership,
  unsupported uses, incident escalation, and support intake.
- Keep CLI help, PyPI documentation, machine-readable matrices, runbooks, and examples aligned.
- Add automated checks for stale commands, versions, links, matrix claims, and packaged docs.

### Batch 3: Reliability And Performance Baselines

- Define versioned targets for startup, orchestration overhead, memory retrieval, artifact
  creation, graph scheduling, gateway queueing, audit delivery, and recovery.
- Add reproducible benchmark tooling that records environment and configuration without prompt
  content.
- Run concurrency, soak, provider outage, database outage, audit outage, disk-pressure, queue
  saturation, retry, and cancellation tests against the reference deployment.
- Prove declared RPO/RTO, audit-buffer bounds, replay behavior, budget enforcement, and
  operator-visible degradation.

### Batch 4: Restricted-Beta Operations

- Define a beta charter, approved users/data, observation period, success measures, privacy
  notice, support path, and accountable owners.
- Add content-free product/operational metrics only after privacy review.
- Exercise provider and gateway credential rotation, policy rollout/rollback, tenant
  offboarding, emergency disable, data lifecycle jobs, and audit reconciliation.
- Run the deployment checklist with organization-owned identity, DLP, database, catalog, audit,
  ingress, and provider controls.

### 0.8 Exit Gate

- A clean environment reproduces the supported deployment from versioned documentation.
- Upgrade, rollback, backup, restore, and uninstall drills pass without unacknowledged loss.
- Declared beta reliability/performance targets pass at the restricted load.
- Every supported matrix cell links to a green release-commit or controlled-environment result.
- Evidence `E0-01` through `E0-06`, `E3-02` through `E3-07`, `E4-01`, `E4-02`, `E4-07`, and
  repository-owned portions of `E5-01` through `E5-03` and `E5-08` are complete or explicitly
  external with owners and dates.

## 0.9: Release Candidate

**Goal:** freeze the stable product boundary and collect the assurance required for general
availability.

### Batch 1: Feature And Compatibility Freeze

- Freeze the supported CLI, configuration, records, schemas, providers, MCP revisions, Skill
  subset, AGS behavior, gateways, Python versions, and deployment components.
- Allow only release-blocking fixes, documentation corrections, dependency/security updates,
  and evidence work after the freeze.
- Publish complete compatibility, deprecation, upgrade, rollback, support, vulnerability, and
  known-limitations policies.

### Batch 2: Controlled Pilot

- Complete the restricted pilot for the approved duration and load.
- Triage every pilot defect and resolve or formally disposition all severity 1 and severity 2
  findings.
- Validate support intake, operator handoff, privacy behavior, tenant offboarding, and data
  correction/deletion in the real pilot environment.
- Confirm that content-free metrics answer the approved success measures without collecting
  prompts, model responses, tool output, or memory content.

### Batch 3: Independent Assurance

- Complete independent penetration testing and resolve or formally accept every finding.
- Conduct incident-response, credential-compromise, provider-outage, audit-outage, database
  recovery, policy rollback, offboarding, and disaster-recovery exercises.
- Obtain security, privacy, legal, data-governance, operations, product, and release review of the
  final supported boundary.

### Batch 4: Release Administration

- Verify protected branches/tags, required reviews, ownership rules, trusted PyPI publishing,
  artifact provenance, SBOM, checksums, attestations, and consumer-side verification.
- Run the complete unit, branch, integration, conformance, adversarial, migration, load,
  recovery, and clean-install matrix from protected systems.
- Reproduce the release candidate from a clean checkout and verify public-install instructions in
  an isolated environment.

### 0.9 Exit Gate

- No unresolved critical/high security finding or release-blocking correctness/data-loss defect.
- Every stable matrix cell has green evidence; unsupported cells are removed or clearly marked.
- Evidence `E4-02`, `E4-05` through `E4-10`, and `E5-01` through `E5-08` is complete, or an
  external gate has a controlled reference and explicit approval.
- Product, engineering, security, privacy, legal, data, operations, and release owners approve
  the candidate for GA.

## 1.0: General Availability

`1.0` promotes a proven release candidate. It is not a feature-expansion release.

### Required Release Artifacts

- signed tag and immutable source commit;
- protected-CI wheel and source distribution;
- checksums, SBOM, provenance/attestation, and consumer verification instructions;
- release manifest linking version, commit, workflows, matrices, known limitations, and evidence;
- compatibility, support, deprecation, upgrade, rollback, uninstall, and vulnerability policies;
- administrator, operator, security/privacy, data, gateway, extension-author, and user docs;
- public supported/unsupported-use statement and incident/support contact path.

### GA Gate

- All `0.9` exit evidence remains valid for the final commit and artifacts.
- Public PyPI clean install, upgrade, rollback, and basic agent-task smoke tests pass.
- Named support/on-call ownership, escalation, vulnerability response, and release authority are
  active.
- The evidence register and all support matrices contain no ambiguous stable claims.
- Executive/product, engineering, security, privacy, legal, data, operations, and release
  sign-off is recorded outside the repository and linked by controlled reference.

### Stabilization

- Monitor provider, gateway, audit, memory, reliability, security, and support signals for the
  declared stabilization window.
- Publish patch releases for qualifying defects without expanding the stable surface.
- Review dependencies, evidence, providers, protocols, and support ownership on a documented
  cadence.

## External Dependencies

The repository can prepare procedures and validate reference implementations, but these items
require an adopting organization or controlled environment:

- verified corporate identity assertions and authenticated managed-policy distribution;
- production Bubblewrap/container enforcement and corporate DLP/classification integration;
- least-privilege Postgres roles, Polaris authorization, object-store controls, encryption, and
  organization-approved RPO/RTO results;
- immutable audit retention, independent chain anchoring, production monitoring, and alerting;
- approved provider residency/retention/rate routes and organization-wide budgets;
- governed Slack, Discord, Telegram, Teams, or Signal applications plus TLS/rate-limited ingress;
- named owners, privacy/legal decisions, pilot participation, support/on-call processes,
  independent penetration testing, incident exercises, and GA sign-off.

Use [External Enterprise Requirements](external-enterprise-requirements.md) to record owners,
dates, controlled references, results, accepted risks, and remediation.

## Deferred Beyond 1.0

- autonomous shared-memory commits;
- governed-data mutation through Polaris;
- a graphical administration console;
- universal support for every provider, identity system, SIEM, secret manager, chat service, MCP
  revision, Claude plugin, or Pi package;
- multi-region active-active coordination unless the reference deployment requires it;
- formal compliance certification as a substitute for implemented controls and evidence.

## Tracking

Every roadmap issue or pull request should identify the target milestone, owner, linked evidence
IDs, implementation/test/doc changes, rollout and rollback impact, security/privacy and support
matrix impact, and external dependencies. At each release, update this roadmap, the evidence
register, support matrices, and release notes together. A status label without linked proof does
not advance the milestone.
