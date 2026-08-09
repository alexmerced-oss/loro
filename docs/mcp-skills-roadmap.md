# MCP And Agent Skills Roadmap

## Status And Scope

MCP Batches 1 and 2 are implemented. Extensions, server mode, release conformance, and Agent
Skills remain planned. This roadmap defines the compatibility, security, implementation, and
verification work required before the broader capability set can be advertised as
enterprise-ready.

Loro will treat these as complementary systems:

- **MCP** connects Loro to external tools, resources, prompts, and asynchronous operations.
- **Agent Skills** provide portable task instructions and supporting files through a
  `SKILL.md` package.
- Neither system grants authority. Every resulting action remains subject to Loro's identity,
  permission, approval, sandbox, data-protection, and audit controls.

## Protocol Baseline

The current MCP revision is `2026-07-28`. It replaces the stateful initialization/session
lifecycle with per-request protocol, identity, and capability metadata. Servers may implement
`server/discover`; clients are not required to call it. Streamable HTTP requests also carry
the `MCP-Protocol-Version` header.

This document uses **classic MCP** to mean the handshake-based protocol family from
`2024-11-05` through `2025-11-25`. Classic clients initialize a stateful connection and may
receive a session identifier. Loro must not confuse the two lifecycle models.

### Required Compatibility Matrix

| Area | `2026-07-28` | `2025-11-25` | `2024-11-05` |
| --- | --- | --- | --- |
| Client support | Required and preferred | Required legacy target | Compatibility target |
| Lifecycle | Stateless, per-request metadata | `initialize` handshake/session | `initialize` handshake/session |
| stdio | Required | Required | Test where SDK supports it |
| Streamable HTTP | Required | Required | Test where SDK supports it |
| Legacy HTTP+SSE | Not used | Compatibility-only, disabled by default | Compatibility-only |
| Tools/resources/prompts | Required | Required | Required compatibility coverage |
| Roots/sampling/protocol logging | Do not adopt | Compatibility callbacks only | Compatibility callbacks only |
| Notifications | `subscriptions/listen` | Legacy notifications/subscriptions | Legacy notifications/subscriptions |

The default version policy will be `auto`, preferring the highest mutually supported revision.
Managed configuration may set a minimum version or exact pin. Loro must fail closed when no
allowed revision overlaps, and it must never silently downgrade below a managed minimum.

## Architecture Direction

Use the official MCP Python SDK v2 behind an optional `mcp` package extra. The SDK already
provides a unified client that probes the new discovery path and falls back to the classic
initialization handshake. Loro should not hand-roll JSON-RPC framing, lifecycle negotiation,
or transport parsing.

Add a `loro.mcp` package with these boundaries:

- A server registry containing stable server ids, transport, command or URL, environment
  allowlist, authentication profile, version policy, extension allowlist, and enabled state.
- A protocol-neutral client facade for tools, resources, prompts, tasks, and subscriptions.
- SDK-backed stdio and Streamable HTTP transports with bounded startup, request, idle, and
  shutdown timeouts.
- Adapters that expose MCP tools through Loro's existing `ToolRegistry`, `PermissionEngine`,
  `ApprovalStore`, normalized resources, sandbox policy, and audit pipeline.
- Content trust labels for MCP resources, prompts, tool results, and server metadata. All are
  untrusted model context, not instructions with elevated precedence.

Canonical MCP tool resources should include server id, tool name, transport, endpoint identity,
and normalized arguments. A model cannot authorize its own MCP call. A server-requested
multi-round-trip input, OAuth flow, task update, or UI action also requires the same policy and
approval checks as an equivalent native Loro operation.

## New-Spec Requirements

For `2026-07-28`, Loro must:

- Send protocol version, client information, and capabilities in each request's MCP `_meta`;
  send the standard protocol/method/name headers over Streamable HTTP.
- Use `server/discover` when useful, while tolerating servers that omit it and falling back to
  the classic initialization path only when policy permits.
- Avoid `initialize`, `notifications/initialized`, `Mcp-Session-Id`, `ping`, standalone HTTP
  GET notification streams, and removed resource subscription methods on the new path.
- Use multi-round-trip request parameters for elicitation-style input; never let a remote
  server interact with the user or model outside Loro's approval and audit boundary.
- Use `subscriptions/listen` for accepted change notifications and bound the event kinds,
  reconnect behavior, queue size, and processing rate.
