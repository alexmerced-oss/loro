# General Availability Readiness

This is the checklist between Loro today (0.22, a pre-1.0 release for controlled evaluation) and
an enterprise general-availability 1.0. It separates what the repository already proves from what
only people outside it can supply. Nothing on this page claims a gate is met without the evidence
named next to it.

Status values: **Met** (evidence in this repository or its CI), **Partial** (some evidence;
the gap is named), **Not met** (work remains in the repository), **External** (only an adopting
organization, an independent party, or the project owner can produce the evidence).

## 1. Product and engineering gates

| Gate | Status | Evidence or gap |
| --- | --- | --- |
| Stable boundary written down and machine-readable | Met | [support-matrix.json](support-matrix.json), [Project Status](project-status.md), frozen [release contract](release-contract.json) checked in CI |
| Every supported surface has written promotion gates | Met | [Promotion Gates (0.22)](project-status.md#promotion-gates-022) |
| CLI surface pinned against accidental change | Met | Golden `--help` and `--json` shape snapshots (`tests/test_cli_golden.py`) |
| Test suite, coverage floor, type checking in CI | Met | Python 3.11 to 3.14 matrix, coverage floor 70% (about 77% measured), mypy with a shrinking ignore list |
| Verified identity | Partial | OIDC/JWT verification for CLI, Web UI and gateways ([Identity](identity.md)); experimental, not yet proven against a production identity provider |
| Multi-user authorization | Partial | RBAC and a Postgres approval authority ([Multi-User Server Mode](multi-user.md)); browser sessions are per process and conversations are not partitioned per user |
| OS-enforced sandbox on every supported platform | Partial | Bubblewrap on Linux, container backend with optional gVisor ([Sandbox](sandbox.md)); the process backend remains the default and is not a security boundary; no macOS or Windows enforcement evidence |
| Tamper-evident audit with SIEM export | Met (repository side) | Hash-chained JSONL, per-run evidence bundles, OCSF/CEF forwarding; immutable retention is External |
| Upgrade and migration paths tested | Partial | Config, session, Web UI database and AAIS state migrations have tests; no long-running upgrade soak across several releases |
| 1.0 compatibility and deprecation policy | Not met | [Compatibility](compatibility.md) defines the mechanism; the 1.0 promise (which interfaces, for how long) is not yet written |
| Release engineering | Partial | Signed tags, attestations, SBOM and checksums are wired ([Release](release.md)); code signing for native artifacts does not apply; publishing requires the owner |

## 2. Security assurance

| Gate | Status | Evidence or gap |
| --- | --- | --- |
| Threat model reviewed for the current release | Partial | [Threat Model](threat-model.md) was last scoped to 0.17; OIDC, RBAC, plugins, web fetch and the container backend need new entries and a review |
| Independent penetration test | External | Follow [Independent Assurance Playbook](assurance-playbook.md); record findings, fixes and retests |
| Sandbox escape testing by an independent party | External | Required for Bubblewrap and the container backend ([External Requirements](external-enterprise-requirements.md)) |
| Dependency, static and secret scanning | Met | Security Evidence workflow (pip-audit, Bandit, secret baseline, license check, SBOM) |
| Vulnerability disclosure process | Met | [SECURITY.md](../SECURITY.md) |

## 3. Pilot

| Gate | Status | Evidence or gap |
| --- | --- | --- |
| At least one adopting organization runs a restricted pilot | External | [Pilot Charter](pilot-charter.md) template; no pilot has been recorded |
| Pilot success measures met and signed off | External | Completion and error rates, latency, cost, support volume, audit delivery, recovery targets |
| Organization-owned controls exercised | External | Every row of [External Enterprise Requirements](external-enterprise-requirements.md): identity provider, managed policy distribution, DLP, Postgres, Polaris, SIEM, model gateway, budgets, operations drills |

## 4. Support

| Gate | Status | Evidence or gap |
| --- | --- | --- |
| Named support owner and response targets | External | [Support Policy](support-policy.md) is best effort today; GA needs a named owner, severity levels and response times |
| Security response on-call | External | Owner and rotation for `SECURITY.md` reports |
| Operator runbooks | Partial | [Operator Runbook](operator-runbook.md), [Recovery](recovery.md); needs tabletop exercises recorded by an operator |

## What the repository can still do before 1.0

1. Update the threat model for the 0.22 surfaces and review it.
2. Write the 1.0 compatibility promise and deprecation windows.
3. Persist Web UI sessions in the shared database and partition conversations per user.
4. Make an OS-enforced sandbox the default where one is available, with a clear refusal otherwise.
5. Add long-running upgrade tests across the last few releases.

## What only others can do

Penetration testing, sandbox escape testing, a pilot with named users, the organization-owned
controls in [External Enterprise Requirements](external-enterprise-requirements.md), and a named
support commitment. Record each with the evidence fields listed in that register and link it from
the [Enterprise Evidence Register](enterprise-evidence.md).
