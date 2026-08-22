# Project Status

## Assessment

Loro `0.15.1` is an **experimental feature release built on the release-quality 0.10
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

The 0.15.1 feature release adds the optional local Web UI with durable multi-conversation chat,
profile-backed bots, governed profile and default-setting editors, streamed runtime events,
approval handling, cancellation, and authenticated non-loopback operation. It reuses the same
AgentRuntime, profile resolution, managed-policy narrowing, and configuration overlays as the
CLI. The Web UI remains experimental and does not expand the 0.10 stable boundary. Its release
validation run passed 624 tests with 4 environment-dependent skips and 75.58% repository
branch-aware coverage.

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
