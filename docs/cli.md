# CLI Guide

## Core Commands

```bash
loro --version
loro get-started
loro get-started --topic prompts
loro
loro repl --resume-session SESSION_ID
loro doctor
loro config
loro configure
loro setup provider
loro setup identity
loro setup approvals
loro setup sandbox
loro setup audit
loro setup memory
loro setup shared-memory
loro setup polaris
loro setup mcp
loro setup gateway
loro setup agents
loro setup profile
loro setup quickstart
loro plan "Draft a rollout plan"
loro run "Summarize the project"
loro graph generate "Create a release checklist" --out release.agraph.yaml
loro graph validate release.agraph.yaml --strict
loro graph plan release.agraph.yaml --json
loro graph run release.agraph.yaml --dry-run
```

Plain `loro` opens a folder-oriented REPL with provider, model, agent, memory, and durable session
metadata. Its responsive status panel keeps the ASCII parrot, workspace details, and live session,
memory, sandbox, and audit markers together at wide and narrow terminal sizes. Use `/status`,
`/new`, `/resume ID`, `/agent NAME`, `/help`, or `/exit` inside it. State-changing commands refresh
the panel immediately. Assistant output streams as it arrives. Model-directed tools appear as
concise start/completion events with safe argument previews and elapsed time, followed by a compact
turn footer for steps, tool calls, token usage, stop state, and the shortened session ID. The REPL
does not print the verbose batch-mode report used by `loro run`.

`loro get-started` is the context-aware entry point for new and returning users. It summarizes
the effective readiness of the current folder, gives a recommended first journey, and links nine
focused terminal guides: `setup`, `profiles`, `repl`, `prompts`, `artifacts`, `graphs`, `memory`,
`tools`, and `governance`. It is read-only and does not invoke a model or alter permissions.

`loro configure` and `loro setup provider` run the numbered AI provider and model wizard. `loro
setup profile` and `loro agents configure` create an OAP v1 profile through system-instruction,
model-route, capability, tool, Skill, MCP, memory, workspace, and default-profile choices. The
profile wizard explains each authority boundary and only selects configured model routes. Run
plain `loro configure` to dynamically discover and change the primary/small routes before creating
a profile; distinct primary/small routes and configured tiers then appear in its model menu. Web
retrieval uses approval-gated `curl`, not a native search API, and the custom profile flow asks
separately about generic shell and web permissions. The provider wizard discovers the provider's
current models, with paging, search, bundled fallback choices, and `--no-discover-models` for
offline setup. The other setup commands guide identity, approvals, sandboxing, local memory,
shared-memory, Polaris, and MCP configuration. `loro setup quickstart` runs the setup wizards in
sequence and preserves existing sections in `.loro/config.local.toml`.

## Complete Command Map

This map reflects Loro 0.17.0. Run `loro COMMAND --help` or
`loro GROUP COMMAND --help` for arguments, options, defaults, and safety behavior.

```text
loro: agents, approvals, artifacts, audit, brief, config, configure, create, credentials, data, docs, doctor, file, gateway, get-started, graph, identity, mcp, memory, operations, plan, policy, providers, remember, repl, run, safety, sandbox, sessions, setup, sheets, shell, skills, slides, web
loro agents: apply, configure, create, digest, explain, forget, history, list, proposals, review, show, state, validate
loro artifacts: verify
loro approvals: list
loro audit: collect, collector-verify, doctor, flush, metrics, query, report, verify
loro brief: executive, incident, meeting, project
loro credentials: delete, doctor, list, set
loro data: applicable-policies, catalog, catalog-role, catalog-roles, catalogs, explain-access, namespace, namespaces, polaris, policies, policy, principal-role, principal-roles, privileges, schema, table, tables, view, views
loro docs: create
loro file: read, search
loro gateway: doctor, serve, setup
loro graph: generate, plan, policy, resume, run, skill-path, status, validate
loro identity: doctor, show
loro mcp: add, auth-add, auth-list, auth-remove, call, doctor, extension-add, extensions, inspect, list, listen, prompt, prompts, read, remove, resources, serve, server-inspect, task-cancel, task-get, task-start, task-update, tasks, test, tools
loro config: check, show, summary
loro memory: accept-proposal, apply-schema, backend-check, commit-draft, drafts, lifecycle, list, migrate, migration-status, proposals, propose, reconcile, remember, schema, search, shared-search, snapshots, sweep
loro operations: backup, benchmark, recovery-targets, release-readiness, restore, verify-backup
loro policy: explain
loro providers: check, conformance, list, request, show, smoke
loro safety: doctor, scan
loro sandbox: doctor
loro sessions: ack, inbox, list, send, show, wake
loro setup: agents, approvals, audit, gateway, identity, mcp, mcp-server, memory, polaris, profile, provider, quickstart, sandbox, shared-memory, skills
loro sheets: analyze, create
loro shell: run
loro skills: disable, enable, import-claude, import-pi, install, list, propose, quarantine, remove, review, show, validate
loro slides: create
loro web: doctor
```

