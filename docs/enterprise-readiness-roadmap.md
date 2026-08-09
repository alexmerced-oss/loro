# Enterprise Readiness Roadmap

## Purpose

This roadmap takes Loro from a functioning alpha CLI agent harness to a product that an
enterprise can deploy, govern, operate, and support with confidence. It begins where the MVP
roadmap ends: the agent loop, provider adapters, permission engine, local and shared memory,
Polaris/Iceberg integration, audit logging, CI, and release process already exist.

Enterprise readiness is not a single release. Loro is ready when the controls below are
implemented, tested in a production-like environment, documented for operators, and backed by
an incident and support process.

## Current Position

Status as of August 2026: **Alpha; MVP capabilities complete; enterprise hardening in progress.**

| Area | Current state | Readiness |
| --- | --- | --- |
| Agent runtime | Bounded model/tool loop, sessions, typed tools, provider-normalized tool calls | MVP complete |
| Configuration | Layered config plus non-overridable managed overlays | Implemented; deployment validation needed |
| Permissions | `allow` / `ask` / `deny`, normalized structured rules, identity-bound interactive approval records | Partial; signed policy artifacts and security review missing |
| Audit | Versioned JSONL/HTTP events, retry, bounded buffer, doctor/flush | Partial; destination immutability and production evidence missing |
| Identity | Typed local/config/environment context, managed required fields, audit/session propagation | Foundation implemented; corporate assertion verification and authorization binding missing |
| Isolation | Tool-level policy and working-directory boundaries | Partial; enforceable sandbox profiles missing |
| Shared memory | Tenant-aware Postgres/Iceberg schema, proposals, explicit commits, citations | Implemented; isolation and lifecycle proof needed |
| Governed data | Read-only Polaris allowlist and Iceberg integration | MVP complete; authorization evidence and production tests needed |
| Providers | Multiple adapters, streaming, normalized errors, smoke tests | MVP complete; gateway, resilience, and spend controls needed |
| MCP and skills | Product requirements only; no runtime implementation | Planned; dual-era protocol, supply-chain, sandbox, and conformance work defined |
| Delivery | Unit tests, coverage threshold, CI, manual integration workflow, release checklist | Healthy alpha; supply-chain and release evidence missing |

The unit test suite is the current strongest quality signal. Postgres and Polaris integration
tests exist but are opt-in, so they do not yet prove the complete enterprise deployment path.

## Readiness Principles

- **Deny by default.** Unknown tools, actions, resources, identities, and data scopes do not run.
- **A human owns consequential actions.** Approval is explicit, attributable, scoped, and
  recorded; a model cannot approve its own request.
- **Tenant boundaries are enforced below the prompt layer.** Prompt instructions are never a
  security boundary.
- **Secrets and sensitive content are minimized.** Logs, model requests, memory, and artifacts
  carry only the data required for the task.
- **Every important action is explainable.** Operators can reconstruct who requested an action,
  which policy allowed it, what ran, and what changed.
- **Failure is bounded.** Time, steps, cost, output size, retries, and tool reach have explicit
  limits.
- **Evidence closes a milestone.** Code alone is insufficient; tests, runbooks, and deployment
  evidence are part of each exit gate.

## Target Deployment Model

The first supported enterprise shape should be intentionally narrow:

- A centrally managed Loro CLI installed on employee workstations or controlled development
  environments.
- An internal OpenAI-compatible model gateway or approved direct provider endpoints.
- Managed, non-overridable policy distributed by enterprise configuration management.
- Corporate identity propagated to Loro, model requests, approvals, memory operations, and
  audit events.
- Postgres for the first production shared-memory deployment; Polaris-governed Iceberg is the
  scale-out path after isolation and lifecycle behavior are proven.
- A durable enterprise audit destination such as an HTTP collector, SIEM, or object store,
  with local JSONL retained as an optional development sink.

Supporting one reference deployment first keeps security review and operational testing
tractable. Additional identity providers, gateways, and audit destinations can follow through
stable interfaces.

## Phase 0: Baseline And Ownership

**Goal:** turn the current alpha into an explicitly owned hardening program.

Phase 0 working documents:

- [Threat Model](threat-model.md)
- [Enterprise Data Classification](data-classification.md)
- [Enterprise Reference Deployment](reference-deployment.md)
- [Enterprise Readiness Evidence Register](enterprise-evidence.md)

Deliverables:

- Assign owners for runtime, identity, permissions, memory/data, security, and release.
- Create a threat model covering prompt injection, tool misuse, credential exposure, tenant
  crossover, audit tampering, dependency compromise, and provider data leakage.