- Negotiate extensions by reverse-DNS id and ignore unknown extensions without granting them
  tools, UI, network, storage, or approval authority.

## Classic-Spec Requirements

For handshake-based MCP revisions, Loro must:

- Perform initialization and capability negotiation before ordinary requests.
- Keep session state scoped to one configured server and one Loro session, with deterministic
  shutdown and no credential or capability reuse across servers.
- Support tools, resources, and prompts through the same internal adapters used by the new
  protocol so policy semantics do not change after negotiation.
- Gate classic roots, sampling, elicitation, and logging callbacks independently. These are
  compatibility features, not dependencies for new Loro architecture.
- Treat legacy HTTP+SSE as an opt-in migration feature with a deprecation warning and a removal
  review date.

## Extensions

Extensions are deny-by-default and independently versioned. The initial allowlist should contain
only extensions for which Loro has a typed adapter and security tests.

- **Tasks:** support `io.modelcontextprotocol/tasks` after the core client is stable. Persist
  durable task handles, honor polling intervals and TTLs, support input-required and cancellation
  states, and route every `tasks/update` input through normal consent controls.
- **MCP Apps:** defer rendering until Loro has a sandboxed application host. Before then, preserve
  safe metadata for inspection but do not execute or render server-supplied HTML. A future host
  must sandbox frames, constrain origins/network access, and route UI calls through normal policy.
- **Experimental skills extension:** evaluate for interoperability later. Loro's initial skill
  support will use the open Agent Skills filesystem format and will not depend on an experimental
  MCP extension.

## Agent Skills Support

Loro will implement the open Agent Skills directory format. A valid skill has a `SKILL.md` file
with YAML frontmatter containing at least `name` and `description`; it may include `scripts/`,
`references/`, and `assets/` directories.

### Discovery And Loading

Discover skills from ordered scopes:

1. Enterprise-managed: `/etc/loro/skills`
2. User: `~/.config/loro/skills`
3. Project: `.loro/skills`

Managed policy decides whether user and project skills are permitted. Name collisions are errors
unless policy defines an explicit precedence. At startup, load only validated name, description,
source, trust, and hash metadata. Load the full `SKILL.md` only when selected, and supporting
files only on demand. Enforce configurable file-count, byte, token, recursion, and reference
depth limits.

### Trust And Execution

- Validate the published Agent Skills naming and metadata constraints; use `skills-ref validate`
  in conformance CI where practical.
- Treat skill text, references, scripts, and assets as untrusted content with source and digest
  provenance.
- Treat experimental `allowed-tools` metadata as an advisory restriction only. Intersect it
  with Loro policy; it can narrow authority but can never expand it.
- Execute skill scripts only through registered Loro tools and sandbox profiles. A skill file is
  never sourced into the Loro process or given ambient credentials.
- Support enable, disable, quarantine, inspect, and remove states. Remote installation requires
  an explicit user action, source review, immutable revision or digest, and audit event.
- Permit agents to draft skill proposals, but require explicit review before local installation
  and explicit governed publication before enterprise sharing. Skills must never write
  themselves into shared memory or a managed skill registry.

## Planned CLI

```text
loro mcp list|add|remove|enable|disable|inspect|doctor|test
loro mcp tools|resources|prompts|tasks
loro skills list|show|validate|enable|disable|install|remove
loro skills propose|review
```

Configuration wizards should collect transport/auth/version choices, display requested
capabilities, validate connectivity without invoking tools, and write secrets only through an
approved environment or credential-store reference.

## Implementation Batches

### Batch 1: Dual-Era MCP Client Foundation

**Status: complete for the alpha client foundation.**

- Add the optional SDK dependency, typed configuration, registry, and diagnostics.
- Implement stdio and Streamable HTTP clients.
- Prefer `2026-07-28`; support `2025-11-25`; retain a `2024-11-05` fixture target.
- List/call tools, list/read resources, and list/get prompts.
- Normalize calls into Loro permissions, approvals, sessions, and audit events.
- Add `mcp add`, `list`, `inspect`, `doctor`, and non-mutating `test` commands.

Exit: one server fixture per required lifecycle works through the same Loro tool interface, and
no-overlap, malformed response, timeout, and denied-call tests pass.

