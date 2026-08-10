# Subprocess Sandbox Profiles

Loro routes direct `shell.run` calls, Git operations, Polaris CLI discovery, MCP stdio servers,
and Agent Skill scripts through named subprocess profiles.
Profiles bound the executable, working directory, inherited environment, runtime, combined
stdout/stderr, writable roots, and network policy. Run `loro sandbox doctor` before deployment.

## Local And Enforced Modes

The default `process` backend preserves local development portability. It enforces canonical
cwd/executable checks, a minimal environment, timeout, and streaming output termination, but it
is not an operating-system filesystem or network sandbox. Diagnostics report those boundaries
as unenforced.

Enterprise Linux deployments should use Bubblewrap and fail closed:

```toml
[permissions]
workspace_roots = ["/work/repos/approved"]

[sandbox]
enabled = true
shell_profile = "controlled-shell"
skill_profile = "skill-script"
git_profile = "git"
governed_data_profile = "governed-data"
mcp_stdio_profile = "mcp-stdio"

[sandbox.profiles.controlled-shell]
backend = "bubblewrap"
require_os_enforcement = true
network = "deny"
allowed_executables = ["/usr/bin/git", "/usr/bin/python3"]
environment_allowlist = ["PATH", "LANG"]
writable_roots = ["/work/repos/approved"]
max_seconds = 120
max_output_bytes = 1000000

[sandbox.profiles.skill-script]
backend = "bubblewrap"
require_os_enforcement = true
network = "deny"
allowed_executables = ["/usr/bin/python3", "/usr/bin/dash"]
environment_allowlist = ["PATH", "LANG"]
writable_roots = []
max_seconds = 60
max_output_bytes = 250000

[sandbox.profiles.git]
backend = "bubblewrap"
require_os_enforcement = true
network = "deny"
allowed_executables = ["/usr/bin/git"]
environment_allowlist = ["PATH", "LANG"]
writable_roots = ["/work/repos/approved"]
max_seconds = 120
max_output_bytes = 1000000

[sandbox.profiles.governed-data]
backend = "bubblewrap"
require_os_enforcement = true
network = "inherit"
allowed_executables = ["/opt/polaris/bin/polaris"]
environment_allowlist = ["PATH", "LANG"]
writable_roots = []
max_seconds = 120
max_output_bytes = 1000000

[sandbox.profiles.mcp-stdio]
backend = "bubblewrap"
require_os_enforcement = true
network = "deny"
allowed_executables = ["/usr/bin/node", "/usr/bin/python3"]
environment_allowlist = ["PATH", "LANG"]
writable_roots = []
max_seconds = 120
max_output_bytes = 1000000
```

Bubblewrap binds only the system roots a profile declares, creates fresh `/dev`, `/proc` and
`/tmp`, unshares the PID, IPC and UTS namespaces, optionally unshares the network namespace, and
bind-mounts only configured writable roots. Every writable root must remain under managed
`[permissions].workspace_roots`. Loro fails before launch when Bubblewrap is unavailable, a
required profile uses the advisory backend, an executable or cwd is outside policy, or a writable
root is invalid.

Filesystem exposure is controlled by two profile fields:

- `filesystem` (default `"minimal"`) binds only `readonly_roots`
  (`/usr`, `/bin`, `/sbin`, `/lib`, `/lib64`, `/etc`, `/opt` by default). Nothing under `$HOME`
  is visible, so `~/.ssh`, `~/.aws` and `~/.config/loro/credentials.json` are simply absent.
- `filesystem = "host_readonly"` restores the older whole-root read-only bind for profiles that
  need it. In that mode `masked_paths` (defaulting to `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`,
  `~/.docker`, `~/.config/loro`) are covered with an empty tmpfs.

`--unshare-pid` also means a sandboxed process can no longer read another process's
`/proc/<pid>/environ`.

## Environment And Output

Child environments start empty and inherit only named variables. Provider, audit, database, MCP,
and other credentials therefore do not reach shell or Skill processes unless an administrator
explicitly adds their variable names. Keep `PATH` managed and prefer canonical executable paths
in `allowed_executables` because a user-controlled `PATH` can select an unintended binary.

A pattern containing `/` is matched against the resolved absolute path. A bare-name pattern such
as `git` is matched against the basename, but never accepts a binary that resolves inside a
workspace root or a profile writable root — the places the agent itself can write. Set
`trusted_executable_prefixes` to additionally require name-matched binaries to live under a
specific prefix.

Loader and interpreter variables (`LD_PRELOAD`, `LD_LIBRARY_PATH`, `PYTHONPATH`, `NODE_OPTIONS`,
`BASH_ENV`, and similar) are rejected by configuration validation in both
`environment_allowlist` and `[mcp.servers.<id>].env_allowlist`: forwarding them would let a
caller inject code into any child process.

MCP stdio has a second explicit environment layer: `[mcp.servers.<id>].env_allowlist`. Loro
prepares the profile environment, adds only those named server variables, and launches through an
`execve` scrubber because the official SDK otherwise restores a small default environment. The
scrubber clears those SDK defaults before starting the real server without putting secret values
in argv.

Output is read concurrently with a combined byte ceiling. Crossing the ceiling kills the child,
marks the result truncated, and returns a nonzero status. The profile timeout is a ceiling: a
caller may request less time but cannot extend it.

## Commands

```bash
loro setup sandbox --profile controlled-shell --backend bubblewrap \
  --require-os-enforcement --network deny \
  --allowed-executables /usr/bin/git,/usr/bin/python3 \
  --writable-roots /work/repos/approved
loro sandbox doctor
loro shell run -- python -V
```

Application checks and Bubblewrap reduce subprocess reach but do not replace endpoint security,
container/VM policy, mandatory access control, production escape testing, or storage-level tenant
authorization. Phase 2 still requires deployment-specific profile configuration and escape
evidence on every supported operating system.
