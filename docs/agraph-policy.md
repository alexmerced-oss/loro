# Agentic Graph Policy

Graph documents are untrusted input. Their declared tools and permissions are requests and can
only narrow Loro's configured policy; they never grant authority. Managed configuration is applied
after user, project, environment, and runtime layers, so graph authors cannot override it.

```toml
[agraph]
enabled = true
conformance_level = 3
state_path = ".loro/graph-runs"
max_document_bytes = 5000000
max_record_bytes = 20000000
max_nodes = 100
max_node_executions = 1000
max_cost_usd = 25.0
max_tier = "advanced"
max_parallel_nodes = 4
allow_command_criteria = false
allow_unknown_executors = false
allow_external_criteria = false
allow_external_subgraph_refs = false
require_integrity_for_refs = true
require_gate_before = ["git:push:*", "net:fetch:*"]
forbidden_permissions = ["fs:write:/etc/**", "shell:exec:curl*"]
required_criteria_kinds = ["file_exists", "expression", "json_schema"]
allow_generation = true
```

Loro policy diagnostics use `LP001` through `LP011`; unsupported conformance uses `AG303` and
routing refusal uses `RT011`. `loro graph policy explain FILE` reports exact JSON pointers before
execution. The canonical graph digest binds resume to reviewed content.

## Command criteria are off by default

Loro's default policy rejects any graph with a `command` success criterion (`LP006`). That
includes several of the AGS specification's own examples: `minimal.agraph.yaml`,
`library-v1-release.agraph.yaml` (and `.json`) and `docs-site-refresh.agraph.yaml`. `loro graph
validate`, `plan` and `run` report `LP006` for them with the setting to change:

```toml
[agraph]
allow_command_criteria = true
```

Put it in `.loro/config.local.toml` for one project, or in managed configuration for an
organization; managed configuration wins, so a project cannot enable what an administrator
disabled. Each check runs its command in the sandbox profile named by `sandbox.shell_profile`,
with that profile's executable allowlist, environment allowlist and limits.

Command criteria are executable code and should remain disabled for third-party graphs. If an
organization enables them, use a Bubblewrap-backed shell profile with network denial, narrow
writable roots, executable allowlists, and short output/time limits. External criteria require
both `allow_external_criteria = true`, a name in `external_criteria`, and a checker registered by
the embedding application. Remote subgraph retrieval is intentionally unsupported by the CLI;
mirror reviewed dependencies locally and pin their integrity digest.

## Executor extensions

AGS lets a harness ignore `x-` members, but an extension must not change what a node does
(SPEC section 23). An executor extension such as MagAgent's `x-magagent-executor` (a node that
calls an MCP tool or an A2A agent instead of a model) does exactly that. Loro implements no
executor extensions, and before 0.22 it ran such nodes as ordinary model tasks, so the graph
"succeeded" without doing what its author meant.

Loro now refuses a graph with a node-level `x-executor` or `x-<vendor>-executor` member it does not
implement. `validate`, `plan`, `policy explain` and `run` report `LP011` with the node id and the
extension name. Other `x-` members, such as `x-agent-profile`, are unaffected. To run those nodes
as model tasks anyway, pass `--allow-unknown-executors` to `loro graph validate`, `plan`, `run`,
`resume` or `policy explain`, or set:

```toml
[agraph]
allow_unknown_executors = true
```

`LP011` then becomes a warning, printed before the run and kept in the run record's
`diagnostics`. `loro graph validate --strict` still fails on it.

## Model tiers

Model tiers are configured under `[model.tiers]`:

```toml
[model.tiers.minimal]
provider = "ollama"
model = "qwen2.5:7b"
context_tokens = 32768

[model.tiers.advanced]
provider = "anthropic"
model = "claude-sonnet-5"
context_tokens = 200000
api_key_env = "ANTHROPIC_API_KEY" # pragma: allowlist secret
```

Routing refuses a lower tier or insufficient context unless the node explicitly permits a
downgrade. Effective provider, model, tier, downgrade state, criteria evidence, and usage are
written to the run record.