## Agentic Graphs

`loro graph validate` applies the AGS 1.0 validator and managed policy. `plan` previews order,
fan-out, cost, concurrency, and model-tier demand. `run` creates a durable schema-conformant record;
gates pause as `awaiting_human` and `resume` verifies the graph digest before continuing. See
[Agentic Graphs](agentic-graphs.md).

## Identity

```bash
loro setup identity
loro identity show
loro identity doctor
```

The active identity is attached to audit events and runtime sessions. Its tenant and subject
become shared-memory defaults when `--tenant-id` and `--created-by` are omitted. Managed
configuration can require fields and make runtime/audited commands fail closed. See
[Identity Context](identity.md) for environment variables and trust boundaries.

## Sandbox

```bash
loro setup sandbox --profile controlled-shell --backend bubblewrap \
  --require-os-enforcement --network deny
loro sandbox doctor
```

Shell and Agent Skill scripts use named profiles with executable, cwd, environment, timeout,
output, writable-root, and network controls. See [Subprocess Sandbox Profiles](sandbox.md).

`plan` and `run` can execute explicit typed tool directives in the prompt:

```bash
loro plan '@tool file.read {"path": "README.md", "limit": 1000}'
loro plan '@tool {"name": "file.read", "args": {"path": "README.md", "limit": 1000}}'
loro plan '@tool file.search {"query": "Polaris", "root": ".", "limit": 5}'
```

The runtime loop also lets model responses request tools with the JSON directive form.
Loro executes approved tool calls, returns tool results to the model, and stops when the
model responds without tool directives or `[runtime].max_steps` is reached. The current
tool registry supports:

- `file.read`: `{"path": "README.md", "limit": 1000}`
- `file.search`: `{"query": "Polaris", "root": ".", "limit": 5}`
- `file.write`: `{"path": "notes.md", "content": "Hello"}`
- `file.replace`: `{"path": "notes.md", "old": "Hello", "new": "Hi"}`
- `git.status`: `{"cwd": "."}`
- `git.diff`: `{"cwd": "."}`
- `git.show`: `{"cwd": ".", "revision": "HEAD"}`
- `git.add`: `{"cwd": ".", "paths": ["notes.md"]}`
- `git.commit`: `{"cwd": ".", "message": "Update notes"}`
- `memory.search`: `{"query": "launch template", "limit": 10}`
- `memory.shared_search`: `{"query": "launch template", "tenant_id": "acme"}`
- `shell.run`: `{"args": ["python", "-c", "print(123)"]}`
- `polaris.readonly`: `{"args": ["catalogs", "list"]}`
- `artifact.create`: `{"kind": "document", "prompt": "Draft onboarding guide", "title": "Onboarding Guide", "body_markdown": "## Welcome\n\nFinal authored content."}`
- `mcp.tools`: `{"server_id": "filesystem"}`
- `mcp.call`: `{"server_id": "filesystem", "tool_name": "read_file", "arguments": {"path": "README.md"}}`
- `mcp.resources`, `mcp.read`, `mcp.prompts`, and `mcp.prompt`
- `mcp.task_start`, `mcp.task_get`, `mcp.task_update`, and `mcp.task_cancel`

Runtime write-like calls still obey configured permissions. When policy is `ask`, Loro prompts
the trusted terminal user; a model-provided `approved=true` cannot authorize itself. Explicit
user-authored directives and direct `--yes` flags remain available only when non-interactive
approvals are enabled. `deny` always blocks execution. File writes/replacements and artifact creation use the same
safety scanner as CLI write commands. Polaris runtime calls require `[polaris].enabled = true`
and are constrained to read-only operations. Artifact runtime calls support `document`,
`presentation`, `spreadsheet`, and `brief`; they require final kind-specific authored fields and
write provenance sidecars. Prompt-only calls are rejected unless `offline_scaffold=true` is
explicitly supplied.

