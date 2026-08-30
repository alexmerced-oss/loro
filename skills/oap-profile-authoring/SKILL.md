---
name: oap-profile-authoring
description: Generate, validate, review, and safely activate portable Open Agent Profile 1.0 specialists.
license: MIT
compatibility: Requires Loro 0.18 or an OAP 1.0 compatible harness.
allowed-tools: profile.create
metadata:
  standard: OAP 1.0
---

# OAP profile authoring

Use `profile.create` when the user asks for a reusable specialist or when a bounded subagent
identity would materially improve a task.

1. Author a precise name, description, instructions, objectives, and constraints.
2. Select only tools, enabled skills, and configured MCP servers that exist in the current
   catalog. Never invent a capability, command, credential, or state fact.
3. Leave `save` false for an agent-initiated idea; this persists a reviewable proposal.
4. Set `save` true only for an explicit user request. Loro still requires trusted edit approval.
5. Keep permissions least-authority and writeback proposal-only.

Use `loro agents generate PROMPT` for an interactive model-authored draft. Use
`--scope universal` to place a reviewed profile in `~/.agentprofiles` for cross-harness discovery.
