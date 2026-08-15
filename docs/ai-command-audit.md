# AI Command Audit

## Purpose

Loro deliberately separates model-backed work from deterministic control-plane operations. This
audit records the 0.13.0 command behavior so users know when a configured model is contacted.

## Model-Backed Commands

| Commands | AI role |
| --- | --- |
| `loro`, `loro repl`, `loro run`, `loro plan` | Run the governed agent runtime and preserve a durable session when resumed. |
| `loro graph generate`, graph task execution | Generate or execute work through configured model tiers under graph policy and budgets. |
| `loro docs create`, `slides create`, `sheets create`, `sheets analyze`, `brief *` | Ask the model for a complete structured draft, validate and data-protect it, then render files. |
| `loro create docs|slides|sheets|brief` | Friendly aliases for the same governed AI artifact pipeline. |
| `loro providers request`, `providers smoke --execute` | Exercise a selected provider/model directly for diagnostics. |

Artifact commands use the configured model by default. `--no-ai` is the explicit offline path.
The mock provider still traverses the runtime and then emits a deterministic substantive draft so
tests and first-run evaluation do not require a network key.

## Deterministic Commands

Configuration, identity, credentials, policy explanation, approvals, audit, safety, sessions,
memory lifecycle, MCP transport, provider inspection, governed-data discovery, file wrappers, and
shell wrappers do not invent model output. They configure, authorize, inspect, transport, or
execute typed operations. Keeping these commands deterministic is part of Loro's governance and
failure-analysis contract.

`artifact.create` in the runtime tool registry is also a deterministic renderer: the agent is the
AI author and the typed tool performs the governed write. Direct CLI artifact commands perform an
AI drafting pass before calling that renderer.

## Current Limits

- Model catalogs change independently of Loro. The setup wizard offers profile defaults and a
  custom model entry rather than claiming an exhaustive live catalog.
- `sheets analyze` drafts an analysis workbook from prompt context; it does not yet parse an
  existing workbook as input.
- Generated drafts are structurally validated, but factual accuracy still requires user review.
