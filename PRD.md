# PRD: Loro Enterprise Agent Harness

> Document status: product requirements and design intent. For shipped behavior and support
> classifications in the current `0.12.0` release, use the
> [Project Status](docs/project-status.md), [support matrix](docs/support-matrix.json), and
> [interoperability matrix](docs/interoperability-matrix.json).

## 1. Product Summary

Build **Loro**, a Python-based CLI agentic harness for software engineering, governed data work, and enterprise productivity. "Loro" is the Spanish word for parrot: an intelligent, social bird that listens, learns, repeats useful knowledge, and helps information move across groups. The product should feel like a capable enterprise work companion that can code, analyze, document, summarize, create artifacts, and coordinate repeatable workflows while staying governed and auditable.

The harness should provide a terminal-first experience similar to Claude Code, OpenCode, Hermes Agent, OpenClaw, and adjacent agent harnesses, but with a core differentiator: governed enterprise-wide memory sharing across coding and non-coding work.

The product will support two memory planes:

1. **Local adaptive memory**: private, machine-local memory that improves as the user works, similar in spirit to Hermes Agent's self-improving loop and Claude Code's auto memory.
2. **Shared enterprise memory**: an explicitly user-controlled memory table backed by either Apache Iceberg or Postgres. The agent may read relevant shared memories created by other users, but it may write to shared memory only when the user explicitly dictates what should be remembered.

The harness will also support governed enterprise data access through Apache Polaris. It must use both the Apache Iceberg REST Catalog API for standards-based table access and the Polaris CLI/API surface for the full Polaris governance model, including principals, roles, privileges, policies, catalogs, namespaces, and operational metadata.

Loro should handle work for developers, analysts, managers, operators, and other enterprise users. In addition to coding tasks, it must support productivity artifact generation such as Markdown docs, Word-compatible documents, slide decks, spreadsheets, reports, meeting briefs, runbooks, release notes, project plans, and data-backed executive summaries.

## 2. Goals

- Provide a fast, Python-native CLI agent harness with interactive TUI and non-interactive command modes.
- Support provider-agnostic LLM usage through configurable model providers and internal AI gateways.
- Offer safe tool use for file operations, shell commands, web/docs lookup, Git operations, code search, governed data access, and productivity artifact creation.
- Implement explicit shared memory writes with auditable provenance, permissions, retention, and review workflows.
- Implement local memory that improves automatically from usage while remaining inspectable and editable by the user.
- Let enterprise teams share validated facts, runbooks, coding conventions, architectural decisions, data contracts, meeting norms, reporting templates, presentation standards, spreadsheet models, incident lessons, and Polaris/Iceberg usage patterns across agents.
- Connect to Apache Polaris for governed data discovery and access using enterprise identities and least-privilege authorization.
- Generate and update common business artifacts including docs, presentations, spreadsheets, briefs, reports, plans, diagrams, and status updates.
- Make all high-risk actions reviewable, denyable, logged, and policy-controlled.

## 3. Non-Goals

- Do not build a web-first product in the MVP. A local TUI and CLI are the first-class interfaces.
- Do not allow autonomous writes to enterprise shared memory. Shared memory requires explicit user dictation.
- Do not bypass Polaris or catalog governance by directly reading object storage paths unless explicitly configured for a local development profile.
- Do not store secrets, tokens, passwords, or raw credentials in local memory, shared memory, Iceberg table properties, Polaris catalog properties, or logs.
- Do not attempt to replace enterprise data catalogs, lineage systems, or policy decision points. Integrate with them.
- Do not become a full office-suite editor in the MVP. Loro should generate, inspect, and transform productivity artifacts through structured libraries and file formats, with richer collaborative editing later.

## 4. Research Notes And Inspirations

### Claude Code

Claude Code establishes the expected baseline for a modern coding harness: terminal, IDE, desktop, web, CI, MCP integrations, file edits, command execution, Git workflows, session continuity, subagents, hooks, skills, and permission modes. Its docs describe persistent instructions through `CLAUDE.md`, automatically accumulated local memory, user/project/managed settings scopes, hooks that fire through the agent lifecycle, and MCP as an integration standard.

Product implications:

- Use hierarchical configuration scopes: managed, user, project, and local.
- Treat memory instructions as context, not enforcement. Enforcement belongs in policy and permission layers.
- Provide lifecycle hooks for session start/end, prompt submission, pre-tool use, post-tool use, compaction, and memory proposal events.
- Support subagents with scoped permissions and optional isolated workspaces.
- Provide readable, editable memory files or records with a `/memory` command equivalent.

Sources:

