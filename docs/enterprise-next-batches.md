# Enterprise Next Batches

This document translates the Enterprise Readiness Roadmap into near-term implementation
batches. It is intentionally concrete so future work can resume without re-reading the full
roadmap every time.

## Planning Snapshot

As of August 2026, Loro has a healthy alpha/MVP foundation: model runtime, tools, provider
adapters, setup wizards, local/shared memory, Polaris discovery, audit JSONL, CI, integration
test scaffolding, and PyPI releases are in place.

The enterprise gap is no longer basic capability. The gap is proof and control:

- Who is acting?
- Which policy allowed the action?
- Can a model approve its own dangerous request?
- Are resource scopes normalized below the prompt layer?
- Can operators reconstruct and trust every consequential event?
- Can the reference deployment be reproduced and tested end to end?

## Batch 1: Phase 0 Evidence Pack

Goal: make the hardening program explicit, owned, and testable before adding more enterprise
control code.

Deliverables:

- Add `docs/threat-model.md` covering prompt injection, tool misuse, credential exposure,
  tenant crossover, audit tampering, dependency compromise, provider leakage, poisoned memory,
  unsafe artifacts, and governed-data misuse.
- Add `docs/data-classification.md` defining what data may enter prompts, local memory, shared
  memory, artifacts, sessions, audit logs, provider requests, and governed-data summaries.
- Add `docs/reference-deployment.md` describing the first supported enterprise deployment:
  centrally managed CLI, internal or approved model gateway, managed policy overlay, Postgres
  shared memory, Polaris-governed Iceberg as scale-out, and durable external audit sink.
- Add `docs/enterprise-evidence.md` as the checklist that links each roadmap exit item to
  tests, docs, owners, and release evidence.
- Update `docs/enterprise-readiness-roadmap.md` with links to these documents.

Acceptance criteria:

- The supported pilot shape is unambiguous.
- Every later phase has evidence placeholders.
- The docs identify assumptions and open decisions instead of pretending unknowns are solved.
- No runtime behavior changes are required for this batch.

Status: repository deliverables complete. The [threat model](threat-model.md),
[data classification policy](data-classification.md),
[reference deployment](reference-deployment.md), and
[evidence register](enterprise-evidence.md) are drafted and cross-linked. Phase 0 governance
remains open until an adopting organization assigns the documented owners, approves the data
classification mapping and threat model, pins infrastructure versions, and records deployment
reproduction and baseline results.

## Batch 2: Identity Context Foundation

Goal: make Loro internally identity-aware before approvals, memory commits, and audit sinks rely
on identity.

Deliverables:

- Add `loro.identity` with an `IdentityContext` model containing subject, display name,
  organization, tenant, groups, roles, auth method, session id, and source.
- Load identity from config and environment variables for the first implementation path.
- Add managed-overlay support for required identity fields and fail-closed behavior when
  configured.
- Add `loro identity show` and `loro identity doctor`.
- Add identity fields to runtime, memory, tool, and audit call sites without changing external
  sink behavior yet.
- Document identity configuration in `docs/identity.md`.

Acceptance criteria:

- Tests cover default local identity, env/config identity, required identity failures, and
  managed overlay precedence.
- Audit events for consequential actions include identity fields where available.
- Identity is never inferred from prompt text.

Status: foundation complete. `loro.identity` resolves local, configuration, and environment
identity; managed overlays can enforce required fields; CLI diagnostics and setup are available;
and audit, runtime, session, tool, and shared-memory paths carry identity. Corporate
authentication/assertion verification and identity-bound authorization remain Phase 1 work and
are explicitly documented in [Identity Context](identity.md).

## Batch 3: Approval Records And Interactive Prompts

Goal: replace approval-by-argument as the enterprise path with attributable, scoped approval
records.

Deliverables:

- Add `loro.approvals` with normalized approval requests, approval records, expiration, and
  replay protection.
- Add interactive approval prompts for file writes/replacements, shell commands, Git
  mutations, shared-memory draft commits, and governed-data actions.
- Show action, normalized target, command or diff preview, policy decision, risk reason, and
  identity before approval.
- Support approve once, deny, and scoped session approval.
- Keep non-interactive `--yes` and explicit runtime approval fields available for tests and
  automation, but mark them as non-enterprise modes unless a managed policy allows them.
- Audit approval request, grant, deny, expiration, and use events.

Acceptance criteria:

- Tests prove the model cannot approve its own request.
- Tests prove changed arguments invalidate a prior approval.
- Tests prove expired approvals and broad replay attempts fail.
- CLI commands remain usable in non-interactive CI/test paths.

Status: complete for the current tool surface. Identity-bound approval requests and records,
canonical argument digests, once/session scope, expiry, replay protection, interactive prompts,
managed non-interactive controls, and lifecycle audit events cover runtime file/shell/Git/Polaris,
direct shell, shared-memory commit, and governed-data discovery paths. Durable approval storage,
policy-version binding, and full resource normalization remain explicitly deferred to later
batches. See [Approvals](approvals.md).

## Batch 4: Normalized Resource Scopes

Goal: evaluate permissions against normalized resources rather than loose strings.

Deliverables:

- Add resource-scope models for filesystem paths, shell commands, Git operations, memory
  tenants/scopes, Polaris catalogs/namespaces/tables, and provider actions.
