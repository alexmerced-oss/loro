# Development Roadmap

## Done In Scaffold

- Typer CLI
- Config layering
- Local memory
- Sessions
- JSONL audit
- Artifact generation and provenance
- File and shell tools
- Read-only Polaris wrapper validation
- Safety scanning before memory and artifact writes
- Provider profiles and local configuration wizard
- Shared memory schema and draft workflow
- Postgres shared memory SQL adapter, schema apply command, and backend check
- Iceberg shared memory SQL adapter
- Polaris typed catalog, namespace, table, and view commands
- Polaris typed role, privilege, policy, and applicable-policy commands
- Explicit typed runtime tool loop for file read/search
- Legacy glob and structured normalized-resource permission policy rules
- Bounded model-directed runtime loop with provider-neutral JSON tool directives
- Runtime tools for shell execution, local memory search, and read-only Polaris passthrough
- Runtime artifact generation tool with provenance sidecars
- Runtime edit tools for approved file writes and replacements
- Runtime Git helpers for status, diff, show, approved add, and approved commit
- Backend-neutral shared memory search result type
- Postgres shared-memory search execution and SQL dry-run fallback
- Shared-memory runtime recall with citations
- Local/shared memory proposal records and accept workflow
- Iceberg readiness checks with optional PyIceberg detection
- Higher-level governed data schema and access explanation commands
- Local Polaris/Iceberg testing guide and optional Polaris CLI integration test
- Streaming model client interface with fallback behavior
- Provider smoke command with explicit execution opt-in
- Normalized provider error handling
- Optional AWS Bedrock adapter guardrails
- Native provider tool-call parsing for OpenAI-compatible, Anthropic, Gemini, and Bedrock
- Live Iceberg shared-memory search and explicit draft commit execution through PyIceberg
- GitHub Actions CI for lint, coverage, tests, and compile checks
- Manual integration workflow for Postgres and Polaris CLI tests
- Release checklist documentation

## Current Status

The five MVP batches below are complete for the 0.3.0 alpha surface. Interactive approval prompts
and identity-bound, single-use approval records cover ask-gated write, shell, Git mutation,
shared-memory, MCP, and governed-data actions. Remaining work is enterprise deployment evidence,
external control integration, and defects found during controlled pilots rather than missing MVP
command scaffolding.

Managed, non-overridable enterprise configuration overlays are implemented and documented.
Typed identity context, environment/config resolution, managed required fields, diagnostics,
audit/session propagation, and shared-memory defaults are also implemented and documented.
The remaining hardening program is tracked in the
[Enterprise Readiness Roadmap](enterprise-readiness-roadmap.md).

## Prioritized Work Batches

### Batch 1: Agent Loop Core

Goal: make `loro run` and `loro plan` behave like an actual model-directed
agent loop instead of a one-shot scaffold.

Acceptance criteria:

- Runtime supports iterative model response -> tool call -> tool execution -> model response.
- Loop has max-step protection and a clear stop reason.
- Tool calls use a provider-neutral textual JSON directive while native provider tool-calling
  is still being built.
- Tool executions are audited and included in saved sessions.
- Existing explicit `@tool ...` prompt directives continue to work for deterministic testing.

Status: core loop complete. Runtime has file read/search, local memory search,
permission-gated shell execution, read-only Polaris passthrough, artifact generation, approved
file write/replace, and Git status/diff/show/add/commit helpers. Interactive TUI approval
prompts and identity-bound approval records now cover ask-gated actions. Explicit user-authored
approval arguments remain a managed, non-interactive compatibility path; model-originated
approval arguments are not trusted.

### Batch 2: Shared Memory Retrieval And Governance

Goal: bring Loro's enterprise memory differentiator into the runtime context path.

Acceptance criteria:

- Add shared memory search commands and a backend-neutral search result type.
- Add Postgres shared-memory retrieval and dry-run SQL rendering.
- Wire shared memory recall into `AgentRuntime` with citations and provenance.
- Add first-class local/shared memory proposal records without allowing autonomous shared writes.
- Document explicit-only shared-memory write guarantees.

Status: complete. Loro can search shared memory through a backend-neutral result shape,
execute Postgres shared-memory retrieval when configured, render Postgres/Iceberg search SQL
when execution is unavailable, recall cited shared memories in `AgentRuntime`, and manage
local/shared memory proposals. Accepting a shared proposal creates a draft only; explicit
draft commit remains required.

### Batch 3: Polaris And Iceberg Local Integration

Goal: turn governed data from CLI wrapper scaffolding into a repeatable local integration path.

Acceptance criteria:

- Add a local Polaris/Iceberg testing guide and compose or fixture scaffolding.
- Add Iceberg REST/PyIceberg readiness checks where available.
- Add higher-level `data schema` and `data explain-access` commands.
- Keep all Polaris passthrough operations constrained by read-only validation.