- Define data classifications and which classes may enter prompts, memory, artifacts, and logs.
- Publish the supported deployment matrix: operating systems, Python versions, model gateway,
  Postgres version, Polaris version, and Iceberg catalog configuration.
- Convert this roadmap into tracked work with an owner and evidence link for every exit item.
- Record baseline performance, provider cost, reliability, and integration-test results.

Exit gate:

- Threat model and data-flow diagram are reviewed by security and engineering.
- Every later phase has accountable owners and testable acceptance criteria.
- The reference deployment can be reproduced from versioned documentation.

## Phase 1: Identity, Policy, And Approval

**Goal:** make every sensitive action attributable and policy controlled.

Deliverables:

- Add an identity abstraction with subject, organization, groups/roles, authentication method,
  and session identifier.
- Integrate the reference corporate identity method, such as OIDC device flow, workload
  identity, or identity asserted by an internal model gateway.
- Add interactive approval prompts for file writes, shell commands, Git mutations, shared
  memory commits, and governed data actions.
- Show the exact action, target, relevant diff or command, risk reason, and policy source before
  approval.
- Support approve once, deny, and narrowly scoped session approval; do not add blanket
  approvals for arbitrary shell or write access.
- Bind approvals to identity, exact normalized arguments, policy version, expiration, and
  session. Reject replay or changed arguments.
- Extend permission matching to normalized filesystem paths, command/executable, Git operation,
  tenant, catalog, namespace, table, and data action.
- Add policy validation and an explanation command that reports why a request is allowed,
  denied, or requires approval.
- Fail closed when identity or managed policy cannot be loaded or validated.

Exit gate:

- Tests prove that the model cannot self-approve, mutate approval arguments, bypass a deny with
  path or command encoding, or reuse an expired approval.
- Every consequential audit event includes actor, policy decision, policy version, approval
  identity, and normalized target.
- Security review approves the permission model for a limited pilot.

## Phase 2: Isolation And Data Protection

**Goal:** contain tool execution and prevent sensitive-data leakage or tenant crossover.

Deliverables:

- Introduce named sandbox profiles for read-only analysis, repository editing, controlled shell,
  and governed data access.
- Enforce workspace roots, symlink-safe path resolution, environment-variable allowlists,
  subprocess timeouts, output limits, and network policy outside model instructions.
- Separate provider credentials from tool subprocess environments and redact inherited secrets.
- Replace the MVP secret scanner with a pluggable data-protection interface supporting managed
  secret scanning, data classification, allowlists, and policy-based blocking.
- Apply content controls consistently to model input/output, memory, artifacts, tool output,
  session records, and audit events.
- Enforce tenant and scope filters in storage queries and authorization checks, not only in CLI
  arguments.
- Define memory retention, expiration, deletion, legal hold, correction, and provenance rules.
- Encrypt shared memory and audit data in transit and at rest using enterprise-managed keys.
- Add adversarial tests for prompt injection, malicious repository content, poisoned shared
  memory, unsafe archive/symlink paths, and oversized tool output.
- Apply the same sandbox, environment, network, trust-label, and approval controls to planned
  MCP servers and Agent Skills; neither remote capabilities nor skill metadata may expand
  authority.

Exit gate:

- Sandbox escape and cross-tenant test suites pass in CI and a production-like environment.
- A data-protection review documents permitted data flows for each supported provider.
- Retention and deletion tests demonstrate that policy is enforced and auditable.

## Phase 3: Audit, Operations, And Reliability

**Goal:** make Loro observable, supportable, and recoverable in daily enterprise use.

Deliverables:

- Define a versioned audit-event schema with event id, timestamp, actor, tenant, session, trace,
  action, target, policy decision, approval, result, and redaction metadata.
- Add a durable audit-sink interface and one reference external sink with authenticated delivery,
  batching, retry/backoff, bounded local buffering, and failure visibility.
- Add tamper-evidence or destination-side immutability and document clock synchronization and
  retention requirements.
- Add structured application logs, metrics, and traces for latency, errors, tool denials,
  approvals, provider usage, token/cost estimates, memory operations, and audit delivery.
- Add model-gateway support with enterprise TLS, proxy, certificate, timeout, retry, rate-limit,
  and request-id behavior.
- Add per-user and per-tenant budgets for steps, tokens, cost, concurrency, output, and tool
  runtime, with clear stop reasons.
- Define provider fallback behavior without silently changing data residency or model policy.
- Add health checks for identity, policy, provider, audit sink, Postgres, Polaris, and Iceberg.
- Record MCP server identity, transport, negotiated protocol revision, capabilities, extensions,
  and skill source/digest in audit and health diagnostics when those features are implemented.
- Publish runbooks for credential rotation, provider outage, audit backlog, policy rollback,
  memory recovery, tenant offboarding, and suspected data exposure.

