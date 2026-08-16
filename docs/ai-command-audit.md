# AI Command Audit

## Purpose

Loro deliberately separates model-backed work from deterministic control-plane operations. This
audit records the 0.14.0 command behavior so users know when a configured model is contacted.

## Model-Backed Commands

| Commands | AI role |
| --- | --- |
| `loro`, `loro repl`, `loro run`, `loro plan` | Run the governed agent runtime and preserve a durable session when resumed. |
| `loro graph generate`, graph task execution | Generate or execute work through configured model tiers under graph policy and budgets. |
| `loro docs create`, `slides create`, `sheets create`, `sheets analyze`, `brief *` | Ask the model for a complete structured draft, validate and data-protect it, then render files. |
| `loro create docs|slides|sheets|brief` | Friendly aliases for the same governed AI artifact pipeline. |
| `loro providers request`, `providers smoke --execute` | Exercise a selected provider/model directly for diagnostics. |

Artifact commands use the configured model by default. They parse the direct model response,
request one corrected draft after schema failure, and do not write files until validation passes.
The mock provider fails with a `loro configure` instruction instead of substituting generic
content. `--no-ai` is the explicit offline-scaffold path.

Graph generation follows the same fail-closed authoring contract: the model authors a small typed
workflow, Loro compiles it into exact governed AGS, then validates schema and managed graph policy.
Invalid workflow output receives one correction attempt and is written only after it passes. `graph
generate --no-ai` and `plan --format agraph --no-ai` are the explicit deterministic skeleton paths.

## Deterministic Commands

Configuration, identity, credentials, policy explanation, approvals, audit, safety, sessions,
memory lifecycle, MCP transport, provider inspection, governed-data discovery, file wrappers, and
shell wrappers do not invent model output. Interactive provider setup may issue a bounded,
read-only model-catalog request; it never invokes a model. The profile wizard validates and writes
OAP configuration without asking a model to author permissions or instructions. These commands
configure, authorize, inspect, transport, or execute typed operations. Keeping them deterministic
is part of Loro's governance and failure-analysis contract.

`loro get-started` is also deterministic and read-only. It summarizes the effective configuration
for the current folder and prints workflow guidance without invoking a model, changing policy, or
granting permissions.

`artifact.create` in the runtime tool registry is a deterministic renderer: the calling agent must
supply final kind-specific content such as `title` plus `body_markdown`, slides, rows, or brief
sections. Prompt-only calls fail instead of producing placeholders. `offline_scaffold=true` is the
explicit exception. Direct CLI artifact commands perform an AI drafting pass before calling the
same renderer contract.

## Current Limits

- Provider catalog APIs vary in completeness and availability. The setup wizard reads the current
  catalog when possible, then retains bundled and custom-entry fallback paths.
- `sheets analyze` drafts an analysis workbook from prompt context; it does not yet parse an
  existing workbook as input.
- Generated drafts are structurally validated, but factual accuracy still requires user review.