- https://code.claude.com/docs/en/overview
- https://code.claude.com/docs/en/memory
- https://code.claude.com/docs/en/settings
- https://code.claude.com/docs/en/hooks

### OpenCode

OpenCode highlights a strong open-source terminal coding agent architecture with JSON/JSONC config, primary agents, subagents, plan/build modes, granular permissions, central enterprise config, SSO/internal AI gateway support, MCP servers, custom tools, LSPs, plugins, and TUI ergonomics.

Product implications:

- Ship built-in `plan`, `build`, `research`, `data`, and `memory-curator` agents.
- Use explicit permission states: `allow`, `ask`, and `deny`.
- Provide granular matching for shell commands, file paths, external directories, web access, task spawning, and data access.
- Support central enterprise configuration that users cannot override.
- Disable sharing/export features by default in enterprise profiles.

Sources:

- https://github.com/anomalyco/opencode
- https://opencode.ai/docs/config/
- https://opencode.ai/docs/agents/
- https://opencode.ai/docs/permissions/
- https://opencode.ai/docs/enterprise/

### Hermes Agent

Hermes Agent's differentiator is a closed learning loop: agent-curated memory, periodic nudges to persist knowledge, autonomous skill creation after complex tasks, skill improvement during use, conversation search, user modeling, scheduled automations, subagents, RPC-style tools, multiple terminal backends, and batch trajectory generation.

Product implications:

- Implement local memory that can propose learnings and organize them into topic files or a local SQLite database.
- Implement skill proposal and skill refinement locally, but require review before enabling new shared/team skills.
- Add conversation search and summarization for local recall.
- Provide scheduled prompts or monitors only after the core CLI is stable.
- Use task trajectories as optional evaluation artifacts and training/evaluation logs, with redaction.

Sources:

- https://github.com/NousResearch/Hermes-Agent
- https://hermes-agent.nousresearch.com/

### OpenClaw

OpenClaw is a local-first, always-on personal assistant gateway with multi-channel messaging, multi-agent routing, sandboxed non-main sessions, broad tool/channel integrations, onboarding, skills, daemon mode, and strong warnings around untrusted inbound messages.

Product implications:

- Separate the agent runtime from optional gateway/channel processes.
- Treat all remote messages, files, docs, and channel inputs as untrusted external content.
- Add per-agent workspaces and per-channel identities if chat integrations are added later.
- Support sandbox profiles for non-main sessions and for data-access tasks.
- Include a doctor command to flag risky permissions, open inbound channels, and memory table misconfiguration.

Sources:

- https://github.com/openclaw/openclaw
- https://openclaw.ai/

### Pi

Pi (`badlogic/pi-mono`) is a compact TypeScript coding-agent harness with model/provider routing,
sessions, Agent Skills, prompt templates, themes, RPC operation, and a rich extension API. Pi
packages can bundle conventional `skills/`, `extensions/`, `prompts/`, and `themes/` resources or
declare them under the `pi` key in `package.json`. Extensions can register tools, commands,
providers, event handlers, UI components, and session behavior and therefore execute with broad
host authority.

Loro interoperates with Pi at the Agent Skills boundary rather than embedding Pi's TypeScript
extension host. Compatibility imports inspect package manifests, normalize compatible `SKILL.md`
packages, resolve `{baseDir}`, and explicitly report extensions/prompts/themes as unsupported.
This preserves portable workflows while keeping Loro permissions, sandboxing, approvals, and
enterprise provenance authoritative.

Sources:

- https://github.com/badlogic/pi-mono
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/packages.md
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md

### Apache Iceberg

Apache Iceberg is the preferred shared-memory table format for large enterprise deployments because it provides table metadata, snapshots, schema evolution, partition evolution, serializable isolation, optimistic concurrency, branching/tagging, and interoperability across compute engines. Iceberg's optimistic commit model and snapshots are useful for auditability and conflict detection in shared memory.

Product implications:

- Use Iceberg as the high-scale shared memory backend when organizations already operate lakehouse infrastructure.
- Store shared memories as append-only facts/events plus curated current-state views.
- Use Iceberg snapshots for memory audit, rollback, retention, and review.
- Design schema evolution up front so memory records can gain fields without rewriting history.
- Use PyIceberg or a compatible Python client for table operations where possible, and Spark/Trino integration for enterprise deployment examples.

Sources:

- https://iceberg.apache.org/spec/
- https://iceberg.apache.org/docs/latest/rest-catalog/
- https://py.iceberg.apache.org/

### Apache Polaris

Apache Polaris provides a governed catalog layer for Iceberg and related table entities. Its model includes catalogs, namespaces, tables, views, principals, principal roles, catalog roles, privileges, and policies. Polaris supports the Iceberg REST catalog protocol while also exposing richer management and governance operations through its CLI and management APIs.

