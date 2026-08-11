# Loro 0.7 Governed Interoperability Work Record

This record maps the 0.7 milestone in the [1.0 roadmap](roadmap-1.0.md) to implementation and
evidence. Repository-owned work is complete when the release commit is green; adopting-enterprise
checks remain explicit rather than being represented by synthetic tests.

## Batch 1: Provider Contracts

- Sanitized fixtures cover OpenAI-compatible, Anthropic, Gemini, and experimental Bedrock
  completion, native tools, usage, streaming or declared fallback, malformed payloads, and errors.
- `loro providers conformance` validates the built wheel's bundled fixtures and profile mapping.
- Managed host allowlists, CA/proxy settings, request correlation, bounded retries, usage budgets,
  and explicit-only graph tier routing are tested.
- `Interoperability Conformance` emits hermetic evidence on changes and offers a protected live
  matrix with no cross-provider fallback.

## Batch 2: MCP And Skills

- The matrix freezes supported, compatibility-only, and unsupported MCP revisions/capabilities.
- Hostile tests cover downgrade, capability confusion, unknown extension data, Tasks revision
  binding, cancellation continuity, and bounded subscriptions.
- Claude and Pi imports expose a frozen skill-only subset and report unsupported host behavior.

## Batch 3: Agentic Graphs

- Pinned AGS schemas, examples, negative fixtures, and run records execute in protected CI.
- Approval, retry, fallback, compensation declaration, resume, and budget paths have deterministic
  failure-injection coverage. Live providers remain an optional protected environment check.

## Batch 4: Remote Gateways

- Slack, Discord, Telegram, Teams, Signal bridge, and generic adapters have signed fixtures.
- Rejection, replay, tenant/channel mismatch, rotation, duplicate, overload, failure, and audit
  paths are covered. Production app registration, TLS ingress, and platform governance belong to
  the adopting deployment.

## External Release Evidence

The repository cannot manufacture corporate provider residency approval, production rate limits,
platform app ownership, immutable audit retention, or organization-controlled secrets. Record
those checks using [External Enterprise Requirements](external-enterprise-requirements.md). A
green hermetic workflow proves Loro behavior; a protected live workflow proves only the configured
test accounts and routes at that commit.

On August 11, 2026, content-free local streaming smoke checks passed for Nous and OpenRouter with
`deepseek/deepseek-v4-flash`, OpenCode Zen with `deepseek-v4-flash`, Anthropic with
`claude-haiku-4-5`, OpenAI with `gpt-5.6-luna`, and Gemini with `gemini-3.6-flash`. No credential,
prompt body, or model response is retained in the repository. Protected workflow evidence must
still repeat approved routes at the release commit when repository secrets are configured.
