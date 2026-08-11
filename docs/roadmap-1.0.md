# Roadmap To Loro 1.0

## Purpose

This is the release plan from Loro `0.5.0` to the first stable `1.0` release. It consolidates
the product work in the [Development Roadmap](roadmap.md), the controls in the
[Enterprise Readiness Roadmap](enterprise-readiness-roadmap.md), and the proof tracked by the
[Enterprise Evidence Register](enterprise-evidence.md).

Loro already has a broad alpha feature set. The road to `1.0` is therefore not primarily a
feature-accumulation exercise. It is a program to narrow the supported deployment, harden its
contracts, prove it under failure, and establish a supportable compatibility boundary.

No calendar date is promised by this roadmap. A milestone ships when its exit evidence is
complete. Patch releases may occur between the milestones below.

## The 1.0 Product Promise

Loro `1.0` will be a stable, enterprise-governed Python CLI agent harness that can:

- run coding and productivity tasks through approved AI providers;
- create documents, presentations, spreadsheets, briefs, and governed Agentic Graphs;
- use local memory while requiring an explicit user decision for every shared-memory commit;
- share memory through Postgres and support Polaris-governed Iceberg as the scale-out backend;
- access governed data through constrained Polaris REST and CLI operations;
- use MCP servers and Agent Skills without granting them authority beyond Loro policy;
- receive authenticated remote work through supported chat gateways;
- keep named provider and gateway credentials in an operating-system credential vault;
- attribute consequential actions to identity, policy, approval, session, and target;
- produce verifiable audit, release, and operational evidence.

The initial stable boundary is the documented reference deployment, not every possible
provider, identity system, operating system, chat service, catalog, or audit destination.

## Current Baseline

The `0.5.0` release is the current published baseline. It includes the agent loop, provider adapters,
productivity artifacts, local and shared memory, Postgres and Iceberg adapters, Polaris access,
policy and approvals, sandbox profiles, audit delivery, MCP, Agent Skills, Agentic Graphs,
credential storage, remote gateways, tests, security scans, SBOM generation, checksums, and
build provenance.

The remaining risk is concentrated in four areas:

1. Production identity, policy distribution, isolation, data protection, and tenant proof.
2. Production-like Postgres, Polaris/Iceberg, audit, gateway, and provider evidence.
3. Upgrade, rollback, compatibility, performance, and operational discipline.
4. Pilot, independent assurance, ownership, and support readiness.

## Release Rules

- **Evidence gates releases.** Implementation, automated tests, documentation, and required
  external proof must all be present.
- **No silent security downgrade.** Missing identity, managed policy, sandbox, audit, or data
  controls fail closed when the reference deployment requires them.
- **Shared memory stays explicit.** No milestone permits a model to autonomously commit shared
  memory.
- **Stable surfaces freeze progressively.** Configuration, CLI, stored records, provider
  adapters, memory schemas, audit events, graph records, and plugin protocols receive explicit
  compatibility policies before `1.0`.
- **Release scope may shrink.** A backend or integration without sufficient proof is marked
  experimental or excluded from the `1.0` supported matrix rather than weakening the gate.
- **External proof is real work.** Items in
  [External Enterprise Requirements](external-enterprise-requirements.md) require an adopting
  organization or controlled environment and cannot be closed by repository code alone.

## Milestone Summary

| Release | Status | Theme | Primary outcome |
| --- | --- | --- | --- |
| `0.5` | Repository work complete | Control contracts | Security-critical interfaces are versioned, testable, and complete. |
| `0.6` | Repository work complete | Data and operations | The reference data and audit path survives realistic lifecycle and failure tests. |
| `0.7` | Planned | Interoperability | Providers, MCP, Skills, graphs, and gateways have governed compatibility evidence. |
| `0.8` | Planned | Enterprise beta | A reproducible reference deployment is operable by pilot administrators and users. |
| `0.9` | Planned | Release candidate | The product is feature-frozen and independently exercised in a controlled pilot. |
| `1.0` | Planned | General availability | Stable contracts, operational ownership, assurance, and release evidence are complete. |