Product implications:

- Use the Iceberg REST catalog path for standard table discovery and metadata operations.
- Use the Polaris CLI/API for full governance operations that are outside the Iceberg REST spec, such as role grants, privilege inspection, policy listing, policy attachment, namespace management, and catalog-role workflows.
- Never place secrets in Polaris catalog/table/view properties, because these are client-visible metadata surfaces.
- Use Polaris policy and role inspection before data tools are exposed to the LLM.
- Provide a governed data agent that can explain why a table is or is not accessible based on Polaris roles and privileges.

Sources:

- https://polaris.apache.org/in-dev/unreleased/getting-started/
- https://polaris.apache.org/in-dev/unreleased/command-line-interface/
- https://polaris.apache.org/in-dev/unreleased/entities/
- https://polaris.apache.org/in-dev/unreleased/policy/

## 5. Users And Personas

- **Enterprise developer**: wants a terminal coding agent that understands local repositories, shared engineering practices, and organization-specific knowledge.
- **Data engineer**: wants the agent to discover governed tables, generate safe SQL/Spark snippets, understand schemas, and respect Polaris permissions.
- **Business analyst**: wants Loro to analyze data, create spreadsheets, write formulas, generate charts, and turn findings into clear reports.
- **Manager or team lead**: wants Loro to produce meeting briefs, status updates, project plans, decision memos, slide decks, and follow-up summaries.
- **Operations/support specialist**: wants Loro to draft runbooks, triage tickets, summarize incidents, prepare handoff docs, and reuse enterprise lessons.
- **Platform administrator**: wants central config, SSO/internal AI gateway routing, permission policy, audit logs, and memory governance.
- **Security/compliance reviewer**: wants explicit controls for tool access, memory writes, data access, retention, redaction, and incident reconstruction.
- **Team lead/architect**: wants shared, curated lessons to propagate across agent sessions and users without relying on stale docs.

## 6. Core Product Requirements

### 6.1 CLI And TUI

Required commands:

- `loro`: launch interactive TUI.
- `loro run "<prompt>"`: run a one-shot task.
- `loro plan "<prompt>"`: read-only planning mode.
- `loro config`: view and edit resolved configuration.
- `loro doctor`: validate provider, permissions, memory, Polaris, and sandbox configuration.
- `loro memory`: inspect local and shared memories.
- `loro remember --local "<memory>"`: explicitly write a local memory.
- `loro remember --shared "<memory>"`: explicitly write shared enterprise memory.
- `loro memory propose`: show local memory proposals generated from recent work.
- `loro data catalogs|namespaces|tables|schema|policy`: governed data discovery commands.
- `loro docs create|summarize|revise|export`: create and transform documents.
- `loro slides create|outline|revise|export`: create and transform presentation decks.
- `loro sheets create|analyze|chart|clean|export`: create and transform spreadsheets.
- `loro brief meeting|project|incident|executive`: create concise enterprise briefs.
- `loro polaris passthrough -- <args>`: controlled wrapper around the Polaris CLI, disabled by default unless enabled by policy.

TUI requirements:

- Streaming assistant output.
- Tool call timeline with status, arguments summary, result summary, and approval state.
- Diff viewer for file edits.
- Session list and resume.
- Memory drawer showing recalled local/shared memories and why they were selected.
- Artifact pane showing generated documents, spreadsheets, slides, reports, and export paths.
- Permission prompts with persistent rule options when allowed by policy.
- Compact mode for low-noise terminal usage.

### 6.2 Agent Loop

The harness must implement a transparent agent loop:

1. Load system prompt, managed policy, project instructions, local memory index, relevant shared memories, and active task.
2. Ask model for next action.
3. Validate action against permissions, policy, sandbox, data governance, and memory rules.
4. Execute approved tool call.
5. Capture structured result.
6. Repeat until complete, blocked, or budget exhausted.
7. Summarize outcome, changes, tests, and memory proposals.

The loop must support:

- Max steps per agent.
- Model-specific timeouts and streaming.
- Context compaction.
- Tool-result summarization.
- Stop conditions.
- Subagent delegation.
- Trace logs.

### 6.3 Built-In Agents

