# Open Agent Profiles

Loro `0.15.0` implements experimental Open Agent Profile (OAP) v1 named agents. The behavior is
provisional Level 3 against the supplied implementation guide; formal upstream certification is
pending an immutable canonical schema and conformance-suite source.

## Create And Run

```bash
loro setup profile
# equivalent profile-focused entrypoint
loro agents configure
loro agents create reviewer --instructions "Review changes and cite concrete evidence."
loro agents validate .loro/agents/reviewer.agent.yaml
loro agents show reviewer
loro agents explain reviewer
loro run --agent reviewer "Review README.md"
loro plan --agent reviewer "Plan the smallest safe change"
```

`loro setup profile` is the recommended interactive path. It creates a validated OAP v1 document
and guides the profile name, description, system instructions, configured provider/model route,
capability preset or custom tool allowlist, enabled Skills, MCP servers, memory access, workspace
root, and learning/writeback mode. It then evaluates the profile against the resolved Loro policy
and asks whether the profile should become the project default. The equivalent command under the
profile namespace is `loro agents configure`. Each step explains whether the choice affects model
behavior, tool availability, runtime permission, or the project policy ceiling.

Profiles select only provider/model routes already present in Loro configuration. Run `loro
configure` first to select a provider and dynamically discover its primary and small models, then
re-run the profile wizard. Primary and small appear as separate choices only when their model IDs
differ; configured tier routes appear as additional choices. This preserves the OAP authority
rule: a profile can choose and narrow an authorized route, but cannot introduce credentials or a
new provider endpoint.

Web research currently means governed HTTP retrieval through `curl`, not a dedicated search-engine
API. It requires three independent gates: `shell.run` in the profile tool allowlist, `web = "ask"`
or `"allow"` at the project ceiling, and `curl` plus inherited networking in the selected shell
sandbox. The web preset configures these gates after confirmation while keeping generic shell
permission denied. A custom profile that includes `shell.run` asks separately about general shell
commands and governed web retrieval, so enabling one does not silently enable the other. Managed
configuration still applies last.

`explain` is the authoritative local answer to what a profile can actually do. It prints the
root-derived trust, inheritance lineage, selected model, effective tools, MCP servers, Skills,
subagents, memory stores/scopes, narrowed permissions and budgets, writeback mode, and every
adjustment made by Loro policy.

## Composition And Harness Controls

`extends` accepts one profile name or an ordered list. Loro resolves the inheritance graph from
the same trusted discovery roots, rejects cycles and references deeper than
`max_reference_depth`, and computes digests from the composed document. Every inheritance layer is
then evaluated separately, so a child cannot widen a parent's tool, permission, path, runtime,
MCP, Skill, memory, or subagent ceiling. Composed profiles use `writeback = "off"`; update a leaf
profile explicitly rather than applying a state delta to an ambiguous merged document.

```yaml
extends: base-reviewer
spec:
  tools:
    policy: allowlist
    allow: [file.read, file.search, mcp.call, skill.read, agent.run]
    mcp_servers: [source-control]
    skills: [secure-review]
  runtime:
    subagents: [test-reviewer]
    max_subagent_depth: 1
  memory:
    stores: [oap-state, local, shared]
    scopes: [acme]
```

MCP and Skill identifiers are checked both when schemas are offered to a model and again at tool
execution. Subagents run through the ordinary bounded `AgentRuntime`, must be explicitly named,
cannot exceed the profile depth limit, and inherit the intersection of parent and child authority.
Local and shared memory backends can be disabled per profile. Shared scopes are intersected with
the resolved identity tenant; a mismatch disables shared memory. Agentic Graph task nodes bind a
profile with the schema-preserving `x-agent-profile: reviewer` extension.

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
and writeback is capped by `[agent_profiles].writeback`. Profiles can narrow configured MCP
servers, enabled Skills, local/shared memory, and named subagents. They cannot install a Skill,
configure an MCP server, create a tenant, or grant a child capability.

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
default_profile = "reviewer"
managed_paths = ["/etc/loro/agents"]
user_paths = ["~/.config/loro/agents"]
project_paths = [".agents", ".loro/agents"]
allow_user = true
allow_project = true
writeback = "propose"
max_bytes = 1000000
max_state_bytes = 200000
max_profiles = 200
max_reference_depth = 8
max_subagent_depth = 3
state_path = ".loro/agent-state.json"
proposal_path = ".loro/agent-proposals"
```

Use `loro setup agents` to configure discovery and the writeback ceiling, or `loro setup profile`
to author and optionally select a default profile. An explicit `--agent` always overrides the
default for one command or REPL. Managed configuration can set `writeback = "off"`, clear the
default, and disable user or project roots. Existing config schema `1.0` files remain compatible
because this section is additive and defaults fail closed around writes.

## Evidence And Limitations

The machine-readable statement is [oap-conformance.json](oap-conformance.json). Tests cover
encodings, digest determinism, timestamp handling, trust, root precedence, collisions, symlink
escape, composition cycles/depth, inherited authority intersection, permission/tool/root/budget
narrowing, MCP and Skill execution scoping, tenant-bound memory scopes, bounded subagents, graph
profile binding, untrusted state, whole-entry eviction, `/state` scope, revision/spec conflicts,
atomic persistence, secret redaction, runtime filtering, sessions, proposals, and auto-writeback
ceilings. Formal upstream fixture certification remains the only declared OAP gap.