## 0.5: Control Contracts

Status: **repository work complete for 0.5.0**. Corporate identity, policy-signing authority,
production Bubblewrap/DLP, protected release administration, and external security review remain
explicit deployment evidence rather than repository claims.

**Goal:** close repository-side ambiguity in the controls that every later milestone depends on.

Implementation batches:

1. **Configuration and compatibility**
   - Add an explicit configuration schema version and tested migration path for every persisted
     configuration shape that will survive into `1.0`.
   - Publish machine-readable supported and experimental feature matrices.
   - Define deprecation warnings and reject unsupported future schema versions safely.
2. **Identity, policy, and approvals**
   - Complete an event-family audit proving every consequential command and runtime tool records
     actor, tenant, policy source/version, normalized target, decision, approval, and result.
   - Add a durable approval-store interface suitable for an enterprise implementation while
     retaining a secure local default.
   - Add adversarial tests for encoded targets, policy ambiguity, replay, clock boundaries,
     cross-session authority, and model-originated approval data.
3. **Isolation and data protection**
   - Extend the hostile fixture suite for prompt injection, poisoned memory, archive traversal,
     symlink races, oversized output, hostile MCP servers, and hostile Skill packages.
   - Verify every subprocess family uses an allowlisted environment and the requested sandbox
     profile.
   - Complete recursive DLP tests for model requests/responses, tool arguments/results, memory,
     artifacts, sessions, gateways, graphs, and audit metadata.
4. **Release engineering**
   - Make the evidence register checkable in CI for missing IDs, invalid states, and broken links.
   - Add a release manifest containing commit, version, artifact digests, SBOM, provenance,
     workflow runs, compatibility matrix, and known limitations.

Exit gate:

- Security-critical tests and module coverage floors pass on every supported Python version.
- Configuration upgrade and downgrade-failure tests pass from every supported pre-`1.0` shape.
- The audit event-family inventory has no unexplained consequential-action gaps.
- Evidence items `E1-01` through `E1-06`, `E2-01` through `E2-05`, `E2-07`, `E2-08`, `E3-01`,
  `E4-04`, and repository-owned parts of `E4-05` have linked evidence or an explicit external
  dependency.

## 0.6: Enterprise Data And Operations

Status: **repository work complete for 0.6.0**. Protected Polaris authorization, managed
object-store behavior, immutable audit retention, and organization-approved recovery objectives
remain external deployment evidence. See the [0.6 work record](roadmap-0.6.md).

**Goal:** prove the reference storage, governed-data, audit, and recovery paths under realistic
load and failure.

Implementation batches:

1. **Postgres shared memory**
   - Run create, search, draft, explicit commit, correction, expiration, deletion, legal hold,
     tenant denial, concurrency, and idempotent retry tests against ephemeral Postgres in CI.
   - Publish versioned migrations and forward/rollback procedures.
   - Add reconciliation diagnostics for state rows and append-only lifecycle events.
2. **Polaris and Iceberg**
   - Exercise credentialed Polaris CLI and REST authorization against a protected environment.
   - Test delegated credential expiry, namespace/table denials, Iceberg snapshot behavior,
     lifecycle idempotency, concurrent writers, and DuckDB-readable seeded data.
   - Pin and publish the supported Polaris, PyIceberg, Iceberg REST, object-store, and DuckDB
     matrix.
3. **Audit and observability**
   - Provide a reference authenticated audit collector deployment and failure-injection harness.
   - Add metrics/export hooks for task latency, provider usage, policy decisions, approvals,
     memory operations, gateway backlog, and audit delivery without prompt-content collection.
   - Prove retry, deduplication, bounded buffering, chain verification, external anchoring, and
     operator-visible failure.
4. **Recovery**
   - Automate install, database migration, backup, restore, reconciliation, and rollback drills
     for the reference deployment where practical.
   - Turn the provider, policy, audit, database, and catalog failure procedures into executable
     operator checks.

Exit gate:

- Protected Postgres and Polaris/Iceberg reports include positive and cross-tenant negative
  results.