- `plan`: read-only exploration, requirements breakdown, and risk assessment.
- `build`: file edits, shell, tests, Git operations subject to permission.
- `research`: docs/web/source-code research, read-only by default.
- `data`: governed data discovery and query drafting through Polaris/Iceberg controls.
- `document`: drafts, revises, summarizes, and exports Markdown, DOCX-compatible, and PDF-targeted documents.
- `presentation`: outlines and generates slide decks with speaker notes, source citations, and exportable PPTX-compatible files.
- `spreadsheet`: creates workbooks, cleans tabular data, writes formulas, builds charts, and explains calculations.
- `briefing`: creates meeting prep, status reports, executive summaries, incident reports, and decision memos from approved context.
- `memory-curator`: proposes local memory updates and shared memory drafts, but cannot commit shared memory directly.
- `security-review`: scans proposed actions, permissions, prompts, external content, and data-access plans.

Each agent must have:

- Prompt/instruction file.
- Tool allowlist.
- Permission defaults.
- Optional model override.
- Max steps.
- Memory access scope.
- Sandbox requirement.

### 6.4 Tools

MVP tools:

- File read/search/glob.
- File edit/patch/write.
- Shell command execution.
- Git status/diff/show/commit branch helpers.
- Python execution in a sandboxed subprocess.
- Web/docs fetch if enabled.
- Dual-era MCP client support for the current stateless specification and classic
  handshake-based servers.
- Agent Skills discovery, validation, progressive disclosure, and policy-governed execution.
- Agentic Graph Specification 1.0 level-3 validation and execution, including managed policy,
  model-tier routing, harness-evaluated success criteria, durable approval gates, bounded
  parallel/iterative composition, resumable run records, and goal-to-graph generation.
- Polaris CLI wrapper.
- Iceberg REST catalog client.
- Postgres memory backend client.
- Iceberg memory backend client.
- Local memory client.
- Document generation and parsing tools for Markdown, DOCX, PDF-targeted outputs, and plain text.
- Presentation generation and parsing tools for PPTX-compatible decks.
- Spreadsheet generation and analysis tools for XLSX, CSV, TSV, formulas, charts, and tabular summaries.
- Diagram generation tools for Mermaid and image export where supported.
- Audit logger.

Tool calls must be structured, typed, logged, and policy-checkable before execution.

### 6.5 Permissions And Policy

Permission decisions:

- `allow`: execute without prompting.
- `ask`: prompt user before execution.
- `deny`: block.

Policy layers:

1. Managed enterprise policy.
2. Command-line flags.
3. Local project policy.
4. Project policy committed to source.
5. User policy.
6. Built-in defaults.

Managed policy must be non-overridable. Permission rules should merge by specificity where possible, with explicit managed denies always winning.

Policy-controlled dimensions:

- Tool name.
- Command pattern.
- File path.
- External directory.
- Network destination.
- Data catalog/namespace/table.
- Memory backend.
- Memory write scope.
- Model provider.
- Subagent type.
- Sandbox profile.

### 6.6 Configuration

Support `TOML` for human editing and JSON schema export for validation.

Configuration files:

- Managed: `/etc/loro/config.toml` on Linux, plus OS-specific enterprise locations later.
- User: `~/.config/loro/config.toml`.
- Project: `.loro/config.toml`.
- Local project: `.loro/config.local.toml`, gitignored.
- Runtime: `LORO_CONFIG`, `LORO_CONFIG_CONTENT`, and CLI flags.

Example:

```toml
[model]
provider = "internal_gateway"
model = "enterprise-coding-agent"
small_model = "enterprise-small"

[permissions]
default = "ask"
shell = "ask"
edit = "ask"
web = "deny"

[memory.local]
enabled = true
path = "~/.local/share/loro/memory"
auto_propose = true

[memory.shared]
enabled = true
backend = "iceberg"
write_policy = "explicit_user_dictation_only"
read_policy = "semantic_retrieval_with_citations"

[memory.shared.iceberg]
catalog_uri = "https://polaris.example.com/api/catalog"
warehouse = "enterprise"
namespace = "agent_memory"
table = "shared_memories"

[polaris]
enabled = true
cli_path = "polaris"
realm = "production"
catalog = "enterprise"
require_role_inspection = true
```

### 6.7 Enterprise Artifact Generation

Loro must treat business artifacts as first-class outputs, not incidental files. The artifact system should support:

- Documents: Markdown, plain text, DOCX-compatible output, PDF-targeted rendering, proposals, PRDs, SOPs, runbooks, policies, release notes, meeting notes, and decision records.
- Presentations: PPTX-compatible decks, outlines, slide narratives, speaker notes, appendix slides, executive summaries, and data-backed charts.
- Spreadsheets: XLSX, CSV, TSV, tabular data cleanup, formulas, pivot-ready tables, charts, variance analysis, reconciliations, and model documentation.
- Briefs: meeting prep, project status, incident summaries, risk summaries, operating reviews, launch briefs, and executive summaries.
- Diagrams: Mermaid diagrams, architecture sketches, workflow diagrams, data lineage diagrams, and organization-safe visual exports.

