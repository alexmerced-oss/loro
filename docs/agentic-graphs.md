# Agentic Graphs

Loro implements Agentic Graph Specification (AGS) 1.0 conformance level 3. An Agentic Graph is a
portable JSON or YAML execution plan whose scheduling, bounds, success checks, and approval points
are enforced by the harness rather than left to the model.

Release 0.17.0 uses `agentic-graph-spec>=1.0.1,<2` and pins upstream commit
`f180a4dbd07911f90dd0821f531d7ccd51bb0764` in CI. Static semantics, RFC 8785 graph digests, and
the portable run-record schema come from that support library. The bundled authoring Skill and
packaged schema mirrors match the same revision. See the
[machine-readable Level 3 result](ags-conformance.json) for the exact claim and fixture revision.

## Quick Start

```bash
loro graph generate "Create a release readiness report" --out release.agraph.yaml
loro graph validate release.agraph.yaml --strict
loro graph plan release.agraph.yaml --json
loro graph run release.agraph.yaml --dry-run
loro graph run release.agraph.yaml --params '{"release":"0.3.0"}'
loro graph run release.agraph.yaml --yes \
  --remember-outcome "Release 0.3.0 passed the approved readiness graph."
loro graph status RUN_ID
loro graph resume RUN_ID
```

`loro plan GOAL --format agraph --out FILE` is an equivalent generation entry point. Both commands
ask the configured model to author a typed workflow of concrete steps and outputs. Loro then
compiles that substance into exact AGS structure with deterministic bounds, retries, typed outputs,
success criteria, estimates, dependencies, canonical logical-tool requirements, and matching
portable permissions before validating every static layer and applying
managed graph policy. Invalid workflow output receives one correction attempt; no graph remains on
failure. A `mock` provider fails with instructions to run `loro configure` rather than silently
writing a generic one-node graph. Use `--no-ai` only when an explicit deterministic one-node
skeleton is useful. That skeleton conservatively infers its declared tools, portable permissions,
and workspace mode from the goal, including governed network access for research and write access
for implementation work. The bundled `skills/agentic-graph` package supplies an authoring workflow,
schema, expression reference, and starter template.

The authoring prompt publishes Loro's exact logical-tool catalog and refuses invented names such as
`net_fetch`. External research uses `shell_exec` because Loro's governed curl/wget path is its web
fetch backend; the compiler pairs that capability with `shell:exec:*` and `net:fetch:*`. Older
three-field workflow drafts remain compatible through conservative per-step capability inference.

Inspect and install the exact Skill shipped in the wheel through the normal digest review flow:

```bash
loro graph skill-path
loro skills validate "$(loro graph skill-path)"
loro skills install "$(loro graph skill-path)" --expected-digest sha256:REVIEWED_DIGEST
```

## Execution Model

Loro validates all three static layers, applies managed policy, computes effective edges and
worst-case fan-out, and only then creates a durable run. Task nodes each receive a separately
bounded `AgentRuntime`. The model emits declared values through `graph.emit_output`; file and
artifact outputs may also be discovered through `path_hint`. The harness, not the model, evaluates
acceptance criteria.

Supported node types are `task`, `decision`, `gate`, `loop`, `map`, and `subgraph`. Ready nodes may
run concurrently up to the smaller of the graph and managed ceilings. Scheduling and record merge
order remain deterministic. Subgraphs may be inline, reusable fragments, or local references with
digest and expected-id checks. External references are denied by default and this runtime does not
retrieve remote graph documents.

Criteria include command, file existence, artifact presence, JSON Schema, regex, expression, LLM
judge, human, and registered external checks. Command checks run through a named Loro sandbox and
are denied by managed policy by default. External checks must be both allowlisted and registered.
LLM judges require structured scores and record median evidence.

Retries reuse their resolved input snapshot and can include failed-criterion evidence. Ordered
fallbacks support skipping, degraded outputs, human takeover, and alternate nodes. Loops and maps
have mandatory hard bounds. When a run fails, declared compensations execute in reverse
topological order.

## Gates And Resume

A gate without an available trusted approver moves the run to `awaiting_human` and persists it
under `[agraph].state_path`. Resume reloads the original source and refuses a changed canonical
digest unless `--force` is supplied. `--yes` is accepted only when managed approval configuration
allows non-interactive approval. Role-restricted gates require the active identity to carry an
allowed role.

Before any non-dry run, Loro renders the node and worst-case execution counts and asks the user to
approve the exact canonical digest. That approval is identity, session, policy, and argument bound
through `ApprovalManager`; changing the document voids it. `--remember-outcome` never writes shared
memory directly. It creates a proposal containing only the user's explicit text, which must still
pass the normal shared-memory review and commit flow.

Run records conform to the support library's `agentic-graph-run-1.0.schema.json`. Full task transcripts remain in Loro
session records; graph records contain references, outputs, criteria evidence, routing, usage, and
human outcomes. The safety policy for session persistence applies before atomic record writes.

## Examples

- [Enterprise brief](examples/agraph/enterprise-brief.agraph.yaml)
- [Governed data summary](examples/agraph/governed-data.agraph.yaml)
- [Release readiness](examples/agraph/release-readiness.agraph.yaml)

Read [Agentic Graph Policy](agraph-policy.md) before enabling submitted graphs for a team.