## MCP

Install the optional SDK and configure a server:

```bash
python -m pip install "loro-agent[mcp]"
loro setup mcp
loro mcp add filesystem --command npx --arg=-y --arg @modelcontextprotocol/server-filesystem --arg .
loro mcp list
loro mcp inspect filesystem
loro mcp doctor filesystem
loro mcp test filesystem
```

Discover and invoke server capabilities:

```bash
loro mcp tools filesystem
loro mcp call filesystem read_file --arguments '{"path":"README.md"}'
loro mcp resources filesystem
loro mcp read filesystem file:///workspace/README.md
loro mcp prompts filesystem
loro mcp prompt filesystem summarize --arguments '{"audience":"engineering"}'
```

Register extensions, resume modern Tasks, and collect bounded change events:

```bash
loro mcp extension-add io.modelcontextprotocol/tasks --version draft --adapter tasks
loro mcp extensions
loro mcp tasks
loro mcp task-start tasks-server build_report --arguments '{"quarter":"Q2"}'
loro mcp task-get tasks-server TASK_ID
loro mcp task-update tasks-server TASK_ID --responses '{"format":"pptx"}'
loro mcp task-cancel tasks-server TASK_ID
loro mcp listen tasks-server --tools --max-events 10 --max-seconds 15
```

Tool calls require an exact Loro approval by default. The current foundation supports stdio and
Streamable HTTP through the official SDK, prefers stateless MCP `2026-07-28`, and falls back to
classic initialization when policy allows. Tasks and `listen` require modern `2026-07-28`;
Tasks are an experimental extension. See [Model Context Protocol](mcp.md).

Serve an explicit read-only subset of Loro:

```bash
loro setup mcp-server
loro mcp server-inspect
loro mcp serve
```

## Agent Skills

```bash
loro setup skills
loro skills list
loro skills validate ./my-skill
loro skills install ./my-skill --expected-digest sha256:REVIEWED_DIGEST
loro skills propose ./my-skill
loro skills review PROPOSAL_ID --accept
```

See [Agent Skills](skills.md) for trust, activation, package limits, and script controls.

Claude and Pi compatibility imports are preview-first. Review the source digest and unsupported
component inventory before repeating the command with `--expected-digest DIGEST --execute`.
Claude MCP definitions require the separate `--include-mcp` flag and only environment-backed
credentials are accepted.

## Providers

```bash
loro providers list
loro providers show openai
loro providers check openai
loro providers request "hello" --provider openai --model gpt-5.6-luna
loro providers smoke "hello" --provider openai --model gpt-5.6-luna
loro providers smoke "hello" --provider openai --model gpt-5.6-luna --execute --stream
loro providers smoke "hello" --provider gemini --model gemini-3.6-flash --execute
loro providers smoke "hello" --provider anthropic --model claude-sonnet-5 --execute
loro providers smoke "hello" --provider nous --model deepseek/deepseek-v4-flash --execute
loro providers smoke "hello" --provider openrouter --model deepseek/deepseek-v4-flash --execute
loro providers smoke "hello" --provider opencode-zen --model deepseek-v4-flash --execute
loro configure --provider ollama --model llama3.2 --small-model llama3.2
```

Live provider smoke commands require the matching environment variable, such as
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `NOUS_API_KEY`,
`OPENROUTER_API_KEY`, or `OPENCODE_ZEN_API_KEY`. Dry-run `providers request` and
`providers smoke` output redacts API keys and lets users inspect provider-specific request
payloads before spending tokens.

## Approvals

```bash
loro setup approvals
loro shell run -- python -c "print('interactive')"
loro shell run --yes -- python -c "print('automation')"
loro memory commit-draft <draft-id> --execute
loro data --yes catalogs
```

Interactive prompts support once, exact-match session, and deny. Non-interactive approvals are
audited but should be disabled in enterprise managed configuration. See
[Approvals](approvals.md).

## Policy Explanation

Explain a normalized request without executing it:

```bash
loro policy explain '{"tool":"shell","action":"run command","resource":{"kind":"shell","executable_name":"python","arguments":["-V"]}}'
```

Output identifies the decision, reason, configured policy version, policy source, matched rule,
and normalized resource. See [Normalized Resource Policy](policy.md).

## Audit Delivery

