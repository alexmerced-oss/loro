# Configuration

The root configuration schema is `1.0`:

```toml
schema_version = "1.0"
```

Configuration written before Loro `0.5.0` was unversioned. The loader treats that exact legacy
shape as schema `1.0`, while every file written by current setup commands receives the root
version. Unknown future versions fail closed instead of being partially interpreted. Back up a
configuration before rewriting it with a newer Loro release.

Loro uses TOML configuration and merges layers in increasing precedence:

1. `/etc/loro/config.toml`
2. `~/.config/loro/config.toml`
3. `.loro/config.toml`
4. `.loro/config.local.toml`
5. `LORO_CONFIG`
6. `LORO_CONFIG_CONTENT`
7. Managed overlays: `/etc/loro/managed.toml`, `LORO_MANAGED_CONFIG`, and `LORO_MANAGED_CONFIG_CONTENT`

Managed overlays are applied last so enterprise policy can be non-overridable by user,
project, local, or runtime config.

## Example

```toml
schema_version = "1.0"

[model]
provider = "mock"
model = "mock-agent"
small_model = "mock-small"
timeout_seconds = 120
max_retries = 2
backoff_seconds = 0.25
verify_tls = true
# ca_bundle_env = "LORO_CA_BUNDLE"
# proxy_env = "LORO_HTTPS_PROXY"
temperature = 0.2
input_cost_per_million = 0
output_cost_per_million = 0

[runtime]
max_steps = 5
max_tool_calls = 50
max_model_input_bytes = 2000000
max_model_output_bytes = 1000000
# max_input_tokens = 100000
# max_output_tokens = 20000
# max_cost_usd = 5.00

[sandbox]
enabled = true
shell_profile = "controlled-shell"
git_profile = "git"
governed_data_profile = "governed-data"
mcp_stdio_profile = "mcp-stdio"
skill_profile = "skill-script"

[sandbox.profiles.controlled-shell]
backend = "process"
require_os_enforcement = false
network = "inherit"
allowed_executables = ["*"]
environment_allowlist = ["PATH", "LANG", "LC_ALL", "TMPDIR"]
writable_roots = []
max_seconds = 120
max_output_bytes = 1000000

[identity]
environment_enabled = true
environment_prefix = "LORO_IDENTITY_"
required_fields = []

[approvals]
interactive = true
allow_non_interactive = true
allow_session_scope = true
once_ttl_seconds = 300
session_ttl_seconds = 900
store = "memory"
store_path = "~/.local/state/loro/approvals.json"
max_store_bytes = 10000000

[permissions]
version = "local-v1"
default = "ask"
shell = "ask"
edit = "ask"
shared_memory = "ask"
governed_data = "allow"
mcp = "ask"
skills = "ask"
session_message = "ask"
web = "deny"
workspace_roots = []

[[permissions.rules]]
tool = "edit"
action = "read*"
target = "docs/*"
decision = "allow"
reason = "Project docs are readable."

[[permissions.rules]]
tool = "shell"
action = "run*"
target = "rm *"
decision = "deny"
reason = "Destructive delete commands are blocked."

[memory.local]
enabled = true
path = ".loro/memory"
auto_propose = true

[memory.shared]
enabled = false
backend = "postgres"
tenant_isolation = "disabled"
write_policy = "explicit_user_dictation_only"
read_policy = "semantic_retrieval_with_citations"
postgres_dsn_env = "LORO_POSTGRES_DSN"
postgres_schema = "public"
iceberg_namespace = "agent_memory"
iceberg_table = "shared_memories"
retention_days = 365

[polaris]
enabled = false
cli_path = "polaris"
require_role_inspection = true

[mcp]
enabled = false
require_https = false
allow_loopback_http = true
block_private_networks = false
follow_redirects = false
max_output_bytes = 1000000
max_pagination_pages = 20
allow_input_required = false
input_required_max_rounds = 3

[mcp.servers.example]
enabled = true
transport = "stdio"
command = "example-mcp-server"
args = []
env_allowlist = []
protocol_mode = "auto"
allowed_protocol_versions = ["2026-07-28", "2025-11-25", "2024-11-05"]
timeout_seconds = 30

[mcp.server]
enabled = false
transport = "stdio"
host = "127.0.0.1"
port = 8766
export_tools = []
export_resources = true
export_prompts = true

[audit]
enabled = true
schema_version = "1.0"
sink = "jsonl"
path = ".loro/audit.jsonl"
include_prompt_preview = true
failure_mode = "warn"
buffer_path = ".loro/audit-buffer.jsonl"
max_buffer_events = 1000
max_retries = 2
backoff_seconds = 0.25
timeout_seconds = 10

[sessions]
path = ".loro/sessions"
message_path = ".loro/session-messages"
max_message_bytes = 100000

[skills]
enabled = true
managed_paths = ["/etc/loro/skills"]
user_paths = ["~/.config/loro/skills"]
project_paths = [".loro/skills"]
allow_user = true
allow_project = true
allow_scripts = false

[safety]
enabled = true
default_classification = "internal"
redaction_text = "[redacted]"
allow_sensitive_override = true

[safety.surfaces.model_input]
action = "block"
maximum_classification = "confidential"

[safety.surfaces.tool_output]
action = "redact"
maximum_classification = "confidential"
allowed_finding_kinds = ["internal_case_id"]

[[safety.custom_patterns]]
kind = "internal_case_id"
pattern = "CASE-[0-9]{5}"
classification = "confidential"
```

