# Plugins And Hooks

Status: experimental in 0.22.

Loro has two ways to extend it, and both can only narrow or add governed capabilities; neither can
grant authority.

## Command hooks

Run an executable before or after matching tool calls:

```toml
[[plugins.hooks]]
name = "no-dotenv"
event = "pre_tool"            # or "post_tool"
match = ["file.*", "patch.apply"]
command = ["python3", "hooks/no_dotenv.py"]
timeout_seconds = 10
# sandbox_profile = "hook"    # default: sandbox.hook_profile
```

The hook receives the event as JSON in the `LORO_HOOK_EVENT` environment variable:
`phase`, `tool`, `arguments` (replaced by `{"truncated": true}` above 16 KB), `origin`, `subject`,
`session_id`, and for `post_tool` also `ok` and `output_preview`. For a `pre_tool` hook, exit 0
allows the call and exit 2 blocks it, with stderr as the reason the model sees. Any other exit
status or a timeout is a hook failure. Hooks run in the `hook` sandbox profile (30 seconds; `python*`, `sh`, `bash` and `node`
allowlisted; the event variable allowlisted); a custom profile must allowlist `LORO_HOOK_EVENT`.

## Plugins

A plugin is a Python package with a `loro.plugins` entry point that returns a `LoroPlugin`
(or a zero-argument callable returning one):

```toml
# the plugin's pyproject.toml
[project.entry-points."loro.plugins"]
acme = "acme_loro:plugin"
```

```python
from loro.plugins import HookDecision, LoroPlugin, PluginTool

def lookup(args):
    return f"ticket {args['id']} is open"

def block_prod(event):
    if event.tool == "shell.run" and "prod" in str(event.arguments):
        return HookDecision(False, "production commands need a change ticket")
    return None

plugin = LoroPlugin(
    name="acme",
    version="1.0.0",
    description="Acme ticket lookups",
    tools=[PluginTool("ticket", "Look up a ticket", {"type": "object",
           "properties": {"id": {"type": "string"}}, "required": ["id"]}, lookup)],
    pre_tool=[block_prod],
)
```

Installing a package enables nothing. Loro imports a plugin only when its name is in the allow
list:

```toml
[plugins]
enabled = ["acme"]
hook_failure = "deny"   # a failing pre_tool hook blocks the call; "allow" lets it through
```

Plugin tools appear as `plugin.<plugin>.<tool>`. They use the `plugins` permission (`ask` by
default, so each call needs approval), then data protection on their output and the audit log,
like built-in tools. An agent running an Open Agent Profile can use a plugin tool only if the
profile lists it; approvals for plugin tools go through the same AAIS authority as everything
else.

## Guarantees

- A `pre_tool` hook can block a call. It cannot approve one, grant a permission, or change the
  call's arguments (it receives a copy).
- A `post_tool` hook observes the protected result; it cannot change it. Its failures are audited
  and never affect the call.
- Every plugin load (`plugin.loaded`), plugin tool call (`plugin.tool_executed`), hook block
  (`plugin.hook_denied`) and hook failure (`plugin.hook_failed`) is audited.

`loro plugins list [--json]` shows installed plugins, whether each is enabled or failed to load,
its tools and hooks, and the configured command hooks. `loro plugins doctor` exits 1 when an
enabled plugin failed to load or a hook's sandbox profile is missing or does not allow
`LORO_HOOK_EVENT`. The Web UI's Extensions view lists the same information.

## Limits

- Python plugins run in the Loro process with its privileges; enable only reviewed packages.
  Command hooks run in a sandbox profile.
- Hooks see tool arguments before data protection is applied to output; treat hook executables as
  trusted code.