Artifact requirements:

- Every generated artifact must include enough provenance for the user to understand its inputs, memory usage, data sources, and assumptions.
- Artifacts based on governed enterprise data must cite the catalog, namespace, table, view, snapshot or query context, and policy-safe summary of access.
- Artifacts should be generated in editable source formats first, then exported to binary formats.
- Loro should validate generated artifacts before finalizing when possible, such as opening a workbook, checking formulas, rendering a document, or validating a slide deck package.
- Loro must not embed secrets, credentials, or restricted raw data into artifacts unless policy explicitly allows and the user confirms.
- Generated artifacts should support enterprise templates and style guides from local/project/shared memory.

MVP artifact approach:

- Generate Markdown and structured JSON plans as the canonical intermediate representation.
- Use Python libraries to emit DOCX, PPTX, XLSX, CSV, and PDF-targeted outputs.
- Keep artifact creation gated by file-write permissions.
- Store artifact metadata in session logs without storing full sensitive contents in audit logs.

## 7. Memory System

### 7.1 Memory Principles

- Local memory may be proposed automatically and written after user approval or under local auto-memory settings.
- Shared memory must never be written from autonomous inference alone.
- Shared memory writes require an explicit user utterance or command, such as `remember this in shared memory: ...`.
- Every shared memory record must include provenance, author identity, source session, timestamp, scope, confidence, classification, and review status.
- The agent must cite recalled shared memory in answers and show why it was retrieved.
- Users must be able to inspect, edit, deprecate, and challenge memories according to permissions.
- Sensitive data must be classified and redacted before memory storage.

### 7.2 Local Memory

Local memory supports:

- User preferences.
- Repository-specific build/test/debug notes.
- Agent mistakes and corrections.
- Tooling habits.
- Frequent task patterns.
- Local conversation summaries.
- Personal writing preferences, audience preferences, recurring meeting formats, and artifact style choices.
- Frequently used spreadsheet assumptions, document structures, and presentation narrative patterns.

MVP storage:

- SQLite database for indexed metadata and retrieval.
- Markdown export/import for user inspection.
- Optional vector index with provider-pluggable embeddings.

Local memory write paths:

- Explicit `loro remember --local`.
- TUI `/remember local`.
- Auto-proposed memory after task completion.
- User-approved local memory proposal.

### 7.3 Shared Enterprise Memory

Shared memory supports:

- Cross-team architectural decisions.
- Known incident resolutions.
- Coding standards and platform runbooks.
- Data contracts and governed table notes.
- Tool-specific operational knowledge.
- Enterprise writing standards, deck templates, spreadsheet conventions, reporting cadences, and audience-specific communication preferences.
- Reusable artifact patterns such as project status formats, operating review structures, launch-readiness checklists, and financial model assumptions.
- Team-approved agent lessons.

Shared memory write paths:

- Explicit `loro remember --shared`.
- TUI `/remember shared`.
- User approves a memory draft and confirms exact text.
- Optional reviewer workflow for publishable memories.

The agent may suggest shared memory candidates, but the final committed text must be dictated or approved explicitly by the user. The UI must clearly distinguish:

- Suggested draft.
- User-approved final content.
- Published shared memory.

### 7.4 Shared Memory Schema

Logical schema:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `memory_id` | string/UUID | yes | Stable ID |
| `tenant_id` | string | yes | Enterprise tenant |
| `scope_type` | enum | yes | org, domain, team, repo, catalog, namespace, table, user-group |
| `scope_key` | string | yes | e.g. repo URL, Polaris table identifier |
| `memory_type` | enum | yes | preference, fact, runbook, decision, warning, data-note, workflow, incident, template, artifact-standard, communication-style |
| `content` | string | yes | User-approved memory text |
| `summary` | string | yes | Short retrieval summary |
| `tags` | array/string | no | Search and governance tags |
| `classification` | enum | yes | public-internal, confidential, restricted, regulated |
| `source` | json | yes | Session, user, command, client version |
| `created_by` | string | yes | User/principal |
| `created_at` | timestamp | yes | Commit time |
| `updated_at` | timestamp | no | Current version update |
| `status` | enum | yes | active, draft, deprecated, disputed, deleted |
| `confidence` | float | no | User/reviewer supplied or default |
| `review` | json | no | Reviewer, approval, expiry |
| `embedding_ref` | string | no | Pointer to vector store or embedding table |
| `supersedes` | array/string | no | Previous memory IDs |
| `expires_at` | timestamp | no | Retention/validity |

Iceberg physical design:

- Table: `agent_memory.shared_memories`.
- Partitioning: `tenant_id`, `scope_type`, optionally bucketed `scope_key`.
- Append-only change log table: `agent_memory.memory_events`.
- Current active view: `agent_memory.active_shared_memories`.
- Optional embeddings table: `agent_memory.memory_embeddings`.

Postgres physical design:

- `shared_memories` table.
- `memory_events` audit table.
- `memory_embeddings` table with `pgvector` when available.
- Row-level security by tenant/scope.
- Triggers for audit fields and immutable event history.

### 7.5 Retrieval

Retrieval should combine:

- Scope filtering.
- Permission filtering.
- Keyword/BM25 search.
- Embedding similarity when enabled.
- Recency and status filters.
- User-selected memory packs.
- Polaris table/data scope when a task references governed data.

The agent prompt must include only compact memory summaries by default. Full memory content is fetched on demand.

## 8. Apache Polaris And Governed Data Access

### 8.1 Required Capabilities

- Authenticate to Polaris through configured enterprise identity.
- Discover catalogs, namespaces, tables, views, and table metadata.
- Inspect principal roles, catalog roles, privileges, and applicable policies.
- Verify access before exposing data tools to the agent.
- Explain denied access in terms of missing roles/privileges where permitted.
- Generate SQL/Spark/PyIceberg snippets that respect catalog configuration.
- Use Iceberg REST catalog APIs for standards-based table operations.
- Use Polaris CLI/API for management/governance operations not covered by the Iceberg REST spec.

### 8.2 Polaris CLI Wrapper

The harness must not shell out to `polaris` as an unconstrained generic command. It should provide a typed wrapper:

- Parse requested operation.
- Validate against permission policy.
- Redact secrets.
- Execute `polaris` with structured arguments.
- Capture JSON where available.
- Normalize output into typed result objects.
- Log operation and decision.

Allowed MVP operations:

- `catalogs list|get`
- `namespaces list|get`
- `tables list|get`
- `views list|get`
- `principal-roles list|get`
- `catalog-roles list|get`
- `privileges list`
- `policies list|get`
- `applicable-policies get`

Admin mutations such as grants, revokes, policy attachments, catalog creation, and namespace creation must default to `deny` or `ask` under managed policy.

### 8.3 Data Query Safety

For MVP, the harness should draft queries and optionally run metadata-only queries. Running row-level queries must be gated by enterprise policy.

Query execution requirements:

- Show catalog, namespace, table, columns, and estimated sensitivity before execution.
- Require approval for row-returning queries unless policy explicitly allows.
- Limit rows by default.
- Redact or block restricted columns based on policy metadata.
- Log query text, row count, duration, and destination, without logging sensitive result data by default.

## 9. Security, Governance, And Compliance

Required controls:

- Central managed configuration.
- SSO/internal model gateway support.
- Secret redaction in prompts, logs, memory, and tool output.
- `.env` and secret-file read protection by default.
- Explicit trust boundary for external content.
- Sandboxed execution profiles.
- Full audit trail for tool calls and memory writes.
- Memory moderation/classification hook.
- Policy-enforced shared memory write gate.
- Policy-enforced data access gate.
- Admin kill switch for tools, providers, and memory backends.

Audit events:

- Session started/ended.
- Prompt submitted.
- Tool requested/allowed/denied/executed/failed.
- Permission prompt shown and user decision.
- Memory retrieved.
- Local memory written.
- Shared memory proposed.
- Shared memory written/deprecated/disputed.
- Polaris operation requested/executed/denied.
- Data query requested/executed/denied.
- Config resolved and policy applied.

## 10. Architecture

### 10.1 Components

- `loro.cli`: Typer-based CLI.
- `loro.tui`: Textual/Rich terminal UI.
- `loro.runtime`: agent loop, session state, compaction.
- `loro.models`: provider adapters and streaming.
- `loro.tools`: typed tool registry.
- `loro.permissions`: policy engine and approval prompts.
- `loro.memory.local`: SQLite/Markdown local memory.
- `loro.memory.shared`: shared memory interface.
- `loro.memory.iceberg`: Iceberg-backed shared memory implementation.
- `loro.memory.postgres`: Postgres-backed shared memory implementation.
- `loro.polaris`: Polaris REST/CLI integration.
- `loro.iceberg`: Iceberg REST/PyIceberg integration.
- `loro.artifacts.documents`: document parsing, generation, rendering, and export.
- `loro.artifacts.presentations`: slide deck planning, generation, validation, and export.
- `loro.artifacts.spreadsheets`: workbook generation, formula handling, charting, and validation.
- `loro.artifacts.briefs`: structured briefs, summaries, and report templates.
- `loro.audit`: structured logs and audit sinks.
- `loro.sandbox`: local, Docker, SSH, or enterprise sandbox backends.
- `loro.hooks`: lifecycle hooks.
- `loro.mcp`: MCP client/server bridge.
- `loro.skills`: Agent Skills discovery, validation, provenance, and activation.