- Resolve filesystem paths symlink-safely against configured workspace roots.
- Normalize shell executable and arguments before policy matching.
- Extend permission rules to target normalized resource fields while preserving simple glob
  rules for backward compatibility.
- Add `loro policy explain` for a request fixture or command preview.

Acceptance criteria:

- Path traversal, symlink, case, relative path, and shell encoding bypass tests fail closed.
- Existing permission tests still pass.
- Policy explanations show the matched rule, normalized resource, and policy source.

Status: complete for the current tool surface. `loro.resources` normalizes filesystem, shell,
Git, memory, Polaris, and provider scopes; configured workspace roots reject traversal and
symlink escape; structured field rules preserve legacy target globs; policy results expose
version/source/matched rule; approvals bind policy version/source; and `loro policy explain`
renders request-fixture decisions. Sandbox enforcement, signed policy artifacts, TOCTOU
containment, and storage-level tenant controls remain later-phase work. See
[Normalized Resource Policy](policy.md).

## Batch 5: Versioned Audit Schema And External Sink Interface

Goal: make audit evidence durable and ready for enterprise operations.

Deliverables:

- Define a versioned audit event schema with event id, timestamp, actor, tenant, session,
  trace, action, target, policy decision, approval, result, and redaction metadata.
- Add `loro.audit.sinks` with JSONL and HTTP sink implementations.
- Add retry/backoff, bounded local buffering, delivery status, and visible failure behavior.
- Add `loro audit doctor` and `loro audit flush`.
- Keep local JSONL as the default development sink.

Acceptance criteria:

- Tests verify no audited consequential action is silently dropped when an external sink fails.
- Audit events include schema version and identity fields.
- External sink failures have deterministic CLI/runtime stop or warning behavior based on
  policy.

Status: complete for the reference sink surface. Audit schema `1.0` promotes identity, session,
trace, action/target, policy, approval, result, and redaction metadata while retaining legacy
details. `loro.audit.sinks` provides JSONL and bearer-authenticated HTTP delivery; HTTP retries
with exponential backoff and retains failures in a bounded local buffer. Warning and fail-closed
modes are deterministic, buffer exhaustion always raises, and `loro audit doctor`/`flush`
provide operational visibility and recovery. Failure-injection tests cover retry, buffering,
full-buffer behavior, flush, and runtime fail-closed behavior. Destination immutability,
cross-process buffer locking, batching, mTLS/signing, production collector evidence, and strict
event-family completeness remain later operations/security work. See
[Audit Events And Delivery](audit.md).

## Batch 6: Subprocess Sandbox Foundation

Goal: establish enforceable process boundaries before expanding enterprise tool reach.

Status: repository implementation complete for current subprocess families. Named profiles govern
direct shell, Git, Polaris, MCP stdio, and Agent Skill scripts with canonical executable/cwd
checks, allowlist-built environments, timeout/output ceilings where Loro owns the pipes,
writable-root policy, diagnostics, and optional fail-closed Bubblewrap filesystem/network
isolation. MCP responses retain their own bounded SDK/result path, and an `execve` scrubber
removes environment defaults restored by the official SDK. Adversarial and interoperability tests
cover secret inheritance, executable and cwd denial, timeout, output exhaustion, explicit MCP
environment, and required-enforcement failure. Production escape tests and other Phase 2
data-protection controls remain open. See
[Subprocess Sandbox Profiles](sandbox.md).

## Batch 7: Managed Data Protection

Goal: apply one classification, scanning, redaction, and blocking contract at every supported
content boundary.

Status: repository implementation complete for supported application flows.
`loro.data_protection` defines pluggable scanners and surface decisions; managed overlays control
classification ceilings, actions, per-surface finding allowlists, custom patterns, and whether the
development sensitive-content override exists. The runtime checks composed provider input and
transforms model/tool output;
memory, artifacts, sessions, cross-session messages, provenance, and recursive audit details are
covered at their application boundaries. Negative tests cover restricted input, redaction,
overlapping findings, classification labels, scanner injection, and managed override lockout.
Corporate scanner/gateway integration, provider-specific approval, production policy evidence,
and Restricted-data workflows remain external or later gates. See
[Managed Data Protection](data-protection.md).

## Batch 8: Shared-Memory Tenant Isolation

Goal: make tenant scope a trusted identity property below CLI and prompt arguments.

Status: repository implementation complete for the supported adapters. Managed `identity` mode
binds search, SQL rendering, commits, and local draft visibility to the resolved identity tenant.
Postgres schema generation adds forced row-level security and connections set transaction-local
tenant context before reads/writes. Iceberg pushes tenant and active-status filtering into scans
and retains a defensive record check. CLI and tool paths reject mismatches before approval or
backend access, and negative tests cover operation, adapter, tool, and draft boundaries.
Production least-privilege database roles, live RLS tests, and Polaris catalog authorization proof
remain evidence gates.

## Recommended Next Work

Batches 1 through 8 are implemented in the repository for the current application surface. The
next work should prioritize memory retention, correction, deletion, legal-hold, and provenance
behavior before broader pilot use. Production database isolation, Bubblewrap, corporate
DLP/provider approvals, and protected MCP conformance artifacts remain evidence gates.