```bash
loro setup audit
loro audit doctor
loro audit flush
loro audit verify
loro audit verify --anchor sha256:EXPECTED_FINAL_HASH
loro audit metrics
loro audit collect --path .loro/audit-collector.sqlite3
loro audit collector-verify --path .loro/audit-collector.sqlite3
```

`audit doctor` validates the schema, sink settings, credential environment, and local backlog.
`audit flush` retries buffered HTTP events in order and exits nonzero if delivery remains
incomplete. `audit verify` validates the local SHA-256 chain and can compare its final hash to an
external anchor. See [Audit Events And Delivery](audit.md).

## Memory

```bash
loro remember --local "Status briefs should include risks and next steps."
loro memory list
loro memory search "status briefs"
loro memory shared-search "launch readiness" --tenant-id acme
loro memory shared-search "launch readiness" --tenant-id acme --dry-run
loro memory migration-status
loro memory migrate --target 2 --execute
loro memory reconcile
loro memory snapshots
loro memory lifecycle <memory-id> --action correct --content "Corrected text" \
  --reason "Owner-approved correction" --execute
loro memory lifecycle <memory-id> --action hold --reason "Legal hold" --execute
loro memory lifecycle <memory-id> --action release-hold --reason "Hold released" --execute
loro memory lifecycle <memory-id> --action delete --reason "Approved erasure" --execute
loro memory propose "Use concise status summaries" --target local
loro memory propose "Use the enterprise launch readiness template" --target shared
loro memory proposals
loro memory accept-proposal <proposal-id>
```

Shared memory is explicit-only. Loro can search configured shared memory, stage shared-memory
drafts, and render or execute supported backend SQL, but it never autonomously commits shared
memory. Accepting a shared proposal creates a draft that still requires an explicit
`commit-draft` step.

## Artifacts

```bash
loro docs create "Draft a project kickoff document"
loro slides create "Quarterly platform update"
loro sheets create "Launch readiness tracker"
loro brief meeting "Prepare for roadmap sync"
loro create docs "Create a practical guide to incident triage"
```

These commands ask the configured model for a complete, typed draft, validate its JSON shape,
scan generated content through the artifact data-protection surface, and then render Markdown,
DOCX, PPTX, XLSX, or CSV files. Invalid model output gets one correction attempt, and no artifact
is written unless the draft validates. The `mock` provider fails with instructions to run `loro
configure`; it never silently substitutes placeholder content. `--no-ai` explicitly selects the
deterministic offline scaffold. Use `--output-dir` to choose where generated files go. The `sheets
analyze` command currently creates an analysis workbook from prompt context; it does not ingest an
existing workbook.

## Files And Shell

```bash
loro file read README.md --limit 1000
loro file search "Polaris" --root .
loro shell run --yes -- python -c "print('hello')"
```

Use `--` before child commands that have flags.

## Sessions

```bash
loro sessions list
loro sessions show <session-id>
loro sessions send <sender-id> <recipient-id> "Review is ready."
loro sessions inbox <recipient-id>
loro sessions wake <recipient-id>
loro run --resume-session <recipient-id> "Continue."
```

Relayed messages are durable untrusted context and never carry user authority. See
[Cross-Session Messaging](session-messaging.md).

## Safety

```bash
loro safety scan "api_key = 'abc123456789'"
loro safety scan --file .env
loro safety doctor
```

Managed policy classifies and scans model, memory, artifact, session, tool-output, and audit
flows. Enterprise overlays can make `--allow-sensitive` non-overridable. See
[Managed Data Protection](data-protection.md).

## Governed Data

```bash
loro data catalogs
loro data namespaces --catalog prod
loro data tables --catalog prod --namespace analytics
loro data schema events --catalog prod --namespace analytics
loro data explain-access events --catalog prod --namespace analytics --catalog-role reader
loro data views --catalog prod --namespace analytics
loro data principal-roles
loro data catalog-roles --catalog prod
loro data privileges --catalog prod --catalog-role reader
loro data policies --catalog prod
loro data applicable-policies events --catalog prod --namespace analytics
loro data polaris catalogs list
```

Polaris commands require `[polaris].enabled = true`. Typed commands cover common catalog,
namespace, table, view, role, privilege, and policy discovery. The lower-level
`data polaris` escape hatch is restricted to read-only operation families.
Use `data schema` and `data explain-access` for higher-level governed metadata summaries
without querying table data.
