# Agent Skills

Loro supports the open Agent Skills filesystem format. A package is a directory whose name
matches the `name` in `SKILL.md`; YAML frontmatter must include `name` and `description`.
Optional supporting content is loaded only when requested. Scripts must remain directly under
`scripts/` to be executable.

## Discovery And Trust

Skills are discovered from managed, user, and project roots in that order. Name collisions are
errors rather than implicit precedence. Discovery reads validated metadata and computes a
SHA-256 package digest; the instruction body is loaded only after activation.

```toml
[skills]
enabled = true
managed_paths = ["/etc/loro/skills"]
user_paths = ["~/.config/loro/skills"]
project_paths = [".loro/skills"]
allow_user = true
allow_project = true
allow_scripts = false
max_files = 100
max_bytes = 1000000
max_instruction_bytes = 100000
max_reference_depth = 4
max_active = 3
```

Managed packages are labeled `enterprise-managed`; user and project packages remain
`untrusted-local`. Neither label grants tool authority. Experimental `allowed-tools` metadata
can narrow script execution but cannot override a Loro deny or satisfy an approval.

## Commands

```bash
loro setup skills
loro skills list
loro skills validate ./my-skill
loro skills show my-skill
loro skills disable my-skill
loro skills enable my-skill
loro skills quarantine my-skill
```

Installation is local-source-only and digest pinned. Review with `skills validate`, then pass
the exact printed digest:

```bash
loro skills install ./my-skill --expected-digest sha256:REVIEWED_DIGEST
loro skills remove my-skill --yes
```

An agent may stage a proposal but cannot install it:

```bash
loro skills propose ./my-skill
loro skills review PROPOSAL_ID --accept
loro skills review PROPOSAL_ID --reject
```

Each proposal is immutable at a content digest and can be reviewed once.

## Claude And Pi Compatibility

Loro can inspect and import the skill portions of Claude skills/plugins and Pi skills/packages.
The default operation is a non-mutating compatibility report:

```bash
loro skills import-claude ./my-claude-plugin
loro skills import-pi ./my-pi-package
```

The report includes a digest, normalized skill names, compatible MCP servers, warnings, and host
components that Loro will not execute. Install only after reviewing that exact source:

```bash
loro skills import-claude ./my-claude-plugin \
  --expected-digest sha256:REVIEWED_DIGEST --execute
loro skills import-pi ./my-pi-package \
  --expected-digest sha256:REVIEWED_DIGEST --execute
```

For Claude plugins, compatible `.mcp.json` or manifest MCP definitions may be imported through a
separate explicit gate:

```bash
loro skills import-claude ./my-claude-plugin \
  --expected-digest sha256:REVIEWED_DIGEST --execute --include-mcp
```

MCP environment values must be same-name `${VARIABLE}` references. Bearer headers must use
`Bearer ${VARIABLE}`; literal values, legacy SSE endpoints, arbitrary headers, and name collisions
fail closed. Plugin-local MCP executables are not imported because their mutable source directory
would no longer be bound to the reviewed digest. Claude agents, commands, hooks, LSP servers,
monitors, binaries, and settings are reported but not hosted. Pi TypeScript extensions, prompts,
and themes are likewise reported but not executed.

Compatibility normalization supplies missing Pi/Claude skill names and descriptions, converts
list-form `allowed-tools`, and resolves `${CLAUDE_PLUGIN_ROOT}`, `${LORO_SKILL_ROOT}`, and
`{baseDir}` to the installed skill directory at activation. Existing Loro policy remains
authoritative, and imported scripts remain disabled by default.

The 0.7 compatibility subset is frozen in
[`interoperability-matrix.json`](interoperability-matrix.json). Loro accepts Agent Skills content,
the skill portions of Claude and Pi packages, and separately approved environment-referenced MCP
definitions from Claude plugins. Claude agents, commands, hooks, LSP integrations, binaries, and
settings are reported as unsupported. Pi TypeScript extensions, prompts, and themes are likewise
reported and left inert. Import reports name every unsupported host component before any write.

## Runtime Activation

Loro activates up to `[skills].max_active` matching skills from metadata. Force explicit
selection with a standalone directive:

```text
@skill python-review
```

Instruction content is inserted as untrusted context with source, trust label, and digest.
Supporting UTF-8 text is available through `skill.read`. `skill.run_script` is disabled by
default; when managed configuration enables it, only direct files under `scripts/` may run,
the package's advisory tool restriction must allow execution, and normal shell policy and
approval still apply. Skill subprocess isolation remains dependent on Loro's broader sandbox
profile work and should not be enabled for an enterprise pilot before that gate is complete.

The format constraints follow the
[Agent Skills specification](https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx).
Compatibility behavior is based on the
[Claude plugin reference](https://code.claude.com/docs/en/plugins-reference) and
[Pi package documentation](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/packages.md).
