# Project Status

## Assessment

Loro `0.16.0` is an **experimental feature release built on the release-quality 0.10
stabilization baseline for controlled evaluation**. The deliberately limited stable core remains
unchanged; Open Agent Profile support is experimental. Loro is not yet unrestricted enterprise
general availability because several production and organization-owned controls require evidence
that cannot be created by repository tests.

## Stable Boundary

The versioned [support matrix](support-matrix.json) is authoritative. Its supported core covers:

- Linux on Python 3.11 through 3.14;
- local and Postgres memory, with an explicit user-authorized shared-memory commit flow;
- OpenAI-compatible, Anthropic, and Gemini provider protocols;
- Agent Skills plus reviewed Claude and Pi Skill import;
- governed coding tools and document, presentation, spreadsheet, and brief artifacts.

Iceberg/Polaris, MCP, Agentic Graphs, Bedrock, the local Web UI, and remote chat gateways remain experimental in
the 0.10 compatibility promise. Their implementations, policy controls, tests, and documentation
are available for controlled qualification, but they are not silently promoted into the stable
surface.

## Verified Release State

The 0.10.0 stabilization source tag is signed and its wheel and source distribution are published to PyPI and
GitHub Releases with checksums, SBOM, provenance, attestations, and a frozen release contract.
Protected CI covers Python 3.11-3.14, security evidence, MCP and Agentic Graph conformance,
Postgres recovery, Polaris/Iceberg quickstart integration, and content-free benchmarks. The
release validation suite passed 547 tests with 5 environment-dependent skips and 74.31% branch
coverage.

The 0.12.0 release completes the repository-defined provisional OAP Level 3 harness without
changing that stable boundary. Its pre-release validation run passed 580 tests with 5
environment-dependent skips, 75.04% repository branch-aware coverage, and 89.13%
profile-package coverage. Formal upstream OAP certification remains pending and is explicitly
provisional in the machine-readable conformance statement.

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
human gates for an explicit decision, and streams node transitions from a replayable cursor.

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
