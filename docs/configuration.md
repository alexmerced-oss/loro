# Configuration

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
[model]
provider = "mock"
model = "mock-agent"
small_model = "mock-small"
timeout_seconds = 120
temperature = 0.2

[runtime]
max_steps = 5

[permissions]
default = "ask"
shell = "ask"
edit = "ask"
web = "deny"

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
write_policy = "explicit_user_dictation_only"
read_policy = "semantic_retrieval_with_citations"
postgres_dsn_env = "LORO_POSTGRES_DSN"
postgres_schema = "public"
iceberg_namespace = "agent_memory"
iceberg_table = "shared_memories"

[polaris]
enabled = false
cli_path = "polaris"
require_role_inspection = true

[audit]
enabled = true
path = ".loro/audit.jsonl"
include_prompt_preview = true

[sessions]
path = ".loro/sessions"
```

## Runtime Overrides

```bash
LORO_CONFIG=/path/to/config.toml loro doctor
LORO_CONFIG_CONTENT='[permissions]\nshell = "allow"\n' loro doctor
```

## Managed Enterprise Overlays

Managed overlays use the same TOML shape as normal configuration, but they are merged after
all other layers. Use them for enterprise guardrails such as permission denies, audit
requirements, shared-memory backend settings, and Polaris defaults.

```toml
# /etc/loro/managed.toml
[permissions]
shell = "deny"
web = "deny"

[[permissions.rules]]
tool = "git"
action = "commit"
target = "*"
decision = "ask"
reason = "Enterprise policy requires explicit commit approval."

[audit]
enabled = true
include_prompt_preview = false

[memory.shared]
enabled = true
write_policy = "explicit_user_dictation_only"
```

For test, container, or centrally launched desktop environments:

```bash
LORO_MANAGED_CONFIG=/opt/loro/managed.toml loro doctor
LORO_MANAGED_CONFIG_CONTENT='[permissions]\nshell = "deny"\n' loro doctor
```

Normal runtime overrides cannot loosen values supplied by managed overlays because managed
TOML is applied last.

## Permission Rules

Rules are evaluated before the per-tool defaults. They use simple case-insensitive glob
matching over `tool`, `action`, and `target`; the first matching rule wins.

Common tool names today are `edit`, `git`, `shell`, and `web`.

## Setup Wizards

Use setup wizards to create or update `.loro/config.local.toml` without hand-writing TOML:

```bash
loro configure
loro configure --provider openai --model gpt-5.6-luna --small-model gpt-5.4-mini
loro setup provider
loro setup memory
loro setup shared-memory
loro setup polaris
loro setup quickstart
```

`loro configure` and `loro setup provider` configure the AI provider. `loro setup memory`
configures private local memory. `loro setup shared-memory` configures explicit-only shared
enterprise memory with either Postgres or Iceberg. `loro setup polaris` configures governed
data discovery through the Polaris CLI. `loro setup quickstart` runs all four in sequence.

All setup commands preserve existing sections in the target config file. They write local
settings only; provider secrets, Postgres DSNs, Iceberg credentials, and tokens should remain
in environment variables.

## Shared Memory Backend Checks

```bash
export LORO_POSTGRES_DSN="postgresql://user:pass@host:5432/loro"
loro memory backend-check
```

The command validates local client readiness only. It does not create tables or commit
memory records.