- No acknowledged audit event is silently lost in the tested outage window.
- A restore drill meets declared RPO/RTO targets in the reference environment.
- Evidence items `E2-04` through `E2-06`, `E3-02` through `E3-07`, `E4-01`, and data portions of
  `E4-02` are closed for the reference deployment or explicitly marked external for the pilot.

## 0.7: Governed Interoperability

**Goal:** make Loro's extension and communication surfaces predictable across supported peers.

Implementation batches:

1. **Provider contracts**
   - Maintain sanitized response fixtures and contract tests for every advertised protocol
     family, including streaming, native tools, usage, malformed responses, retries, and errors.
   - Run protected live smoke tests for the release's supported provider matrix.
   - Verify gateway CA/proxy behavior, residency-safe routing, request IDs, rate limits, budget
     reporting, and no unapproved cross-provider fallback.
2. **MCP and Agent Skills**
   - Produce green protected conformance artifacts for every advertised classic and current MCP
     revision, transport, and client/server role.
   - Add hostile interoperability fixtures for capability confusion, version downgrade,
     extension data, Tasks, subscriptions, and cancellation.
   - Freeze the supported Claude and Pi compatibility subset and report unsupported host
     behavior explicitly.
3. **Agentic Graphs**
   - Run pinned AGS conformance for validation and records plus controlled live-provider graph
     execution under managed policy.
   - Prove approval, retry, fallback, compensation, resume, and budget behavior through failure
     injection.
4. **Remote gateways**
   - Exercise supported Slack, Discord, Telegram, Teams, Signal-bridge, and generic adapters
     against governed test applications or signed platform fixtures.
   - Test signature rejection, replay, channel/tenant mismatch, credential rotation, duplicate
     delivery, overload, cancellation, and audit correlation.

Exit gate:

- The provider and protocol support matrices link to green release-commit evidence.
- Unsupported models, protocol revisions, plugin capabilities, and gateway event types fail
  clearly and safely.
- Evidence items `E3-05`, `E4-03`, `E4-08`, `E4-09`, and `E4-10` are closed for the advertised
  `1.0` compatibility set.

## 0.8: Enterprise Beta

**Goal:** deliver a reproducible, observable reference deployment to a restricted user pilot.

Implementation batches:

1. **Deployment and support matrix**
   - Freeze the proposed `1.0` operating systems, Python versions, provider gateway, Postgres,
     Polaris/Iceberg, MCP, and gateway matrix.
   - Publish reproducible installation, managed configuration, upgrade, rollback, and
     uninstallation procedures.
2. **Role-based documentation**
   - Publish separate administrator, operator, security reviewer, data steward, plugin author,
     gateway administrator, and end-user guides.
   - Document data flows, telemetry, privacy behavior, retention, unsupported uses, and incident
     escalation.
3. **Reliability and performance**
   - Establish versioned benchmarks for startup, task latency overhead, memory retrieval,
     artifact creation, graph scheduling, gateway queueing, and audit delivery.
   - Run concurrency, soak, provider outage, database outage, audit outage, and disk-pressure
     tests against the reference deployment.
4. **Pilot operations**
   - Add privacy-reviewed, content-free pilot metrics and a support intake template.
   - Exercise credential rotation, policy rollout/rollback, tenant offboarding, and emergency
     disable procedures.

Exit gate:

- A clean environment can reproduce the reference deployment from versioned documentation.
- Upgrade from `0.7` and rollback from `0.8` are tested without losing committed memory or audit
  evidence.
- Declared reliability and performance targets pass for the restricted pilot load.
- Evidence items `E0-01` through `E0-06`, `E4-07`, `E5-01` through `E5-04`, and `E5-08` have
  named owners and dated pilot evidence.

## 0.9: Release Candidate

**Goal:** freeze the stable product boundary and collect the assurance needed for GA.

Release-candidate rules:

- Feature freeze: only release-blocking defects, documentation corrections, dependency updates,
  and evidence work enter the candidate branch.
- Run the complete unit, integration, conformance, security, adversarial, migration, load,
  recovery, and clean-install matrix from protected CI and controlled environments.