### 10.2 Suggested Python Stack

- CLI: `typer`.
- TUI: `textual` and `rich`.
- Config: `pydantic-settings`, `tomli/tomli-w`, JSON schema export.
- HTTP: `httpx`.
- LLM adapters: provider-specific SDKs plus OpenAI-compatible API support.
- Database: `sqlalchemy`, `psycopg`, `pgvector` optional.
- Iceberg: `pyiceberg`.
- Documents: `python-docx`, Markdown tooling, optional `pypdf`/PDF rendering tools.
- Presentations: `python-pptx`.
- Spreadsheets: `openpyxl`, `xlsxwriter`, `pandas`, optional `duckdb`.
- Search: SQLite FTS5 locally, Postgres full-text/pgvector for Postgres backend.
- Logging: `structlog`.
- Testing: `pytest`, `pytest-asyncio`, `respx`, local Docker Compose integration tests.

## 11. MVP Scope

### MVP Must Have

- Interactive CLI/TUI with streaming.
- One-shot CLI mode.
- Provider-agnostic model config.
- File, search, shell, edit, and Git tools.
- Basic document generation from prompts and approved context, with Markdown output and DOCX-compatible export.
- Basic spreadsheet generation and analysis for CSV/XLSX, including formulas and summary charts.
- Basic presentation outline and PPTX-compatible deck generation from approved source material.
- Meeting, project, and incident brief generation.
- Permission engine with `allow/ask/deny`.
- Local memory with inspect/propose/approve flow.
- Shared memory interface with Postgres backend.
- Iceberg backend design plus minimal PyIceberg proof of concept.
- Explicit-only shared memory write gate.
- Polaris CLI wrapper for read-only discovery and policy inspection.
- Audit log.
- `doctor` diagnostics.
- Docs with examples.

### MVP Should Have

- Iceberg REST catalog integration for shared memory.
- Semantic retrieval with optional embeddings.
- MCP client support for `2026-07-28` plus classic compatibility through `2025-11-25`.
- Agent Skills `SKILL.md` support with progressive disclosure and Loro policy enforcement.
- Sandbox profiles.
- Subagents for research, productivity artifact generation, and memory curation.
- Query drafting for governed Iceberg tables.
- Enterprise template packs for common docs, spreadsheets, presentations, and briefs.
- Artifact validation checks for generated DOCX/PPTX/XLSX files.

### MVP Could Have

- Scheduled tasks.
- Multi-channel gateway.
- Agent-generated skill proposals with explicit local installation and governed enterprise
  publication review.
- Web UI.
- Enterprise admin console.
- Collaborative document integrations for Google Drive, Microsoft 365, Notion, Confluence, Jira, Slack, and email.

## 12. Acceptance Criteria

- A user can run `loro` in a repository and ask the agent to inspect, edit, test, and summarize changes.
- A non-developer enterprise user can run `loro brief meeting` or `loro docs create` and generate a useful artifact without needing a source-code repository.
- A user can create a DOCX-compatible document, PPTX-compatible presentation, and XLSX spreadsheet from approved local context.
- A user can ask Loro to analyze a CSV/XLSX file, generate formulas/charts, and explain the workbook assumptions.
- A user can run `loro plan` and verify no file edits or shell mutations occur without approval.
- A user can inspect all resolved permissions and configuration with `loro config`.
- A user can ask the agent to remember something locally and see it recalled in a later session.
- The agent can propose a local memory from a completed task, but the user can accept, edit, or reject it.
- The agent cannot write shared memory unless the user explicitly issues a shared-memory remember command or approves exact final text.
- A shared memory written by user A can be retrieved by user B when scope and permissions allow.
- A shared memory cannot be retrieved by a user lacking tenant/scope permission.
- Postgres shared memory backend passes create/read/search/deprecate/audit tests.
- Iceberg shared memory backend can append memories and read active scoped memories in a local test catalog.
- Polaris wrapper can list catalogs/namespaces/tables and inspect roles/policies in a configured environment.
- The harness refuses to store secrets in memory and warns when content appears sensitive.
- The harness refuses to embed restricted data in generated artifacts unless policy and user confirmation allow it.
- Every tool call and memory mutation has an audit event.

## 13. Metrics

Product metrics:

