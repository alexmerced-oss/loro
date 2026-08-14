# Open Agent Profiles

Loro `0.11.0` implements experimental Open Agent Profile (OAP) v1 named agents. The behavior is
provisional Level 2 against the supplied implementation guide; formal upstream certification is
pending an immutable canonical schema and conformance-suite source.

## Create And Run

```bash
loro agents create reviewer --instructions "Review changes and cite concrete evidence."
loro agents validate .loro/agents/reviewer.agent.yaml
loro agents show reviewer
loro agents explain reviewer
loro run --agent reviewer "Review README.md"
loro plan --agent reviewer "Plan the smallest safe change"
```

`explain` is the authoritative local answer to what a profile can actually do. It prints the
root-derived trust, selected model, effective tools, narrowed permissions and budgets, writeback
mode, and every adjustment made by Loro policy.

## Discovery And Trust

Profiles use `.agent.yaml`, `.agent.yml`, `.agent.json`, or `.agent.md` and are discovered from:

1. managed roots such as `/etc/loro/agents`;
2. user roots such as `~/.config/loro/agents`;
3. portable project root `.agents`, then preferred Loro root `.loro/agents`.

Later roots have precedence and shadowed sources are reported. Duplicate names inside one root are
errors. Trust is assigned from the root; a `metadata.trust` claim inside a file is discarded.
Symlinks escaping a root are rejected. Resolution performs no installs, fetches, or writes.

## Authority Boundary

Profiles narrow existing Loro configuration. They never add authority. Decisions use the least
permissive value, workspace roots must remain contained, tools are intersected with the configured
catalog, runtime budgets use the lower ceiling, profile rules cannot short-circuit managed rules,
and writeback is capped by `[agent_profiles].writeback`. Profile-selected sandbox, MCP, Skills,
subagents, and external memory stores are not implemented in this release.

Profile state is rendered in a delimited `trust="untrusted"` block. It cannot grant tools,
permissions, approval, or user authority. State is budgeted by whole entry, preserves pinned
entries, performs no variable substitution, and passes through managed data protection before
model injection or persistence.

## Explicit State Learning

Only an explicit user directive creates profile state:

```text
@agent-state Reviews must cite concrete line numbers.
```

With the default `propose` ceiling, Loro writes a redacted, digest-bound pending proposal:

```bash
loro agents proposals
loro agents review PROPOSAL_ID --accept
```

Review verifies the profile revision and authority-stable spec digest before applying the delta.
`auto` applies ordinary state deltas only when both profile and managed config allow it. Capability
proposals never auto-apply. Every operation is restricted to `/state`; no flag or configuration can
bypass that boundary.

Human-driven removal and direct delta application also require explicit flags:

```bash
loro agents forget reviewer ENTRY_ID --approve
loro agents apply reviewer delta.json --approve
```

Writes hold a sidecar lock across revision and digest checks, update a same-directory temporary
file, fsync content, atomically replace the profile, and fsync the parent directory where
supported. Conflicts reject instead of rebasing.

## Configuration

```toml
[agent_profiles]
enabled = true
managed_paths = ["/etc/loro/agents"]
user_paths = ["~/.config/loro/agents"]
project_paths = [".agents", ".loro/agents"]
allow_user = true
allow_project = true
writeback = "propose"
max_bytes = 1000000
max_state_bytes = 200000
max_profiles = 200
state_path = ".loro/agent-state.json"
proposal_path = ".loro/agent-proposals"
```

Use `loro setup agents` to configure the local section. Managed configuration can set `writeback =
"off"` and disable user or project roots. Existing config schema `1.0` files remain compatible
because this section is additive and defaults fail closed around writes.

## Evidence And Limitations

The machine-readable statement is [oap-conformance.json](oap-conformance.json). Tests cover
encodings, digest determinism, timestamp handling, trust, root precedence, collisions, symlink
escape, permission/tool/root/budget narrowing, untrusted state, whole-entry eviction, `/state`
scope, revision/spec conflicts, atomic persistence, secret redaction, runtime filtering, sessions,
proposals, and auto-writeback ceilings.