## Agentic Graphs And Model Tiers

`[agraph]` controls AGS conformance, durable state, document/record/node/execution/cost/tier and
parallelism ceilings, criterion policy, permission/gate rules, reference integrity, and generation.
`[model.tiers.minimal|standard|advanced|frontier]` maps logical intelligence demand to a provider,
model, optional context window, API-key environment variable, and base URL. Existing single-model
configuration remains valid; minimal falls back to `small_model` and other tiers to `model`.

See [Agentic Graph Policy](agraph-policy.md) for a complete managed example. The highest-precedence
managed overlay should own enterprise graph ceilings and checker/reference policy.

## Runtime Overrides

```bash
LORO_CONFIG=/path/to/config.toml loro doctor
LORO_CONFIG_CONTENT='[permissions]\nshell = "allow"\n' loro doctor
```

## Managed Enterprise Overlays

Managed overlays use the same TOML shape as normal configuration, but they are merged after
all other layers. Use them for enterprise guardrails such as permission denies, audit
requirements, required identity fields, shared-memory backend settings, and Polaris defaults.

```toml
# /etc/loro/managed.toml
[permissions]
shell = "deny"
web = "deny"

[identity]
required_fields = ["subject", "organization", "tenant", "auth_method", "source"]

[approvals]
interactive = true
allow_non_interactive = false
allow_session_scope = true

[sandbox.profiles.controlled-shell]
backend = "bubblewrap"
require_os_enforcement = true
network = "deny"
allowed_executables = ["/usr/bin/git", "/usr/bin/python3"]
environment_allowlist = ["PATH", "LANG"]
writable_roots = ["/work/repos/approved"]

[[permissions.rules]]
tool = "git"
action = "commit"
target = "*"
decision = "ask"
reason = "Enterprise policy requires explicit commit approval."

[audit]
enabled = true
include_prompt_preview = false
schema_version = "1.0"
sink = "http"
http_url = "https://audit.example.internal/v1/loro/events"
http_token_env = "LORO_AUDIT_TOKEN"
failure_mode = "fail"
buffer_path = "/var/lib/loro/audit-buffer.jsonl"
max_buffer_events = 1000

[memory.shared]
enabled = true
write_policy = "explicit_user_dictation_only"
tenant_isolation = "identity"

[safety]
allow_sensitive_override = false

[safety.surfaces.model_input]
action = "block"
maximum_classification = "internal"
```

For test, container, or centrally launched desktop environments:

```bash
LORO_MANAGED_CONFIG=/opt/loro/managed.toml loro doctor
LORO_MANAGED_CONFIG_CONTENT='[permissions]\nshell = "deny"\n' loro doctor
```

Normal runtime overrides cannot loosen values supplied by managed overlays because managed
TOML is applied last.

To make managed policy mandatory and digest-pinned, set:

```bash
export LORO_MANAGED_CONFIG_REQUIRED=true
export LORO_MANAGED_CONFIG=/opt/loro/managed.toml
export LORO_MANAGED_CONFIG_SHA256=sha256:<aggregate-digest>
```

The digest covers each managed source label and its exact bytes in merge order. Loro verifies
the digest before parsing or applying policy and fails closed on a missing source, malformed
TOML, or mismatch. Distribution, key management, rotation, and approval of that digest remain
enterprise configuration-management responsibilities.

## Provider Transport And Budgets

Provider calls retry timeouts, connection failures, HTTP 429, and HTTP 5xx responses with
bounded exponential backoff. Other HTTP 4xx responses and malformed payloads fail immediately.
`ca_bundle_env` and `proxy_env` name environment variables; they never store the CA path or proxy
credential in TOML. Keep `verify_tls = true` in managed deployments.

Runtime usage records provider-reported token counts where available and otherwise uses a
conservative character estimate. Cost enforcement is active only when the configured model has
nonzero per-million input/output prices. The limits are per Loro task; organization-wide and
concurrent tenant quotas require a shared model gateway or another distributed coordinator.