Exit gate:

- Load and failure-injection tests meet agreed service objectives for the reference deployment.
- No audited action is silently lost during a tested destination outage.
- An operator can detect, diagnose, and recover each documented failure using the runbooks.

## Phase 4: Verification And Supply Chain

**Goal:** produce repeatable evidence that releases are secure and behave correctly end to end.

Deliverables:

- Run Postgres, Polaris, and Iceberg integration tests automatically in protected CI or a
  scheduled production-like environment rather than only through manual opt-in.
- Add end-to-end tests spanning identity, managed policy, approval, tool execution, memory,
  provider gateway, and external audit delivery.
- Add provider contract tests using recorded sanitized fixtures plus controlled live smoke tests.
- Run MCP conformance and dual-era interoperability tests for every advertised revision,
  transport, extension, and client/server role; validate Agent Skills against official fixtures.
- Raise coverage expectations for security-critical modules and add branch-focused tests for
  policy, approvals, path handling, redaction, identity, and audit delivery.
- Add dependency vulnerability, secret, license, and static-analysis checks with an explicit
  triage policy and remediation targets.
- Pin and review build dependencies; generate an SBOM and provenance attestation for every
  release artifact.
- Sign release artifacts and verify them in the fresh-environment installation smoke test.
- Define supported-version, upgrade, rollback, configuration-migration, and vulnerability-
  disclosure policies.

Exit gate:

- A clean checkout produces a signed, verifiable artifact through protected CI.
- The reference deployment passes end-to-end, isolation, upgrade, rollback, and recovery tests.
- Critical and high security findings are resolved or formally risk accepted before release.

## Phase 5: Pilot And General Availability

**Goal:** validate the controls with real users, then establish a supportable GA boundary.

Pilot deliverables:

- Start with a small group, approved repositories, non-production data, and the most restrictive
  sandbox profile.
- Publish administrator, operator, user, privacy, and incident-response documentation.
- Instrument opt-in usage and collect approval friction, denied-action, failure, latency, and
  support data without collecting prompt content by default.
- Run an incident tabletop exercise and an access/offboarding review.
- Complete an independent penetration test and remediate release-blocking findings.

GA exit gate:

- Pilot service objectives and adoption targets are met for an agreed observation period.
- Security, privacy, legal, operations, and product owners sign off on the supported use cases.
- On-call ownership, escalation paths, vulnerability response, backup/restore, and disaster
  recovery are exercised, not merely documented.
- Known limitations and unsupported use cases are published.

## Release Gates And Measures

The status, owner, and proof for each exit item are tracked in the
[Enterprise Readiness Evidence Register](enterprise-evidence.md).

The team should choose numeric targets during Phase 0. At minimum, each candidate release must
report:

| Measure | Required evidence |
| --- | --- |
| Security | Open findings by severity, threat-model changes, isolation/adversarial results |
| Authorization | Deny/ask/allow coverage, approval bypass tests, policy explanation tests |
| Tenant isolation | Cross-tenant negative tests for every shared backend and governed-data path |
| Audit | Event completeness, delivery success, backlog age, tamper/immutability verification |
| Reliability | End-to-end success rate, provider/tool latency, timeout and recovery results |
| Cost control | Token/cost accounting coverage and budget-enforcement tests |
| Quality | Unit, integration, end-to-end, upgrade, rollback, and supported-platform results |
| Supply chain | SBOM, vulnerability scan, signature, provenance, and clean-install verification |
| Operations | Runbook exercise results, backup restore test, incident and rollback drills |

## Immediate Priorities

The concrete near-term batch plan is tracked in
[Enterprise Next Batches](enterprise-next-batches.md).

The next implementation sequence should be:

1. Complete Phase 0 ownership, threat modeling, data classification, and reference-deployment
   decisions.
2. Build the identity context and interactive approval flow together so approvals are
   attributable from their first release.
3. Add normalized resource scopes to permission decisions before expanding mutating tools.
4. Implement the external audit-sink interface and versioned event schema early; later phases
   depend on trustworthy evidence.
5. Establish the Postgres-based end-to-end enterprise test path, then extend the same contract
   to Polaris/Iceberg.
6. Add sandbox profiles and managed data-protection integration before broadening the pilot.

## Deferred Until After The First Enterprise Release

- A graphical administration console.
- Fully autonomous shared-memory writes or governed-data mutations.
- Supporting every identity provider, model gateway, SIEM, and secrets platform at launch.
- Formal compliance certification before the underlying controls and evidence are mature.
- Multi-region active-active operation unless the reference deployment requires it.

These may become valuable, but none should displace identity, isolation, auditability,
operability, and end-to-end proof from the critical path.