Status: complete. Loro has a local Polaris/Iceberg testing guide, optional Polaris CLI
integration test scaffolding, Iceberg backend readiness checks with optional PyIceberg
detection, `data schema`, and `data explain-access`. Iceberg shared-memory search and
explicit draft commits can execute through a configured PyIceberg catalog, which should point
at a Polaris-governed REST catalog or another enterprise-governed Iceberg catalog.

### Batch 4: Provider And Streaming Hardening

Goal: make provider integrations reliable enough for real agent sessions.

Acceptance criteria:

- Add streaming model response interface.
- Add real provider smoke-test command behind explicit user opt-in.
- Implement Bedrock behind optional AWS dependencies.
- Improve provider error messages for auth, rate limits, unsupported models, and malformed
  responses.
- Normalize tool-call response parsing across OpenAI-compatible, Anthropic, Gemini, and
  Bedrock providers.

Status: complete. Model clients expose `stream()`, `loro providers smoke` performs redacted
dry-runs by default and real calls only with `--execute`, provider/network/response errors are
normalized for CLI/runtime display, and Bedrock is available behind optional AWS SDK
dependencies. Native OpenAI-compatible `tool_calls`, Anthropic `tool_use`, Gemini
`functionCall`, and Bedrock `toolUse` payloads are normalized into runtime tool calls while
the textual `@tool` directive remains available as a compatibility path.

### Batch 5: Testing, CI, And Release Discipline

Goal: keep the expanding harness safe to change.

Acceptance criteria:

- Add GitHub Actions for ruff, pytest, compileall, and coverage.
- Establish a coverage baseline and threshold.
- Add optional integration jobs for Postgres, Polaris, Iceberg, and live model providers.
- Add release checklist docs for packaging, smoke tests, and provider validation.

Status: complete. Main CI installs dev dependencies, runs ruff, coverage-backed pytest, and
compileall. A manual integration workflow gates Postgres and Polaris CLI integration tests.
Coverage has an initial threshold in `pyproject.toml`, and `docs/release.md` captures release
verification, smoke checks, and packaging steps.

## Enterprise Hardening

- Identity and SSO/internal model gateway integration
- Durable approval storage and signed policy artifacts (configured versions are already bound)
- Production sandbox proof and corporate DLP/provider policy evidence (the repository-level
  sandbox and managed data-protection contracts are implemented)
- Audit destination immutability, stronger authentication, and production outage evidence
- Protected production-like end-to-end evidence for Postgres, Iceberg, and Polaris (scheduled
  ephemeral Postgres and Polaris service-smoke CI are implemented)
- Production operational runbooks and pilot validation (repository supply-chain automation is
  implemented)

See the [Enterprise Readiness Roadmap](enterprise-readiness-roadmap.md) for phases, acceptance
gates, evidence requirements, and immediate priorities.
The near-term execution plan is tracked in
[Enterprise Next Batches](enterprise-next-batches.md).

Slack, Discord, and Telegram channel gateways are not implemented in 0.3.0. A future gateway
batch must add authenticated adapters, channel/user-to-identity mapping, replay protection,
rate and payload bounds, explicit approval routing, untrusted-message labeling, durable delivery,
and audit coverage before any bot surface is advertised. See [Channel Gateways](channel-gateways.md).

## MCP And Agent Skills

MCP client, transport authorization, extensions, server mode, Agent Skills, and conformance
automation are implemented. The
[MCP And Agent Skills Roadmap](mcp-skills-roadmap.md) defines seven implementation batches for:

- MCP `2026-07-28` stateless client support and classic handshake-based compatibility through
  `2025-11-25`, with a `2024-11-05` compatibility target.
- stdio and Streamable HTTP transports, version negotiation, typed tools/resources/prompts,
  enterprise authorization, Tasks, subscriptions, and least-privilege MCP server mode.
- Open Agent Skills `SKILL.md` discovery, validation, progressive disclosure, provenance,
  sandboxed execution, and explicit install/publish review.
- Official conformance and dual-era interoperability evidence before support is advertised.

Cross-session messaging is also implemented as a durable local mailbox inspired by Claude
Code's `SendMessage`. Relayed content is untrusted, carries no user authority, cannot approve
tools, and is delivered when a saved session resumes. See
[Cross-Session Messaging](session-messaging.md).

Status: implementation batches are complete. The scheduled official conformance workflow must
produce green release artifacts before a protocol combination is called conformance-qualified.

## Agentic Graph Specification

AGS 1.0 conformance level 3 is implemented: vendored schemas/reference validation, strict AGX,
managed graph policy, deterministic plans, tier routing, durable schema-conformant records,
parallel scheduling, all node and criterion kinds, approval gates, retries/fallbacks,
compensation, digest-guarded resume, generation, a bundled authoring Skill, read-only MCP exports,
examples, tests, and CI. See [Agentic Graphs](agentic-graphs.md) and the completed
[implementation roadmap](../ROADMAP-agentic-graphs.md).
