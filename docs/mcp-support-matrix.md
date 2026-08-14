# MCP Support Matrix

This matrix defines what Loro advertises. A combination is supported only when its unit,
interoperability, and official conformance evidence is green for the release commit.

| Role | Revision | stdio | Streamable HTTP | Lifecycle | Evidence |
| --- | --- | --- | --- | --- | --- |
| Client | `2026-07-28` | Supported | Supported | Stateless, per-request metadata | Official SDK interoperability tests; runner scenarios pending |
| Client | `2025-11-25` | Supported | Supported | Classic initialization/session | SDK tests and explicit official conformance scenarios |
| Client | `2024-11-05` | Compatibility target | Compatibility target | Classic initialization/session | Fixture tests; not advertised as fully conformance-qualified |
| Server | `2026-07-28` | Supported | Supported | Stateless | Official SDK dual-era server tests; runner scenarios pending |
| Server | `2025-11-25` | Supported | Supported | Isolated classic session | Official SDK tests and explicit official conformance scenarios |

## Capability Matrix

| Capability | Client | Server | Status |
| --- | --- | --- | --- |
| Tools | List/call | Explicit read-only exports | Supported |
| Resources | List/read | Server manifest | Supported |
| Prompts | List/get | Planning and review templates | Supported |
| Tasks extension | Start/get/update/cancel/resume | Not exported | Experimental |
| Modern subscriptions | Bounded listener | SDK behavior only | Experimental |
| MCP Apps | Metadata remains inert | Not rendered or exported | Unsupported |
| Unknown extensions | Inert | Not exported | Supported deny-by-default behavior |
| Agent Skills | Filesystem format, not an MCP extension | Not exported | Supported locally |

The scheduled `MCP Conformance` workflow runs the official `0.1.16` scenarios matching Loro's
advertised server and client capabilities for `2025-11-25`. It separately runs official Python
SDK interoperability tests for both classic and `2026-07-28` stateless operation. The published
runner does not yet contain `2026-07-28` scenarios, so Loro must not describe that revision as
officially conformance-qualified until such scenarios exist and pass. Workflow artifacts are
release evidence; local unit tests alone do not count as conformance proof.

On August 9, 2026, a local qualification run passed all nine configured server scenarios and
both configured client scenarios with `@modelcontextprotocol/conformance@0.1.16`. The official
`skills-ref==0.1.1` validator also accepts Loro's reference Agent Skill fixture. The GitHub
workflow must repeat those checks for the release commit before the result becomes release
evidence.

The frozen 0.7 claim is also recorded in
[`interoperability-matrix.json`](interoperability-matrix.json). Hostile fixtures prove that an
unknown or downgraded revision is rejected, a classic peer cannot activate modern Tasks by merely
advertising the extension, and unknown extension data remains inert even when allowlisted. MCP
Apps, legacy HTTP+SSE, and undeclared extensions are not silently downgraded or rendered.

## Sunset Policy

- `2026-07-28` is preferred.
- `2025-11-25` remains supported until a dated roadmap review removes it with at least one
  minor-release notice period.
- `2024-11-05` is compatibility-only and may be removed after downstream inventory confirms no
  managed deployments require it.
- Legacy HTTP+SSE remains disabled and is not part of the supported matrix.
