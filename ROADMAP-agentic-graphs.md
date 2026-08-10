# Loro × Agentic Graph Specification — implementation roadmap

> Companion to [docs/roadmap.md](docs/roadmap.md) and
> [docs/enterprise-readiness-roadmap.md](docs/enterprise-readiness-roadmap.md). Scoped to one
> capability: making Loro a first-class **Agentic Graph** harness — able to accept, validate,
> govern and execute graphs users submit, and to generate conformant graphs from a goal.
>
> Spec repository: **`agentic-graph-spec`** (AGS 1.0) —
> <https://github.com/AlexMercedCoder/agentic-graph-spec>
> Key references: `SPEC.md`, `schema/agentic-graph-1.0.schema.json`,
> `schema/agentic-graph-run-1.0.schema.json`, `docs/harness-integration.md`,
> `docs/skill-authoring.md`, `tools/validate_agraph.py`.
>
> Status: implemented. Phases 0-6 are represented in code, tests, examples, CI, and docs.
> Last updated: 2026-08-10.

## Implementation record

The roadmap was completed against AGS 1.0 commit
`6bf105f2f7b51176bc1a4b49db0a722a2aa2e774`. The implementation lives in
`src/loro/agraph/`; CLI integration is isolated in `src/loro/cli_graph.py`. The upstream schemas,
reference validator, valid examples, and invalid conformance fixtures are vendored for
reproducibility. User documentation is in `docs/agentic-graphs.md` and
`docs/agraph-policy.md`; security assumptions are in `docs/threat-model.md`.

Repository completion means the portable implementation and hermetic proof exist. External
enterprise evidence remains organization-specific: managed policy approval, production sandbox
proof, protected live-provider cost/reliability runs, identity-provider integration, and an
approved external-checker registry must be supplied by the adopting organization.

| Delivered area | Implementation and evidence |
| --- | --- |
| AGS loading and conformance | `src/loro/agraph/document.py`, `validate.py`, vendored schemas/reference validator, and invalid fixtures in `tests/fixtures/agraph/` |
| Governance and planning | `policy.py`, `plan.py`, managed `[agraph]` configuration, exact-digest approval, and CLI/MCP read-only planning surfaces |
| Durable execution | `execute.py`, `store.py`, `schedule.py`, conformant run records, resume digest guards, gates, checkpoints, retries, budgets, and audit events |
| Level 3 features | AGX decisions, joins, loops, maps, local subgraphs with integrity checks, parallel execution, judges, external criteria, fallback, and compensation |
| Authoring and ecosystem | Deterministic generation/repair, three Loro examples, bundled `agentic-graph` Skill, `loro doctor` capability reporting, and pinned conformance CI |
| Verification | `tests/test_agraph.py`, MCP/tool integration tests, strict example validation, coverage gates, wheel install smoke testing, and secret-baseline checks |

---

## 1. Why this fits Loro specifically

Loro's thesis, per [ARCHITECTURE.md](ARCHITECTURE.md) and [PRD.md](PRD.md), is an agent harness for
**enterprise coding, governed data work, and productivity artifacts**, where identity, approvals,
permissions, budgets and audit are load-bearing rather than bolted on.

An Agentic Graph is the artifact that governance story has been missing. Today Loro can audit what
an agent *did*. With AGS it can audit — and let a person approve — what an agent *is going to do*,
before a single token is spent. "Approve the plan, not the output" is a materially stronger
enterprise position than "review the transcript afterwards," and Loro is unusually well placed to
implement it because the enforcement primitives already exist.

The existing pieces, and what each becomes under AGS:

| Loro today | AGS concept | Fit |
| --- | --- | --- |
| `src/loro/runtime.py` → `AgentRuntime.run(prompt, mode, session_id)`, the bounded model-directed loop | The inside of a `task` node | Direct. The existing loop becomes the node executor's inner loop; steps 1–14 of the documented runtime flow are unchanged. |
| `src/loro/budgets.py` → `UsageBudget`, `BudgetExceeded`, `before_model`/`after_model`/`add_tool_calls` | `constraints` (tokens, cost, tool calls) | Very close. Already fail-closed, already tracks input/output tokens, cost and tool calls. Needs per-node scoping and the `min(node, remaining global)` nesting rule. |
| `RuntimeConfig` → `max_steps`, `max_tool_calls`, `max_model_input_bytes`, `max_input_tokens`, `max_cost_usd` | `constraints.max_agent_steps`, `max_tool_calls`, `max_total_tokens`, `max_cost_usd` | Near one-to-one rename. `max_steps` is exactly AGS `max_agent_steps` (the node's inner-loop bound). |
| `src/loro/approvals.py` → `ApprovalManager`, `ApprovalRequest`, `ApprovalScope`, identity binding, expiry, replay protection | `gate` nodes and `human[]` checkpoints | Excellent fit, and better than most harnesses will manage. Identity-bound, non-replayable approvals are exactly what an AGS gate should be. |
| `src/loro/permissions.py` → `PermissionEngine` allow/ask/deny + `src/loro/resources.py` normalized resource scopes | `requirements.permissions` intersection | Direct. AGS permissions are a *requested ceiling*; Loro's ordered rules and managed overlays remain the enforced policy. |
| `src/loro/audit/` → schema 1.0 envelope, identity/trace/action/target/policy/approval/result fields, JSONL hash chain, HTTP sink | AGS run records | Strong fit. A run record is a structured summary; the hash-chained JSONL stays the tamper-evident ground truth. |
| `src/loro/sandbox.py` → named subprocess profiles, Bubblewrap enforcement, network deny, writable roots, `max_seconds`, `max_output_bytes` | `constraints.isolation`, `requirements.network`, `requirements.workspace` | Direct. Loro's profiles are richer than AGS's four isolation values, so map AGS → profile name. |
| `src/loro/identity.py` → `IdentityContext` (subject, tenant, groups, roles) | `gate.roles`, `human[].roles`, escalation targets | Direct. Role-restricted approvals become enforceable rather than advisory. |
| `src/loro/data_protection.py` → `DataProtectionEngine.enforce(content, surface)` | `inputs/outputs.redact`, run-record redaction | Direct. AGS mandates that redacted values never reach records; Loro already has the engine. |
| `src/loro/skills.py` → `SkillRegistry`, provenance, lifecycle, `max_active` | `requirements.skills` | Direct. |
| `src/loro/tool_runtime.py` → `ToolRegistry` with `file.read`, `file.search`, `file.write`, `file.replace`, `shell.run`, `memory.search`, `memory.shared_search`, `polaris.readonly`, `artifact.create`, `skill.read`, `skill.run_script`, `session.inbox`, `session.send` | `requirements.tools` logical names | Needs a small mapping table: AGS says `file_write`, Loro says `file.write`. |
| `src/loro/sessions.py` → `SessionRecord`, `SessionStore` (durable JSON) | Per-node execution records | Reusable pattern; a graph run needs a parent record above it. |
| `src/loro/config.py` → layered config with **non-overridable managed overlays** (`/etc/loro/managed.toml`, `LORO_MANAGED_CONFIG`) | Enterprise policy over submitted graphs | **This is Loro's differentiator.** See §2.2. |
| `src/loro/artifacts/` → documents, presentations, spreadsheets, briefs, provenance sidecars | `outputs` of type `artifact` | Direct. Provenance sidecars already record what AGS wants in a run record. |
| `src/loro/polaris.py`, `src/loro/memory/iceberg.py`, `src/loro/governed_data.py` | Governed-data graphs | The most distinctive AGS use case Loro can offer: multi-step governed data work with catalog reads declared per node. |
| `src/loro/mcp/` → typed registry, security policy, dual-era transports | `requirements.mcp_servers` | Direct, and Loro's MCP security boundary means declared MCP requirements are actually enforceable. |

### Gaps at roadmap outset

The following bullets are retained as the original gap analysis. Each is closed by the
implementation record above and the completed phase work below.

Being explicit about the gaps, because they define most of the work:

- **No model roles.** `ModelConfig` in `src/loro/config.py` has one `provider`/`model` plus a
  `small_model`. AGS needs a **routing profile**: four tiers → model choices. This is new config,
  not new machinery.
- **No task ledger.** `SessionStore` records one completed run. A graph needs durable per-node state
  that survives a process exit — especially with `awaiting_human` gates that hold for days.
- **No scheduler and no parallelism.** `AgentRuntime.run()` is one prompt, one loop, synchronous.
  AGS's default `max_parallel_nodes: 1` means sequential execution is fully conformant, so this is
  deferrable — but it is the largest structural addition when it comes.
- **No criteria evaluation.** Loro has no concept of "the harness decides whether this succeeded."
  This is the single most valuable thing AGS brings to Loro's enterprise story.

---

## 2. Target architecture

### 2.1 New package

New package `src/loro/agraph/`, reusing everything else:

```
src/loro/agraph/
├── __init__.py
├── document.py      # load JSON/YAML, duplicate-key rejection, canonical digest
├── validate.py      # AG0xx / AG1xx / AG2xx / AG9xx findings with JSON Pointers
├── expressions.py   # AGX tokenizer, parser, evaluator (never host eval)
├── plan.py          # effective edge set, topo order, worst-case count, cost, tier histogram
├── policy.py        # managed enterprise policy over graphs  <-- Loro-specific
├── schedule.py      # readiness, edge activation, skip propagation, joins
├── execute.py       # per-node lifecycle; wraps AgentRuntime
├── routing.py       # intelligence tier -> provider/model
├── criteria.py      # the nine criterion kinds
├── store.py         # durable graph run state (extends the SessionStore pattern)
├── generate.py      # goal -> graph (the second direction)
└── record.py        # AGS run record emission alongside the audit chain
```

CLI lives in a **new module**, not in `src/loro/cli.py` — that file is already 3,971 lines and is the
largest in the project. Add `src/loro/cli_graph.py` exposing a `graph_app` Typer group, registered
next to the existing `skills_app` / `mcp_app` / `policy_app` in `src/loro/cli.py` with a single
`app.add_typer(graph_app, name="graph")` line.

### 2.2 Managed AGS policy — the Loro-specific feature

Loro's configuration layering ends with **non-overridable managed overlays** re-applied after
runtime overrides (`/etc/loro/managed.toml`, `LORO_MANAGED_CONFIG`, `LORO_MANAGED_CONFIG_CONTENT`).
That mechanism, applied to submitted graphs, is something no other harness will have:

```toml
# /etc/loro/managed.toml
[agraph]
enabled = true
max_conformance_level = 3
max_nodes = 50
max_node_executions = 200
max_cost_usd = 50.0
max_tier = "advanced"                    # frontier requires an explicit exception
allow_command_criteria = false           # kind: command from third-party graphs is denied
allow_external_subgraph_refs = false     # only local ./ refs load
require_integrity_for_refs = true
require_gate_before = ["git:push", "net:fetch"]   # a gate must precede these permissions
forbidden_permissions = ["fs:write:/etc/**", "shell:exec:curl*"]
required_criteria_kinds = ["command", "file_exists", "expression", "json_schema"]
                                          # every node needs >=1 deterministic required criterion
```

A graph that violates managed policy is **rejected at validation time with a policy diagnostic**,
before execution and before any approval prompt. This turns "we let users submit agent plans" from a
risk into a controlled capability, and it directly serves the concerns in
[docs/threat-model.md](docs/threat-model.md) and
[docs/external-enterprise-requirements.md](docs/external-enterprise-requirements.md).

### 2.3 Tier routing configuration

New optional block in `ModelConfig` (or a sibling `TiersConfig`) in `src/loro/config.py`:

```toml
[model.tiers]
minimal  = { provider = "ollama",    model = "qwen2.5:7b" }
standard = { provider = "openai",    model = "<mid>" }
advanced = { provider = "anthropic", model = "<large>" }
frontier = { provider = "anthropic", model = "<flagship>" }
```

Defaults when unset: every tier resolves to `ModelConfig.model`, except `minimal`, which resolves to
`ModelConfig.small_model`. That keeps existing single-model configs working and honest — the run
record records the effective tier and `downgraded: true`, so nobody is misled about a `frontier`
node that ran on a small model.

---

## 3. Completed phases

The tables preserve the original estimates and implementation notes. Every listed phase is now
complete. Effort key: **S** ≈ 1–2 days · **M** ≈ 3–5 days · **L** ≈ 1–2 weeks.

### Phase 0 — Decide and de-risk

| # | Item | Effort | Depends on | Notes |
| --- | --- | --- | --- | --- |
| 0.1 | Decide whether graph execution is a new CLI verb (`loro graph run`) or a mode of `loro run` | S | — | Recommended: a new verb. `loro run` / `loro plan` take a prompt; a graph is a different input shape. |
| 0.2 | Design the `[model.tiers]` config block and its defaults (§2.3) | S | — | Additive to `ModelConfig`; must not break existing `.loro/config.local.toml` files. |
| 0.3 | Design the `[agraph]` managed policy block (§2.2) and its diagnostic codes | M | — | Use a Loro-specific `LP` prefix so policy findings are distinguishable from spec `AG` findings. |
| 0.4 | Fix the logical-tool-name mapping: AGS `file_read`/`file_write`/`shell_exec`/`http_fetch`/… → Loro `file.read`/`file.write`/`shell.run`/… in `src/loro/tool_runtime.py` | S | — | Also decide the AGS permission scope for Polaris and governed data. AGS scopes are fixed (`fs`, `net`, `shell`, `git`, `process`, `secret`, `mcp`, `human`, `custom`), so Polaris reads become `custom:polaris_read:<catalog>/<namespace>`. |
| 0.5 | Decide graph run state storage: extend `SessionStore` (`src/loro/sessions.py`) or add a sibling store | S | — | Recommended: a sibling `GraphRunStore` under `src/loro/agraph/store.py` following the same durable-JSON pattern, with the same `SafetyConfig` / `DataProtectionEngine` enforcement. |
| 0.6 | Write three Loro-shaped example graphs into `docs/examples/agraph/` | M | 0.2, 0.4 | Suggested: a governed-data pipeline (Polaris discovery → Iceberg draft → approval gate → commit), an enterprise brief pipeline, and a release-readiness checklist. These are Loro's actual use cases and they exercise gates and audit properly. |
| 0.7 | Threat-model addendum: graph documents as untrusted input | M | 0.3 | Add to [docs/threat-model.md](docs/threat-model.md). Spec §22 covers prompt injection via node text, command criteria as code execution, external subgraph refs, and fan-out amplification. |

**Exit:** the two mapping tables, the managed-policy design, and the threat-model addendum exist;
the three example graphs validate cleanly under the spec repo's `--strict` validator.

---

### Phase 1 — Read, validate and govern graphs (AGS conformance level 0)

| # | Item | Effort | Depends on | Notes |
| --- | --- | --- | --- | --- |
| 1.1 | `agraph/document.py`: load `.agraph.json` / `.agraph.yaml`, reject duplicate YAML keys, canonical SHA-256 digest | S | — | `pyyaml` is not currently a Loro dependency — add it, or reuse the pattern from `src/loro/skills.py`'s `_parse_skill_file` front-matter handling. Duplicate keys must raise: a duplicated node id would silently drop a node. |
| 1.2 | `agraph/validate.py` layers 1–2: JSON Schema plus cycles, dangling ids, entrypoints, joins, decision branches, fragment refs | M | 1.1 | Port from the spec repo's `tools/validate_agraph.py`. Findings as typed records with JSON Pointers, consistent with the structured decisions in `src/loro/permissions.py`. |
| 1.3 | `agraph/expressions.py`: AGX tokenizer + recursive-descent parser | M | — | Parser only in this phase. **Never `eval()`.** Loro treats every untrusted surface through `DataProtectionEngine`; expressions must be no different. |
| 1.4 | Layer 3: scope checks, predecessor rule (AG201), undeclared references (AG203), `secrets.*` prohibition (AG205) | M | 1.2, 1.3 | AG205 matters especially here: Loro's whole secret posture is that API keys live in environment variables, never in config files. |
| 1.5 | `agraph/policy.py`: evaluate the `[agraph]` managed policy block against a document | M | 0.3, 1.2 | Reuse the managed-overlay loading already in `src/loro/config.py`. Emit `LP` diagnostics. |
| 1.6 | `agraph/plan.py`: effective edge set, topological order, reachability, worst-case execution count, projected cost, tier histogram | M | 1.2 | Worst-case count multiplies `retry.max_attempts` × `loop.max_iterations` × `map.max_items` through every nesting level — the fan-out amplification guard from spec §22. |
| 1.7 | CLI: `loro graph validate <file>`, `loro graph plan <file> [--json]`, `loro graph policy explain <file>` | M | 1.5, 1.6 | `policy explain` mirrors the existing `loro policy explain` UX in `src/loro/cli.py`. Put all of it in `src/loro/cli_graph.py`. |
| 1.8 | Audit events for graph load, validation and policy decisions: `agraph.validated`, `agraph.policy_denied` | S | 1.5 | Reuse `AuditLogger` and the schema 1.0 envelope with identity and trace binding. |
| 1.9 | Tests: the spec's `conformance/invalid/` fixtures with their `# EXPECT:` codes, plus managed-policy denial cases | M | 1.5 | Into `tests/`, alongside the existing policy and permission suites. |

**Exit:** `loro graph validate` and `loro graph plan` work on all five spec examples; managed policy
can reject a graph before execution; every conformance fixture reports its expected diagnostic.
**Conformance level 0.**

---

### Phase 2 — Execute graphs sequentially (AGS conformance level 1)

Sequential execution is fully conformant — AGS defaults `max_parallel_nodes` to `1`. Loro should
take that default and stay single-threaded through this phase, which keeps identity, approvals and
the audit hash chain simple.

| # | Item | Effort | Depends on | Notes |
| --- | --- | --- | --- | --- |
| 2.1 | `[model.tiers]` config block in `src/loro/config.py` + `agraph/routing.py` | M | 0.2 | Enforce spec §11.4: never route below the requested tier unless `allow_downgrade`; refuse with `RT011` before spending; honor `min_context_tokens`; record what was chosen. |
| 2.2 | `agraph/store.py`: durable graph run state — per-node status, outputs, attempts, resumable across process exits | M | 0.5 | Follow `SessionStore`: bounded record size, `DataProtectionEngine.enforce`, safe id handling (`safe_session_id`). |
| 2.3 | `agraph/schedule.py`: edge activation table, readiness per join mode, skip propagation, deterministic tie-break | M | 1.6, 2.2 | Spec §17.3–§17.6. The tie-break (topological, then declaration order) is normative and makes sequential runs reproducible. |
| 2.4 | `agraph/execute.py`: per-node lifecycle — resolve inputs once, verify requirements, run `AgentRuntime`, collect outputs, evaluate criteria | L | 2.1, 2.3 | One `AgentRuntime.run()` per node attempt, with `session_id` threading so each node is a durable session and the graph run is their parent. Inputs resolve **once** and are reused verbatim by retries (spec §9.2). |
| 2.5 | Output contract: a new `graph.emit_output` tool in `src/loro/tool_runtime.py`, plus `path_hint` file discovery | M | 2.4 | Loro's tool loop is explicit and typed, which makes this cleaner here than in most harnesses. Register it through the same permission and audit boundary as every other tool. |
| 2.6 | Per-node budget scoping: construct a `UsageBudget` per node from `constraints`, clamped to the graph's remaining global budget | M | 2.4 | `src/loro/budgets.py` needs a graph-level accumulator above it. `BudgetExceeded.budget` maps directly onto the AGS `budget_exceeded` failure class. |
| 2.7 | `agraph/criteria.py`: `command`, `file_exists`, `artifact_present`, `human` | M | 2.4, 2.5 | `command` must run through `src/loro/sandbox.py` under the node's profile, with `timeout_seconds` applied, and be denied outright when managed policy sets `allow_command_criteria = false`. |
| 2.8 | Retry with feedback: `retry.max_attempts`, `retry_on` class matching, `feedback: failed_criteria` injecting each failed criterion's `description` **and** its evidence | M | 2.7 | Failure classes map onto existing errors: `BudgetExceeded` → `budget_exceeded`, `ModelProviderError` → `model_error`, tool errors → `tool_error`. |
| 2.9 | `gate` nodes and `human[]` checkpoints at `before_start` / `after_outputs`, on top of `ApprovalManager` | M | 2.4 | The highest-value mapping in this document. An AGS gate becomes an identity-bound `ApprovalRequest` with `gate.roles` checked against `IdentityContext.roles`, canonical-argument binding, expiry, and replay protection. If a checkpoint cannot be presented, fail with `RT015` — never skip. |
| 2.10 | `requirements` enforcement: intersect `permissions` with `PermissionEngine` decisions and the normalized resources in `src/loro/resources.py`; block on unavailable non-optional tools (`RT012`) | M | 0.4, 2.4 | Intersect, never union. The graph asks; managed policy decides. |
| 2.11 | CLI: `loro graph run <file> [--dry-run] [--yes]`, `loro graph status <run-id>`, `loro graph resume <run-id>` | M | 2.4 | `--yes` must remain gated by the existing managed non-interactive-automation policy. |
| 2.12 | Reject graphs above the supported conformance level with `AG303`, naming the missing features | S | 1.2 | A level 1 harness rejects rather than degrades (spec §19). |
| 2.13 | Audit events per node: `agraph.node_started`, `agraph.node_completed`, `agraph.criteria_evaluated`, `agraph.gate_decided` | M | 2.9 | Same envelope, identity and trace id as `runtime.task_started`. One trace id per graph run, node id as a field. |
| 2.14 | Tests: execute `examples/minimal.agraph.yaml` end to end; assert scheduling determinism; assert the `RT011` routing refusal; assert audit chain integrity across a multi-node run | M | 2.11 | |

**Exit:** `loro graph run examples/minimal.agraph.yaml` completes with criteria checked by the
harness, every node audited, and gates enforced against identity. **Conformance level 1.**

---

### Phase 3 — Branching, budgets, isolation (AGS conformance level 2)

| # | Item | Effort | Depends on | Notes |
| --- | --- | --- | --- | --- |
| 3.1 | AGX **evaluator** on the Phase 1 parser: strict typing, the fixed function set | M | 1.3 | No coercion; a type-mismatched comparison is an evaluation error, per spec. |
| 3.2 | `decision` nodes: constrain the answer to declared labels, `default_branch` + `RT022` fallback, `evaluator: expression` with no model call | M | 3.1, 2.4 | Reuse `src/loro/model_tools.py` structured-output normalization. |
| 3.3 | `conditional` and `on_failure` edges, node-level `when`, `join: any` / `n_of` | M | 3.1, 2.3 | |
| 3.4 | Criterion kinds `expression`, `regex`, `json_schema` | S | 3.1 | |
| 3.5 | Full `constraints` enforcement including `max_wall_clock_seconds`, `temperature`, `seed`, `determinism` | M | 2.6 | `determinism: strict` requires a seed and temperature 0; if the routed provider cannot honor it, fail with `RT013` rather than pretending. |
| 3.6 | `constraints.isolation` → `src/loro/sandbox.py` profiles (`shared`→process, `worktree`→a new git-worktree profile, `sandbox`/`container`→Bubblewrap-enforced profiles); fail rather than downgrade (`RT014`) | M | 2.7 | Loro's `require_os_enforcement` flag is exactly the "fail rather than silently downgrade" semantics AGS wants. |
| 3.7 | `failure.fallback` (all five strategies) and `failure.escalation` | M | 2.8 | `escalation.to: human` routes through `SessionMailbox` (`src/loro/session_messages.py`) so escalations survive a process exit. |
| 3.8 | `human` checkpoints at `before_side_effects`: pause at the first mutating tool call | M | 2.9 | Loro already classifies actions by normalized resource and approval requirement in `src/loro/tool_runtime.py`, so the classifier mostly exists. |
| 3.9 | `policy` switches: `on_expression_error`, `on_node_failure`, `on_unknown_field`, human timeouts, `checkpointing`, `resume` | S | 3.1 | Managed overlays should be able to pin these — for example forcing `on_human_timeout` away from `approve`. |
| 3.10 | Managed-policy enforcement at run time as well as validation time: re-check `max_tier`, `forbidden_permissions` and `require_gate_before` at node start | M | 1.5, 2.10 | Validation-time checks can be bypassed by a resumed run against an edited document; the run-time re-check closes that. Pairs with the digest guard in 4.8. |

**Exit:** the canonical `library-v1-release` example runs, takes the correct branch, enforces
budgets, and honors isolation. **Conformance level 2.**

---

### Phase 4 — Composition, judges, run records (AGS conformance level 3)

| # | Item | Effort | Depends on | Notes |
| --- | --- | --- | --- | --- |
| 4.1 | `loop` nodes: `while`/`until`/`repeat`, `max_iterations`, `carry`, `collect`, `on_max_iterations` | M | 3.3 | Recursive use of the scheduler over the body fragment; do not inline fragment nodes into the parent scope. |
| 4.2 | `map` nodes with `max_items`, `max_parallel`, `on_item_failure`, order-preserving `collect` | M | 4.1, 4.4 | Needs the parallel executor (4.4) to be more than sequential fan-out; `max_parallel: 1` is valid in the meantime. |
| 4.3 | `subgraph` nodes: `use`, `inline`, `ref` with `integrity` and `expected_id` verification; managed policy can forbid non-local refs | M | 4.1, 1.5 | External refs pull in executable content — treat as a supply-chain surface and cover in [docs/security-supply-chain.md](docs/security-supply-chain.md). |
| 4.4 | Parallel node execution: `constraints.max_parallel_nodes`, `concurrency_group` | L | 3.6 | The largest structural change in this roadmap. `AgentRuntime` is synchronous today; parallel nodes need per-node runtime instances, thread- or process-safe audit writes (the JSONL sink already uses process locking), and careful `UsageBudget` aggregation. Consider deferring until there is user demand — sequential is conformant. |
| 4.5 | `llm_judge` criteria: route by `judge_intelligence`, `samples` with median, evidence capture | M | 3.1, 2.7 | Managed policy should be able to require at least one deterministic criterion alongside any judge (`required_criteria_kinds` in §2.2). |
| 4.6 | `external` criteria registry | S | 4.5 | Natural home: a governed registry gated by managed config, in keeping with how `src/loro/mcp/extensions.py` keeps unknown extensions inert. |
| 4.7 | `failure.compensation` — reverse-order undo on a failed run | M | 3.7 | Especially relevant for governed-data graphs where a node may have staged an Iceberg draft. |
| 4.8 | AGS run records conforming to `agentic-graph-run-1.0.schema.json`, emitted alongside the audit chain | M | 2.13 | The audit JSONL stays the tamper-evident record; the run record is the portable summary. Populate `attempt.routed` including `downgraded`. Run every record through `DataProtectionEngine` and honor `redact: true`. |
| 4.9 | Resume with digest guard: refuse when `graph_digest` changed (`RT053`) unless explicitly forced, and audit the force | S | 4.8, 2.2 | |
| 4.10 | Shared-memory integration: record graph outcomes as citable memories through `src/loro/memory/operations.py`, explicit-write only | M | 4.8 | Consistent with Loro's rule that shared memory writes stay explicit and user-approved. |

**Exit:** all five spec examples execute. **Conformance level 3.**

---

### Phase 5 — Generating graphs (the second direction)

| # | Item | Effort | Depends on | Notes |
| --- | --- | --- | --- | --- |
| 5.1 | `agraph/generate.py` skeleton pass: goal + workspace context → nodes, `depends_on`, `outputs`, generated against the JSON Schema | L | 1.4, 2.1 | `loro plan` already exists as a read-only planning mode in `src/loro/cli.py`; this is its structured successor. Feed it `file.search` results and recalled memories. |
| 5.2 | Specification pass, **per node**: `success.criteria`, `intelligence`, `requirements`, `constraints`, `failure`, `estimate` | L | 5.1 | Two passes are not optional — single-pass generation reliably produces criteria like "the work is complete". |
| 5.3 | Validate → repair loop against both spec findings and managed policy | M | 5.2, 1.5 | Generating *inside* managed policy is a real advantage: the generator cannot propose a graph the organization would reject. |
| 5.4 | Tier calibration: require `rationale` for `advanced`/`frontier`; clamp to managed `max_tier` | S | 5.2 | |
| 5.5 | CLI: `loro graph generate "<goal>" [--out plan.agraph.yaml]`, and `loro plan --format agraph` | M | 5.3 | Making `loro plan` able to emit a graph is the smallest change with the largest visible payoff. |
| 5.6 | Approve-then-run: render the plan, let the user edit, re-validate, require an `ApprovalManager` approval of the **graph digest**, then run | M | 5.5, 2.11 | Approving a specific digest is the enterprise-grade version of "approve the plan": the approval is void if the document changes. |
| 5.7 | Governed-data graph templates: Polaris discovery → transformation → shared-memory draft → approval → commit | M | 5.2, 4.7 | Loro's most differentiated use case. Pairs with [docs/polaris-iceberg.md](docs/polaris-iceberg.md). |

**Exit:** `loro graph generate` produces a policy-compliant, strictly-valid graph a reviewer can
approve by digest.

---

### Phase 6 — Ecosystem and evidence

| # | Item | Effort | Depends on | Notes |
| --- | --- | --- | --- | --- |
| 6.1 | Ship an AGS **Agent Skill** package following the spec's `docs/skill-authoring.md`, installable through `loro skills install` | M | 1.7, 5.5 | `SkillRegistry` in `src/loro/skills.py` already enforces provenance, digests, size limits and a proposal/review lifecycle — an unusually good home for a skill that writes executable plans. Keep `allow_scripts: false` compatible: the skill must be useful without running scripts. |
| 6.2 | MCP server exposure: publish read-only `agraph.validate` and `agraph.plan` tools through `src/loro/mcp/server.py` | M | 1.7 | Lets other agents validate graphs against Loro's managed policy without executing anything. Fits the existing hard-coded read-only tool ceiling. |
| 6.3 | Docs: `docs/agentic-graphs.md` (concepts, CLI, policy) and `docs/agraph-policy.md` (the managed block) | M | 1.7, 2.11 | Follow the existing docs conventions in `docs/policy.md` and `docs/skills.md`. |
| 6.4 | Enterprise evidence: add graph acceptance, policy denial, gate enforcement and audit continuity to `docs/enterprise-evidence.md` | M | 2.13 | Directly serves the enterprise-readiness track already underway. |
| 6.5 | Publish Loro's conformance level and `supported_features` in `loro doctor` output | S | any level claim | Feeds the run record's `harness` block. |
| 6.6 | CI: run the spec repo's conformance fixtures in `.github/workflows` | S | 1.9 | Keeps Loro honest as AGS moves. |

---

## 4. Original implementation sequence

This completed sequence is retained to explain how the implementation was staged.

1. **0.2 + 0.4 — The two mapping tables** (S + S).
   `[model.tiers]` config design, and AGS logical tool names → Loro's `file.read` / `shell.run` /
   `artifact.create` registry. Cheap, unblocks everything, and forces the one decision people will
   argue about (which model is `frontier`).

2. **1.1 + 1.2 — Loader and validation layers 1–2** (S + M).
   `agraph/document.py` and `agraph/validate.py`, ported from the spec repo's
   `tools/validate_agraph.py`. At the end you can tell a user their graph is wrong and exactly
   where.

3. **1.5 — Managed `[agraph]` policy** (M).
   Do this *early*, not late. It is Loro's differentiator, it is small on top of the existing
   overlay loader, and building it before execution means the governance story is true from the
   first release rather than retrofitted.

4. **1.6 + 1.7 — Plan and render** (M + M).
   `loro graph plan` and `loro graph policy explain`. **Ship this.** Conformance level 0, useful on
   its own, and it demonstrates the "approve the plan" pitch before any executor exists.

5. **2.9 — Gates on `ApprovalManager`** (M), with the minimum of 2.1–2.7 needed to reach it.
   Identity-bound, expiring, replay-protected approvals as AGS gates is the single most
   differentiated thing Loro can build here, and it is the demo that will sell the feature.

Parallelism (4.4), loops, maps, subgraphs, and generation followed after the first five and are now
implemented.

---

## 5. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| **`src/loro/cli.py` grows further.** Already 3,971 lines, the largest module in the project. | Medium. | All graph commands in `src/loro/cli_graph.py`; one `add_typer` line in `cli.py`. No exceptions. |
| **Parallelism breaks audit and budget invariants.** Concurrent nodes writing the hash-chained JSONL and sharing a `UsageBudget`. | High if attempted early. | Defer 4.4. Sequential is conformant. When it lands: per-node `UsageBudget` with a locked graph-level accumulator, and rely on the existing JSONL process locking. |
| **Graph documents are untrusted input.** `description`, `instructions` and `rubric` go straight in front of a model; `command` criteria execute shell; `subgraph.ref` fetches code. | High. This is the central new attack surface. | The whole point of 0.7 and 1.5. Managed policy defaults to `allow_command_criteria = false` and `allow_external_subgraph_refs = false`. `requirements` are a request, never a grant — intersect with `PermissionEngine`. Spec §22. |
| **Approval semantics drift.** An AGS gate that is not identity-bound would be weaker than Loro's existing approvals. | High — it would undercut the product's core claim. | Implement gates *as* `ApprovalRequest`s from the start (2.9). Never build a parallel approval path. |
| **Validation-time policy bypassed on resume.** A user edits an approved document and resumes. | Medium. | Digest guard (4.9) plus run-time policy re-check (3.11), and approve-by-digest in 5.6. |
| **No durable state means gates cannot hold.** `awaiting_human` with `on_timeout: hold` needs to survive a process exit. | High — day-scale approvals are the enterprise norm. | Item 2.2 is not optional. Build `GraphRunStore` before gates. |
| **Single-model configs silently mislabel tiers.** A `frontier` node running on the only configured model. | Medium. | Default all tiers to `ModelConfig.model`, record `downgraded: true` and the effective tier in every run record, and surface it in `loro graph plan`. |
| **Generation produces judged-only criteria**, which are unverifiable and unauditable. | High for an enterprise product. | `required_criteria_kinds` in managed policy (§2.2), plus the `--strict` repair loop (5.3) and advisory `AG906`. |
| **Spec churn.** AGS 1.0 is a draft standard. | Low. | Pin `ags_version: "1.0"`; MINOR releases are additive by policy (spec §21). CI against the spec's fixtures (6.6). |

---

## 6. Definition of done

Loro can claim AGS support when all of the following hold:

- `loro graph validate` reproduces every diagnostic in the spec's `conformance/invalid/` fixtures.
- `loro graph policy explain` shows exactly which managed rules a graph violates, before execution.
- `loro graph plan` renders any valid document without executing anything.
- `loro graph run` executes all five spec examples, with criteria checked by the harness rather than
  asserted by the model.
- Every graph gate is an identity-bound `ApprovalRequest` with role checking, expiry and replay
  protection, and an unpresentable required checkpoint fails the node rather than being skipped.
- Every node execution emits audit events on the existing hash-chained schema 1.0 envelope, and the
  chain verifies across a full multi-node run.
- Every run emits a record conforming to `agentic-graph-run-1.0.schema.json`, redacted through
  `DataProtectionEngine`, with `attempt.routed` recording the effective tier and any downgrade.
- `loro graph generate` produces documents that pass both `--strict` validation and managed policy.
- `loro doctor` reports the conformance level and feature list.
- `docs/enterprise-evidence.md` covers graph acceptance, policy denial, gate enforcement and audit
  continuity.