See [Managed Data Protection](data-protection.md) for surface defaults, decision semantics, and
the remaining enterprise integration requirements.

## Permission Rules

Rules are evaluated before the per-tool defaults. Legacy rules use simple case-insensitive glob
matching over `tool`, `action`, and `target`. Structured rules additionally match
`resource_kind` and every field under `permissions.rules.resource`; the first matching rule
wins. Filesystem path fields are case-sensitive on POSIX.

```toml
[permissions]
version = "enterprise-42"
workspace_roots = ["/work/repos"]

[[permissions.rules]]
tool = "shell"
action = "run*"
resource_kind = "shell"
decision = "deny"

[permissions.rules.resource]
resolved_executable_name = "python*"
```

Common tool names today are `edit`, `git`, `shell`, `shared_memory`, `governed_data`, `mcp`,
`skills`, `session_message`, `provider`, and `web`. See [Normalized Resource Policy](policy.md)
for fields, workspace-root
behavior, policy explanation, and security boundaries.

## Setup Wizards

Use setup wizards to create or update `.loro/config.local.toml` without hand-writing TOML:

```bash
loro configure
loro configure --provider openai --model gpt-5.6-luna --small-model gpt-5.4-mini
loro setup provider
loro setup identity
loro setup approvals
loro setup audit
loro setup memory
loro setup shared-memory
loro setup polaris
loro setup mcp
loro setup mcp-server
loro setup gateway
loro setup skills
loro setup quickstart
```

`loro configure` and `loro setup provider` configure the AI provider. `loro setup identity`
configures local or enterprise-provided identity fields and fail-closed requirements. `loro
setup approvals` configures interactive prompts, non-interactive automation, exact session
reuse, and expiration. `loro setup memory` configures private local memory. `loro setup shared-memory` configures
explicit-only shared enterprise memory with either Postgres or Iceberg. `loro setup polaris`
configures governed data discovery through the Polaris CLI. `loro setup mcp` configures one
stdio or Streamable HTTP MCP server without storing environment-secret values and can attach the
experimental Tasks extension. `loro setup audit` configures local
JSONL or external HTTP delivery, retry, buffering, and failure behavior. `loro setup mcp-server`
configures an explicit read-only export surface, and `loro setup skills` controls skill scope and
script policy. `loro setup quickstart` configures the original core setup areas; run the two
extension setup commands separately when those capabilities are required.

All setup commands preserve existing sections in the target config file. Secret values belong in
environment variables or the [Credential Vault](credentials.md); local TOML stores only vault
references. `loro setup gateway` configures one signed endpoint and trusted identity mapping.

## Credential Vault And Named Provider Accounts

```toml
[model]
provider = "openai"
model = "gpt-5.6-luna"
api_key_env = "OPENAI_API_KEY" # pragma: allowlist secret
credential_ref = "vault://provider/openai/work-api-key"
```

The environment value wins when present; otherwise Loro resolves `credential_ref` through the
operating-system keyring. This lets projects select different accounts for one provider without
placing keys in TOML. See [Credential Vault](credentials.md).

## Channel Gateways

```toml
[gateway]
enabled = true
host = "127.0.0.1"
port = 8765
state_path = ".loro/gateway-state.json"
max_body_bytes = 1000000
max_pending_tasks = 32
max_workers = 4
```

Endpoint-specific routes, credential references, workspace/channel allowlists, and platform-user
identity mappings live under `[gateway.endpoints.ID]`. See [Channel Gateways](channel-gateways.md).

See [Identity Context](identity.md) for identity precedence, supported environment variables,
managed requirements, propagation, and current trust limitations.
See [Approvals](approvals.md) for record binding, replay protection, prompt behavior, and the
recommended managed policy that disables non-interactive approvals.
See [Audit Events And Delivery](audit.md) for schema fields, collector behavior, buffering,
failure modes, and operations.

## MCP Extensions

Use `loro mcp extension-add` to register a versioned extension and repeated `--extension` options
on `loro mcp add` to attach configured extensions to a server. `loro mcp extensions [SERVER]`
shows whether each extension is enabled, managed-allowlisted, implemented, schema-valid, and
active. Unknown adapters never gain authority. Optional `settings_schema` uses JSON Schema and
is validated when the registry activates an extension.

`[mcp].task_store_path` controls durable local task handles. `subscription_max_events` and
`subscription_max_seconds` are operator ceilings; CLI callers may request only equal or lower
limits. See [Model Context Protocol](mcp.md) for the Tasks configuration and commands.

## Shared Memory Backend Checks

```bash
export LORO_POSTGRES_DSN="postgresql://user:pass@host:5432/loro"
loro memory backend-check
```

The command validates local client readiness only. It does not create tables or commit
memory records.