- Task completion rate.
- Artifact generation success rate by artifact type.
- Artifact validation pass rate.
- User approval rate for proposed local memories.
- Shared memory reuse rate.
- Shared memory dispute/deprecation rate.
- Time saved per recurring task.
- Tool denial/approval rate.
- Polaris access denial explanation success rate.

Quality metrics:

- Retrieval precision for local and shared memories.
- False-positive secret detection rate.
- False-negative memory write gate rate, target 0 for autonomous shared writes.
- Test pass rate after agent modifications.
- Formula correctness and artifact render validation success rate.
- User-reported stale memory rate.

Operational metrics:

- LLM token usage per task.
- Tool execution latency.
- Memory retrieval latency.
- Polaris CLI/API latency.
- Shared memory backend write/read latency.
- Audit log delivery success.

## 14. Risks And Mitigations

| Risk | Mitigation |
| --- | --- |
| Agent writes incorrect shared memory | Require explicit user dictation, provenance, review status, dispute/deprecate workflow |
| Shared memory leaks sensitive data | Secret scanning, classification, policy gates, redaction, admin review |
| Agent bypasses Polaris governance | Disable direct object-store access by default; require Polaris checks before data tools |
| Stale or conflicting memories | Status fields, supersession, expiry, confidence, retrieval ranking, citation display |
| Productivity artifacts contain unsupported assumptions | Require provenance, assumptions sections, validation checks, and user review before final export |
| Documents or slides leak restricted data | Classify sources, redact by default, log artifact provenance, and require policy/user confirmation for sensitive exports |
| Permission prompts become noisy | Scoped persistent rules, managed defaults, command/path patterns |
| Shell/file tools cause damage | Ask-by-default, sandbox profiles, deny destructive commands by default |
| Iceberg backend complexity slows MVP | Build Postgres backend first, Iceberg adapter second behind same interface |
| Ambiguous enterprise identity mapping | Integrate with SSO/internal gateway; include principal and role diagnostics |

## 15. Open Product Decisions

- Which enterprise document, slide, and spreadsheet templates should ship as built-ins versus managed organization templates?
- Which collaboration suites should be integrated first: Microsoft 365, Google Workspace, Notion, Confluence, Jira, Slack, or email?
- Should shared memory require reviewer approval before becoming active, or can user-authored memories be active immediately by scope?
- Which embedding provider is acceptable for restricted enterprise memories?
- Should Iceberg shared memory use one table per tenant or a multi-tenant table with strict scope filtering?
- How should Polaris row-level policies be represented to the agent without exposing sensitive policy details?

## 16. Milestones

### Milestone 1: CLI Agent Foundation

- Typer CLI.
- Textual/Rich TUI prototype.
- Provider adapter.
- Agent loop.
- File/search/shell/edit tools.
- Permission engine.
- Session logs.
- Basic Markdown artifact output.

### Milestone 2: Local Memory

- SQLite local memory.
- `/memory` UI.
- Explicit local remember.
- Auto memory proposal after session.
- Retrieval into prompt with citations.

### Milestone 3: Productivity Artifacts

- Document agent with Markdown and DOCX-compatible export.
- Spreadsheet agent with CSV/XLSX read/write, formulas, and charts.
- Presentation agent with outline-to-PPTX-compatible export.
- Briefing agent for meetings, projects, incidents, and executive summaries.
- Artifact provenance metadata.
- Basic render/open validation for generated files.

### Milestone 4: Shared Memory Postgres

- Shared memory interface.
- Postgres schema and migrations.
- Explicit shared remember gate.
- Search/retrieval.
- Audit events.
- Deprecate/dispute flow.

### Milestone 5: Polaris Read-Only Governance

- Polaris CLI wrapper.
- Catalog/namespace/table discovery.
- Role/privilege/policy inspection.
- Governed data agent.
- Doctor diagnostics.

### Milestone 6: Iceberg Shared Memory

- PyIceberg/REST catalog setup.
- Iceberg shared memory table.
- Append/read/search proof of concept.
- Snapshot-aware audit and rollback docs.

### Milestone 7: Enterprise Hardening

- Managed config.
- SSO/internal gateway integration.
- Secret scanning.
- Sandbox profiles.
- Dual-era MCP and Agent Skills support governed by the documented support and interoperability
  matrices.
- Integration tests and security review.

### Milestone 8: Secure Remote Work And Credentials

- OS-keyring credential vault with named accounts and environment override compatibility.
- Signed Slack, Discord, and Telegram gateways with tenant-scoped identity mapping.
- Teams Workflow/outgoing-webhook support and a generic signed bridge for Signal and other chat
  systems.
- Durable replay suppression, bounded asynchronous work, safe replies, and no remote approval
  authority.
