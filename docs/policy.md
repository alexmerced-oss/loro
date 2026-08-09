# Normalized Resource Policy

Loro evaluates permission requests against typed, canonical resources. Legacy `tool`, `action`,
and `target` glob rules remain supported, while structured rules can match fields that are not
safe to infer from a display string.

## Resource Types

Current normalized resource kinds are:

- `filesystem`: operation, absolute resolved path, and matched workspace root.
- `shell`: operation, invoked executable name, resolved executable, resolved name, exact
  argument array, and shell-escaped display command. Loro still executes without a shell.
- `git`: operation, resolved repository, workspace root, resolved paths, revision, or message.
- `memory`: operation, tenant, scope type/key, and backend.
- `polaris`: operation, catalog, namespace, table/resource, role, policy, and arguments.
- `provider`: operation, provider, model, and configured base URL.
- `mcp`: operation, server id, transport, redacted endpoint, remote capability name, argument
  names, and a canonical argument digest. Raw MCP argument values are approval-bound but are
  not stored in the normalized target.
- `session_message`: operation, sender and recipient session ids, content digest, and the
  permanent `carries_user_authority = false` marker. Raw message content is not part of the
  normalized target.

Filesystem and Git paths use `Path.resolve(strict=False)`. Existing symlinks and parent
segments are resolved before root enforcement and policy matching. Structured filesystem path
fields are case-sensitive on POSIX; legacy target globs retain their case-insensitive behavior
for compatibility.

## Workspace Roots

Set managed roots to deny filesystem and Git resources outside approved workspaces:

```toml
[permissions]
version = "corp-policy-2026-08-08"
workspace_roots = ["/work/repos", "/work/documents"]
```

An empty root list preserves the local-development behavior of allowing normalization anywhere.
Enterprise managed policy should set explicit roots. A path outside all configured roots fails
before approval, so a user cannot approve an out-of-scope path.

## Structured Rules

The first complete matching rule wins. Every entry under `resource` must match the normalized
field using glob syntax.

```toml
[permissions]
version = "corp-policy-42"
shell = "ask"
edit = "deny"

[[permissions.rules]]
tool = "edit"
action = "read file"
resource_kind = "filesystem"
decision = "allow"
reason = "Read approved repository documentation."

[permissions.rules.resource]
path = "/work/repos/*/docs/*"

[[permissions.rules]]
tool = "shell"
action = "run*"
resource_kind = "shell"
decision = "deny"
reason = "Direct interpreters require a sandbox profile."

[permissions.rules.resource]
resolved_executable_name = "python*"

[[permissions.rules]]
tool = "governed_data"
action = "tables"
resource_kind = "polaris"
decision = "allow"

[permissions.rules.resource]
catalog = "prod"
namespace = "analytics"
```

Keep `version` stable for one policy artifact and change it whenever authorization behavior
changes. Approval fingerprints bind this version and policy source, so an existing grant cannot
be reused after the evaluated policy version changes.

## Explain A Decision

Pass a JSON fixture to the policy explainer:

```bash
loro policy explain '{
  "tool": "shell",
  "action": "run command",
  "target": "python -V",
  "resource": {
    "kind": "shell",
    "executable_name": "python",
    "resolved_executable_name": "python3.12",
    "arguments": ["-V"]
  }
}'
```

The result includes the decision, reason, policy version, policy source, matched rule index, and
normalized resource. Explanation does not execute or approve the request.

## Security Boundaries

- Resource normalization and policy evaluation do not replace an operating-system sandbox.
  Shell and Skill subprocesses can additionally require the Bubblewrap backend; other process
  families are not yet routed through it.
- `strict=False` supports paths that will be created, but there remains a time-of-check/time-of-
  use window if another process can replace path components after authorization.
- Shell policy sees exact argument boundaries and rejects NUL bytes. Named sandbox profiles now
  constrain its environment, cwd, executable, output, and runtime; network/filesystem isolation
  requires an operational Bubblewrap profile and production validation.
- The policy version is configured and approval-bound but is not yet signed or integrity-
  verified. Managed distribution must protect the policy file.
- Memory tenant selection is represented in policy resources. Managed identity isolation now
  binds shared-memory operations/adapters and local drafts to the trusted identity tenant;
  Postgres emits forced RLS and Iceberg pushes tenant filtering into scans. Production database
  role and Polaris authorization evidence remains Phase 2 work.
- MCP policy does not replace remote-server trust, transport authentication, content trust
  labeling, or a subprocess/network sandbox. See [Model Context Protocol](mcp.md).
- Session-message policy governs delivery only. A receiver must independently authorize every
  action considered from relayed content. See [Cross-Session Messaging](session-messaging.md).
