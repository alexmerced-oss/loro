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
allow_external_criteria = false
allow_external_subgraph_refs = false
require_integrity_for_refs = true
require_gate_before = ["git:push:*", "net:fetch:*"]
forbidden_permissions = ["fs:write:/etc/**", "shell:exec:curl*"]
required_criteria_kinds = ["file_exists", "expression", "json_schema"]
allow_generation = true
```

Loro policy diagnostics use `LP001` through `LP010`; unsupported conformance uses `AG303` and
routing refusal uses `RT011`. `loro graph policy explain FILE` reports exact JSON pointers before
execution. The canonical graph digest binds resume to reviewed content.

Command criteria are executable code and should remain disabled for third-party graphs. If an
organization enables them, use a Bubblewrap-backed shell profile with network denial, narrow
writable roots, executable allowlists, and short output/time limits. External criteria require
both `allow_external_criteria = true`, a name in `external_criteria`, and a checker registered by
the embedding application. Remote subgraph retrieval is intentionally unsupported by the CLI;
mirror reviewed dependencies locally and pin their integrity digest.

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
