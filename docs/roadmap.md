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
- Glob-based permission policy rules
- Bounded model-directed runtime loop with provider-neutral JSON tool directives
- Runtime tools for shell execution, local memory search, and read-only Polaris passthrough
- Runtime artifact generation tool with provenance sidecars
- Runtime edit tools for approved file writes and replacements
- Runtime Git helpers for status, diff, show, approved add, and approved commit

## Next MVP Work

- Shared memory retrieval and governance integration
- Iceberg governed execution integration
- Complete model provider adapters, including Bedrock and streaming
- CI, coverage reporting, and integration-test scaffolding

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
prompts remain in Enterprise Hardening; current runtime approvals are explicit tool arguments
plus policy evaluation.

### Batch 2: Shared Memory Retrieval And Governance

Goal: bring Loro's enterprise memory differentiator into the runtime context path.

Acceptance criteria:

- Add shared memory search commands and a backend-neutral search result type.
- Add Postgres shared-memory retrieval and dry-run SQL rendering.
- Wire shared memory recall into `AgentRuntime` with citations and provenance.
- Add first-class local/shared memory proposal records without allowing autonomous shared writes.
- Document explicit-only shared-memory write guarantees.

### Batch 3: Polaris And Iceberg Local Integration

Goal: turn governed data from CLI wrapper scaffolding into a repeatable local integration path.

Acceptance criteria:

- Add a local Polaris/Iceberg testing guide and compose or fixture scaffolding.
- Add Iceberg REST/PyIceberg readiness checks where available.
- Add higher-level `data schema` and `data explain-access` commands.
- Keep all Polaris passthrough operations constrained by read-only validation.

### Batch 4: Provider And Streaming Hardening

Goal: make provider integrations reliable enough for real agent sessions.

Acceptance criteria:

- Add streaming model response interface.
- Add real provider smoke-test command behind explicit user opt-in.
- Implement Bedrock behind optional AWS dependencies.
- Improve provider error messages for auth, rate limits, unsupported models, and malformed
  responses.
- Normalize tool-call response parsing across OpenAI-compatible, Anthropic, Gemini, and local
  providers.

### Batch 5: Testing, CI, And Release Discipline

Goal: keep the expanding harness safe to change.

Acceptance criteria:

- Add GitHub Actions for ruff, pytest, compileall, and coverage.
- Establish a coverage baseline and threshold.
- Add optional integration jobs for Postgres, Polaris, Iceberg, and live model providers.
- Add release checklist docs for packaging, smoke tests, and provider validation.

## Enterprise Hardening

- Managed non-overridable config
- SSO/internal model gateway integration
- Real approval prompts in TUI
- Sandbox profiles
- Audit sinks beyond local JSONL
- Integration tests for Postgres, Iceberg, and Polaris
