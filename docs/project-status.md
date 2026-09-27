# Project Status

## Assessment

Loro `0.21.0` is an **experimental feature release built on the release-quality 0.10
stabilization baseline for controlled evaluation**. The deliberately limited stable core remains
unchanged. Open Agent Profile and Agentic Graph support are aligned with their published 1.0
specifications and compatible 1.x support libraries, while remaining experimental in Loro's product
support matrix. Loro is not yet unrestricted enterprise general availability because several
production and organization-owned controls require evidence that cannot be created by repository
tests.

## Stable Boundary

The versioned [support matrix](support-matrix.json) is authoritative. Its supported core covers:

- Linux on Python 3.11 through 3.14;
- local and Postgres memory, with an explicit user-authorized shared-memory commit flow;
- OpenAI-compatible, Anthropic, and Gemini provider protocols;
- Agent Skills plus reviewed Claude and Pi Skill import;
- governed coding tools and document, presentation, spreadsheet, and brief artifacts.

Iceberg/Polaris, the MCP client, Agentic Graphs, Bedrock, OIDC sign-in, non-loopback and
multi-user Web UI use, and remote chat gateways remain experimental. From 0.22 the local Web UI
(loopback, launch token, single user) and Loro's MCP server at protocol revision 2025-11-25 are
supported; the gates that justified each promotion, and the ones that keep the rest experimental,
are in [Promotion Gates](#promotion-gates-022). Their implementations, policy controls, tests, and documentation
are available for controlled qualification, but they are not silently promoted into the stable
surface.

## Verified Release State

The 0.10.0 stabilization source tag is signed and its wheel and source distribution are published to PyPI and
GitHub Releases with checksums, SBOM, provenance, attestations, and a frozen release contract.
Protected CI covers Python 3.11-3.14, security evidence, MCP and Agentic Graph conformance,
Postgres recovery, Polaris/Iceberg quickstart integration, and content-free benchmarks. The
release validation suite passed 547 tests with 5 environment-dependent skips and 74.31% branch
coverage.

The 0.12.0 release completed the repository-defined provisional OAP Level 3 harness without
changing that stable boundary. Its historical pre-release validation run passed 580 tests with 5
environment-dependent skips, 75.04% repository branch-aware coverage, and 89.13%
profile-package coverage. Release 0.17.0 supersedes that provisional conformance description.

The 0.13.0 feature release adds selectable provider/model setup, a durable folder REPL, and
model-drafted document, presentation, spreadsheet, and brief commands. These additions preserve
the 0.10 stable boundary and the provisional OAP Level 3 status. Its release validation run passed
586 tests with 5 environment-dependent skips and 75.07% repository branch-aware coverage.

The 0.14.0 feature release completes those workflows with provider-wide model discovery, a
permission-oriented profile wizard, streaming REPL tool activity, fail-closed authored artifacts,
AI-compiled executable graphs, and the context-aware `get-started` command. It preserves the same
stable boundary and provisional OAP classification. Its pre-release validation run passed 614
tests with 5 environment-dependent skips and 75.50% repository branch-aware coverage.

The 0.15.2 feature release adds the optional local Web UI with durable multi-conversation chat,
profile-backed bots, governed profile and default-setting editors, streamed runtime events,
approval handling, cancellation, and authenticated non-loopback operation. It reuses the same
AgentRuntime, profile resolution, managed-policy narrowing, and configuration overlays as the
CLI. The Web UI remains experimental and does not expand the 0.10 stable boundary. Its release
validation run passed 624 tests with 4 environment-dependent skips and 75.58% repository
branch-aware coverage.

The 0.16.0 feature release completes that Web UI. Assistant markdown is now rendered rather than
shown as literal `**` markers and backticks, without a raw-HTML pass, so untrusted model output
cannot introduce elements or `javascript:` links. Loopback binding is token-gated per launch,
matching MagAgent, which closes the API to other local processes and other users on a shared
machine. The committed frontend bundle is verified in CI: the `Web UI` workflow reinstalls from the
lockfile, runs the unit tests, rebuilds, and fails when the shipped assets no longer match their
source, which previously could ship a UI older than its own code. Frontend dependencies moved from
`latest` to exact pins so the bundle is reproducible. Its release validation run passed 720 tests
with 3 environment-dependent skips, alongside 29 frontend tests.

The Web UI also gained a Graphs view, closing the largest gap between the CLI and the browser: Loro
implements AGS conformance level 3, and none of that runtime was previously reachable from its own
interface. It discovers, validates, plans, and runs graphs through the same governed executor, holds
human gates for an explicit decision, streams node transitions from a replayable cursor, and lets
operators edit card instructions, dependencies, profiles, declared tools, and portable permission
requirements before the validated graph is saved or run.

A Governance view now exposes the evidence surface in the browser: resolved identity, budgets,
sandbox posture and approval mode; policy explanation for a hypothetical request; and the audit
record with hash-chain verification and filtering by event type. It is read-only throughout and
cannot grant authority or mutate state.

Two gaps in that streaming were closed. The Graphs view claimed a dropped connection resumed from
the cursor, but the client requested the whole log every time, never tracked its position, and
cleared the run on any error; a lost connection therefore blanked the board while the run carried
on. It now resumes from the last event it saw, and a reloaded page finds a run still in progress
through a new endpoint that lists live handles, as distinct from the persisted history a finished
record lands in.

A first-run panel replaces the workspace when a folder cannot run a turn. Loro is configured per
folder and the browser assumed that had already happened, so a fresh project produced a composer
whose first message failed with a provider error and no explanation. The panel reports the same
readiness `loro get-started` does and can select a provider and model, but never accepts a
credential: keys stay in the environment or the OS keyring, and it reports only whether one was
found and which variable it expects.

A Memory view exposes the last subsystem the browser could not see. Local memories, the proposal
queue, and governed shared memory were all terminal-only, so the memory shaping every reply was
invisible from the interface that displayed those replies. Accepting a proposal writes a local
memory or stages a shared draft exactly as the CLI does, and declining one is newly possible at all:
the CLI only accepts, so the queue could previously only grow. Both decisions are audited.

Two accessibility audit passes run against a live server, because the properties that matter are
computed styles against real backgrounds rather than anything a unit test can assert. The first
covers contrast in both themes, accessible names, heading structure, pointer-target size, clipped
text, and horizontal overflow; the second covers the tab ring, focus indication,
`prefers-reduced-motion`, 200% zoom, and narrow viewports. Both are clean across every view.

Release 0.17.0 aligned OAP and AGS with their canonical 1.0 schemas and 1.0.1 Python
support libraries. CI pins OAP commit `7fb633a1a59dd7636ffb0030d254f2f58934f74a` and AGS commit
`f180a4dbd07911f90dd0821f531d7ccd51bb0764`. Loro claims OAP Level 3 and AGS Level 3 in
[machine-readable OAP evidence](oap-conformance.json) and
[machine-readable AGS evidence](ags-conformance.json). Profiles, exports, state updates, graph
digests, and run records now use the canonical formats; older Loro profile encodings remain
readable at the storage boundary. Final release evidence must be generated from the tagged commit.

Release 0.20.0 retains the AAIS 1.0 authority/presenter boundary for chats, delegated agents, and graph
tools. Protected actions are persisted before presentation, resolved by digest-bound decisions,
and available through the Web UI or the documented bidirectional standard-I/O transport without
falling back to a hidden terminal prompt. This remains an experimental surface and does not expand
the 0.10 stable compatibility boundary.

GitHub main-branch and release-tag rulesets, required checks, secret scanning with push
protection, and Dependabot security updates are active. Non-provider secret scanning and
validity checks are unavailable under the repository's current GitHub plan, so repository-owned
security scans remain part of the release evidence.

## Remaining 1.0 Gates

General availability requires adopting-organization evidence for corporate identity and managed
policy trust, production sandbox/DLP controls, least-privilege data infrastructure, immutable
audit retention, approved provider and chat applications, recovery and incident exercises,
independent penetration testing, pilot closure, named support/on-call ownership, and formal
security, privacy, legal, data, operations, product, and release approval.

The [Roadmap To 1.0](roadmap-1.0.md) is the sole forward roadmap. The
[External Enterprise Requirements](external-enterprise-requirements.md) and
[Enterprise Evidence Register](enterprise-evidence.md) define the evidence needed to promote
the stabilization baseline without overstating readiness.

## Promotion Gates (0.22)

A surface moves from experimental to supported only when every gate below is met with evidence in
this repository or its CI. Gates that need evidence Loro cannot produce itself (a penetration test,
a protected deployment, a pilot organization) keep a surface experimental until that evidence is
linked.

### Local Web UI: loopback, launch token, single user (promoted to supported)

| Gate | Status | Evidence |
| --- | --- | --- |
| Every API route family has automated tests, including authentication, CSRF and origin checks | Met | `tests/test_webui*.py` (about 120 tests) |
| Frontend type-checks and unit tests pass, and the committed bundle matches its source | Met | `.github/workflows/webui.yml` (vitest, rebuild, stale-bundle check, start-up smoke test) |
| Stored conversations survive upgrades | Met | Versioned SQLite schema with migration tests (`test_migration_preserves_existing_conversations`) |
| Every view reviewed at 1440 and 375 px in light and dark themes with no horizontal overflow | Met for the 0.22 branch | Automated overflow audit plus reviewed screenshots of all ten views; fixes in this release: composer pushed off-screen in long chats, 375 px overflow, unstyled Extensions and Run center controls |
| Keyboard access, focus and contrast audits | Met | Two audit passes recorded in [Local Web UI](webui.md#accessibility); new sign-in controls have visible focus |
| It uses the same runtime, policy, approvals and audit as the CLI | Met | Web UI runs through `AgentRuntime`; approvals through the AAIS store |

Scope of the promise: `loro web` on a loopback address with the per-launch token, one user. The
browser UI, its URL, and stored conversations are covered; the `/api/*` routes are the UI's
private interface and may change between minor releases.

### Web UI OIDC sign-in, non-loopback binding, multi-user use (still experimental)

| Gate | Status |
| --- | --- |
| Verified identity for every request | Met in 0.22 (`loro web --auth oidc`), new in this release, so not yet proven in use |
| Role-based access control per user | Met in 0.22 (experimental; [Multi-User Server Mode](multi-user.md)) |
| Shared, durable approval storage for more than one server process | Met in 0.22 with `approvals.authority = "postgres"`; browser sessions are still per process |
| Independent security review or penetration test of the remote configuration | Not met (external) |
| Deployment evidence behind a TLS proxy with a production identity provider | Not met (external) |

### MCP server, protocol revision 2025-11-25 (promoted to supported)

| Gate | Status | Evidence |
| --- | --- | --- |
| Official conformance scenarios for every advertised server capability pass | Met | `MCP Conformance` workflow: `@modelcontextprotocol/conformance` 0.1.16 server scenarios, green on the v0.21.0 tag and weekly on `main`; must be green again on the 0.22.0 tag |
| Official SDK interoperability tests pass | Met | `tests/test_mcp_sdk.py`, `tests/test_mcp_server.py` |
| Least privilege: only explicitly exported read-only tools, deny by default | Met | `mcp.server.export_tools` ceiling tests; [MCP](mcp.md) |
| DNS-rebinding protection on Streamable HTTP | Met | `dns-rebinding-protection` conformance scenario |

### MCP client, and protocol revision 2026-07-28 (still experimental)

| Gate | Status |
| --- | --- |
| Official conformance scenarios for 2026-07-28 | Not met: the published runner has no 2026-07-28 scenarios yet |
| stdio servers run under an OS-enforced sandbox by default | Not met: the default `mcp-stdio` profile uses the process backend |
| Protected deployment evidence with real third-party servers | Not met (external) |
