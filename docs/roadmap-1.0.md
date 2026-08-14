# Loro Roadmap To 1.0

## Purpose

This is the single authoritative roadmap for work remaining after Loro `0.10.0`. It covers the
experimental `0.11.0` Open Agent Profile release and the first stable `1.0` release. Completed
milestones belong in release notes; implementation proof belongs in the
[Enterprise Evidence Register](enterprise-evidence.md).

No calendar date is promised. A milestone ships when its repository-owned work is complete and
its required controlled-environment or adopting-organization evidence is linked. Patch releases
may ship between these milestones.

## Current Baseline

Loro `0.10.0`, released August 11, 2026, is the 1.0 stabilization baseline. In addition to the
agent, memory, governance, provider, MCP, Skill, graph, gateway, artifact, deployment, benchmark,
and release controls delivered through `0.8`, the repository now provides:

- a frozen machine-readable snapshot of the CLI, schemas, protocols, support classifications,
  matrices, managed policy, compose stack, and reference deployment;
- a content-free installed-environment readiness report with strict warning enforcement;
- CI rejection of unreviewed candidate-contract drift;
- pilot defect severity/disposition, independent assurance, consumer verification, support,
  vulnerability, upgrade, rollback, incident, and offboarding procedures;
- a truly fail-closed managed identity requirement that cannot be satisfied by local fallbacks.
- strict-ready provider setup, transactional audited configuration, digest-bound artifact
  provenance, and a signed-tag verification path.

The [0.10 release notes](releases/0.10.0.md), [release contract](release-contract.json),
[support matrix](support-matrix.json),
[interoperability matrix](interoperability-matrix.json), and
[data support matrix](data-support-matrix.json) describe the exact shipped boundary.

## The 1.0 Promise

Loro `1.0` will be a stable, enterprise-governed CLI harness whose supported reference
deployment can:

- run approved coding and productivity tasks through governed AI routes;
- keep provider credentials isolated from tools and subprocesses;
- maintain local memory while requiring an explicit user decision for every shared-memory write;
- use tenant-scoped Postgres shared memory and supported Agent Skills without bypassing Loro
  policy;
- attribute consequential actions to identity, tenant, policy, approval, session, and target;
- survive documented provider, database, audit, and process failures without silent data loss or
  security downgrade;
- provide administrators, operators, reviewers, plugin authors, gateway owners, and users with a
  reproducible deployment and support contract;
- produce verifiable operational, security, compatibility, and release evidence.

The stable promise applies only to the frozen `1.0` support matrix. Integrations without enough
proof remain experimental or are excluded rather than weakening the release gate. For the 0.10
stabilization line, Iceberg/Polaris, MCP, Agentic Graphs, Bedrock, and remote gateways are outside
the stable compatibility promise even though their policy-governed implementations remain
available for evaluation.

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
| `0.8` | Released | A reproducible reference deployment is measurable and operable for a restricted beta. |
| `0.9` | Released | The candidate boundary is frozen and published for controlled pilot and assurance. |
| `0.10` | Released | Repository hardening, release signing, and the deliberately small stable core are frozen. |
| `0.11` | Next | Experimental OAP v1 Level 2 named-agent profiles ship without expanding the stable core. |
| `1.0` | Planned | Approved stable contracts, ownership, evidence, and public release artifacts are complete. |

## 0.11: Open Agent Profile

Loro `0.11.0` adds portable, named agent profiles as an experimental surface. The target is Open
Agent Profile v1 conformance Level 2: document loading, discovery and trust, fail-closed privilege
narrowing, runtime instantiation, untrusted state injection, and approval-gated state writeback.
Level 3 composition, profile-selected MCP/Skills, subagents, external profile memory stores, and
Agentic Graph integration remain deferred.

The detailed, batch-by-batch scope and release gates are in the
[0.11.0 Release Plan](releases/0.11.0-plan.md). The source guide remains the
[OAP Implementation Guide](oap-implementation-plan.md).

### 0.11 Exit Gate

- The exact OAP v1 source revision, schemas, fixtures, reference digests, conformance tests, and
  license are pinned and recorded before any conformance claim.
- Profiles can narrow but never widen model, tool, permission, path, host, budget, sandbox, data,
  identity, tenant, or writeback policy.
- Profile state is always labeled untrusted, data-protected before injection and persistence, and
  unable to carry approval authority.
- Delta application is revision-checked, digest-bound, `/state`-only, locked, durable, atomic, and
  recoverable; capability proposals never auto-apply.
- The no-profile runtime and existing 0.10 stable surface remain behaviorally compatible.
- Python 3.11-3.14 CI, security evidence, OAP fixture/conformance jobs, package verification, and
  clean-install smoke tests pass for the exact release artifacts.
- Support and interoperability matrices classify OAP as experimental and publish precise Level 2
  limitations.

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

- All `0.10` exit evidence remains valid for the final commit and artifacts.
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
