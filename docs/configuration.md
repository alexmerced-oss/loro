# Configuration

Loro uses TOML configuration and merges layers in increasing precedence:

1. `/etc/loro/config.toml`
2. `~/.config/loro/config.toml`
3. `.loro/config.toml`
4. `.loro/config.local.toml`
5. `LORO_CONFIG`
6. `LORO_CONFIG_CONTENT`

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

Future managed enterprise policy should be non-overridable. The current MVP uses deep-merge precedence.

## Permission Rules

Rules are evaluated before the per-tool defaults. They use simple case-insensitive glob
matching over `tool`, `action`, and `target`; the first matching rule wins.

Common tool names today are `edit`, `git`, `shell`, and `web`.

## Provider Wizard

Use the configuration wizard to create `.loro/config.local.toml`:

```bash
loro configure
loro configure --provider openai --model gpt-4.1 --small-model gpt-4.1-mini
```

## Shared Memory Backend Checks

```bash
export LORO_POSTGRES_DSN="postgresql://user:pass@host:5432/loro"
loro memory backend-check
```

The command validates local client readiness only. It does not create tables or commit
memory records.