- Complete a restricted pilot over the agreed observation period and disposition every severity
  1 or severity 2 defect.
- Complete independent penetration testing and remediate or formally accept findings.
- Rehearse incident response, provider outage, audit outage, database recovery, policy rollback,
  credential compromise, offboarding, and package rollback.
- Verify protected branches/tags, required reviews, trusted PyPI publishing, artifact provenance,
  SBOM, checksums, and consumer-side attestation verification.
- Freeze the `1.0` CLI/configuration/API/storage compatibility statement and publish every known
  limitation.

Exit gate:

- No unresolved critical or high security finding and no open release-blocking correctness or
  data-loss defect.
- Every supported matrix cell has a green result or a signed risk decision that narrows support.
- Evidence items `E4-02`, `E4-05` through `E4-07`, and `E5-01` through `E5-08` are complete.
- Product, engineering, security, privacy, legal, operations, and release owners approve the
  candidate for GA.

## 1.0: General Availability

`1.0` is a promotion of a proven release candidate, not a new feature release.

Required release artifacts:

- signed Git tag and immutable source commit;
- wheel and source distribution built through protected CI;
- checksums, SBOM, provenance/attestation, and consumer verification instructions;
- complete release manifest and evidence links;
- compatibility, support, deprecation, upgrade, rollback, and vulnerability policies;
- administrator, operator, security, privacy, data, gateway, plugin, and user documentation;
- public known limitations and unsupported-use list;
- support/on-call ownership and incident escalation path.

Post-release requirements:

- Perform immediate clean-install and upgrade smoke tests from public PyPI.
- Monitor provider, gateway, audit, memory, and support signals for the defined stabilization
  window.
- Publish patch releases for qualifying defects without expanding the stable surface.
- Review evidence, dependency risk, and provider/protocol compatibility on a documented cadence.

## 1.0 Quality Gates

Every `1.0` candidate must meet all of the following:

| Gate | Minimum proof |
| --- | --- |
| Correctness | Unit, branch, integration, end-to-end, migration, rollback, and recovery suites pass. |
| Security | Threat model current; no unaccepted critical/high findings; adversarial and isolation suites pass. |
| Authorization | Cross-tenant denials and exact approval/policy tests pass on every consequential surface. |
| Memory safety | Shared writes remain explicit; lifecycle, idempotency, retention, hold, correction, and deletion are proven. |
| Governed data | Polaris REST/CLI and Iceberg access obey the advertised authorization boundary. |
| Interoperability | Provider, MCP, Skills, AGS, and gateway matrices link to green evidence. |
| Reliability | Declared SLOs, budgets, failure injection, audit durability, and restore objectives pass. |
| Supply chain | Protected build, SBOM, scans, checksums, provenance, signing, and clean public install pass. |
| Operations | Runbooks, telemetry, ownership, incident response, offboarding, and disaster recovery are exercised. |
| Product | Pilot measures pass and supported/unsupported uses are approved and published. |

Numeric SLOs, pilot targets, vulnerability remediation windows, support response objectives, and
RPO/RTO values must be chosen by accountable owners during `0.8`; until then they are open gates,
not implicitly satisfied targets.

## Explicitly Deferred Beyond 1.0

- Autonomous shared-memory commits.
- Governed-data mutation through Polaris.
- A graphical administration console.
- Universal support for every provider, identity platform, SIEM, secrets manager, chat service,
  MCP revision, Claude plugin, or Pi package.
- Multi-region active-active coordination unless required by the reference deployment.
- Formal compliance certification as a substitute for control implementation and evidence.

## Tracking The Work

Use this document for release sequencing and use
[Enterprise Evidence Register](enterprise-evidence.md) for closure. Each implementation issue or
pull request should identify:

- target milestone (`0.5` through `1.0`);
- owning workstream and accountable owner;
- linked evidence IDs;
- implementation, test, documentation, rollout, and rollback changes;
- security/privacy impact and supported-matrix impact;
- external dependency, if any.

At each minor release, update this roadmap, the evidence register, the support matrices, and
the release notes together. A checked box without linked proof does not advance the milestone.