Implemented evidence includes `src/loro/mcp/`, MCP CLI/setup commands, normalized `mcp`
resources, runtime tools, `tests/test_mcp.py`, and official-SDK modern/classic coverage in
`tests/test_mcp_sdk.py`. The `2024-11-05` revision remains a compatibility target pending the
Batch 6 conformance matrix; current official-SDK tests prove `2026-07-28` and the SDK's latest
classic handshake path.

### Batch 2: Enterprise Transport And Authorization

Status: **implemented**.

- Add server/host allowlists, TLS requirements, redirect/SSRF controls, environment isolation,
  output limits, and credential profiles.
- Implement current MCP OAuth discovery, issuer validation, and Client ID Metadata Documents;
  keep Dynamic Client Registration only behind explicit legacy policy where required.
- Gate classic roots, sampling, logging, and server-initiated callbacks.
- Add multi-round-trip input approval for the new protocol.

Exit: credential, issuer-confusion, downgrade, DNS/redirect, environment-leak, and callback
abuse suites pass.

Implemented in `src/loro/mcp/security.py`, typed MCP credential profiles, the official SDK OAuth
providers, custom no-redirect HTTP clients, bounded pagination/results, and callback-deny
defaults. Authorization-code profiles use SDK discovery, issuer/resource validation, and CIMD;
DCR requires explicit legacy opt-in. Managed DNS preflight is defense in depth and does not
replace enterprise egress enforcement. Batch 3 is the next implementation batch.

### Batch 3: Extensions And Long-Running Work

- Add an extension registry with ids, versions, schemas, adapters, and managed allowlists.
- Implement Tasks create/get/update/cancel and durable task resumption.
- Implement bounded `subscriptions/listen` handling.
- Document MCP Apps as unsupported until a sandboxed host is complete.

Exit: unknown extensions are inert, task inputs require approval, and reconnect/cancellation
tests pass without losing audit continuity.

### Batch 4: MCP Server Mode

- Expose selected Loro tools, resources, and prompts through an explicit server allowlist.
- Serve new and classic lifecycle clients from the same deployment where the SDK supports it.
- Keep the new path stateless; isolate classic sessions and credentials.
- Never expose local/shared memory or governed data by default.

Exit: server conformance, tenant isolation, least-privilege export, and dual-era interoperability
tests pass.

### Batch 5: Agent Skills Foundation

- Add discovery, parser, validation, progressive disclosure, provenance, and lifecycle commands.
- Integrate skill activation with model context budgeting and Loro policy.
- Add sandboxed script/resource access and explicit proposal/review workflows.
- Add configuration wizard support and user/operator documentation.

Exit: official-format fixtures validate, malicious and oversized skill suites fail closed, and
`allowed-tools` cannot bypass a deny or approval.

### Batch 6: Conformance And Release Qualification

- Run the official MCP conformance suite for each advertised role and revision.
- Add interoperability fixtures using official SDK example clients and servers.
- Test negotiation, downgrade, malformed schemas, cancellation, authorization, restart, load,
  and dependency-version upgrades.
- Publish the supported revision/transport/extension matrix and sunset dates.

Exit: only combinations with repeatable CI evidence are documented as supported.

## Release Acceptance Criteria

- Every MCP connection records server identity, transport, negotiated revision, capabilities,
  extensions, endpoint or command digest, and policy source without recording secrets.
- A `2026-07-28` connection works without initialization or session affinity.
- A classic connection completes initialization and has isolated session state.
- The same canonical tool request receives the same Loro policy decision on both lifecycles.
- Unsupported revisions and extensions fail closed with actionable diagnostics.
- MCP resources, prompts, results, and skill content remain untrusted context.
- Skills cannot grant tools, expose credentials, execute ambient scripts, or publish themselves.
- Documentation clearly distinguishes implemented, experimental, compatibility-only, and
  unsupported features.

## Primary References

- [MCP versioning and negotiation](https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning)
- [MCP 2026-07-28 release overview](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [MCP Python SDK v2 changes](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md)
- [MCP Tasks extension](https://modelcontextprotocol.io/extensions/tasks/overview)
- [MCP conformance suite](https://github.com/modelcontextprotocol/conformance)
- [Classic MCP 2024-11-05 specification](https://modelcontextprotocol.io/specification/2024-11-05)
- [Agent Skills specification](https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx)
- [MCP guidance for Agent Skills](https://modelcontextprotocol.io/docs/develop/build-with-agent-skills)
