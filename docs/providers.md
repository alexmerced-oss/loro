# AI Providers

Loro supports provider configuration through built-in profiles and a local setup wizard.

Run the wizard with no flags for the easiest setup:

```bash
loro configure
loro doctor
```

The wizard lists available providers, prompts for the primary and small model, and writes
`.loro/config.local.toml`. API keys stay in environment variables. Choose `mock` for a no-key
first run, or pick a cloud provider after exporting the matching API key.

## Commands

```bash
loro providers list
loro providers show openai
loro providers check openai
loro providers request "hello" --provider openai --model gpt-5.6-luna
loro providers smoke "hello" --provider openai --model gpt-5.6-luna
loro providers smoke "hello" --provider openai --model gpt-5.6-luna --execute
loro providers smoke "hello" --provider openai --model gpt-5.6-luna --execute --stream
loro configure
```

Non-interactive example:

```bash
loro configure \
  --provider ollama \
  --model llama3.2 \
  --small-model llama3.2 \
  --base-url http://localhost:11434
```

By default, `loro configure` writes `.loro/config.local.toml`, which is ignored by Git.

`loro providers check` validates the configured provider profile and reports missing API key environment variables. `loro providers request` builds and prints a redacted request payload without sending it over the network. `loro providers smoke` is also dry-run by default and only calls the provider when `--execute` is passed.

## Built-In Profiles

Cloud and gateway profiles:

- OpenAI
- Anthropic
- Google Gemini
- Mistral
- Groq
- Cerebras
- Together AI
- Fireworks AI
- DeepSeek
- xAI
- Perplexity
- OpenRouter
- Nous Portal / Nous Research
- OpenCode Zen
- OpenCode Go
- Azure OpenAI
- AWS Bedrock

Local and self-hosted profiles:

- Ollama
- LM Studio
- vLLM
- Generic OpenAI-compatible endpoint

## Current Scope

The MVP stores provider configuration, exposes provider metadata, and includes request-building adapters for:

- OpenAI-compatible providers
- Anthropic
- Gemini
- Ollama
- Mock local provider
- AWS Bedrock through optional `boto3` / `botocore` dependencies

OpenAI-compatible profiles include OpenAI, Mistral, Groq, Cerebras, Together AI, Fireworks AI, DeepSeek, xAI, Perplexity, OpenRouter, Nous Portal, OpenCode Zen, OpenCode Go, Azure OpenAI, LM Studio, vLLM, and generic OpenAI-compatible endpoints.

Model clients expose both `complete()` and `stream()`. Providers that do not yet have native
streaming support use a safe fallback that yields the completed response as one chunk.

Provider clients also normalize native tool-call response payloads into Loro's internal
tool-call shape. The runtime can execute tool calls returned through OpenAI-compatible
`tool_calls`, Anthropic `tool_use` blocks, Gemini `functionCall` parts, and Bedrock
`toolUse` blocks. The provider-neutral `@tool {"name": "...", "args": {...}}` text
directive remains supported for deterministic testing, local models, and providers that do
not expose native tool calling.

Provider/network errors are normalized into Loro provider errors so CLI and runtime output can
show clear messages for timeouts, HTTP status failures, malformed JSON, missing response
content, malformed tool calls, and optional SDK issues.

## Live-Tested Provider Examples

The following provider/model combinations have been validated with live provider smoke tests
and at least one model-directed Loro agent loop:

```bash
export NOUS_API_KEY="<your-nous-key>"
loro providers smoke "Reply with exactly: ok" \
  --provider nous --model deepseek/deepseek-v4-flash --execute

export OPENROUTER_API_KEY="<your-openrouter-key>"
loro providers smoke "Reply with exactly: ok" \
  --provider openrouter --model deepseek/deepseek-v4-flash --execute

export OPENCODE_ZEN_API_KEY="<your-opencode-key>"
loro providers smoke "Reply with exactly: ok" \
  --provider opencode-zen --model deepseek-v4-flash --execute

export ANTHROPIC_API_KEY="<your-anthropic-key>"
loro providers smoke "Reply with exactly: ok" \
  --provider anthropic --model claude-sonnet-5 --execute
loro providers smoke "Reply with exactly: ok" \
  --provider anthropic --model claude-haiku-4-5-20251001 --execute

export OPENAI_API_KEY="<your-openai-key>"
loro providers smoke "Reply with exactly: ok" \
  --provider openai --model gpt-5.6-luna --execute

export GEMINI_API_KEY="<your-gemini-key>"
loro providers smoke "Reply with exactly: ok" \
  --provider gemini --model gemini-3.6-flash --execute
```

Model ID details matter across gateways:

- Nous Portal and OpenRouter use `deepseek/deepseek-v4-flash`.
- OpenCode Zen uses the bare slug `deepseek-v4-flash`.
- OpenAI `gpt-5*` models such as `gpt-5.6-luna` only support the default sampling temperature, so Loro omits `temperature` for OpenAI `gpt-5*` requests.
- Anthropic `claude-sonnet-5*` models deprecate `temperature`, so Loro omits it for that model family.
- Gemini `gemini-3.6-flash` and `gemini-3.5-flash-lite` deprecate sampling parameters, so Loro omits `generationConfig.temperature` for those models.

AWS Bedrock requires optional dependencies:

```bash
python -m pip install "loro-agent[aws]"
```

Bedrock uses AWS environment/profile credentials through `boto3`; Loro does not store AWS
credentials in config.

## Notes From Hermes And OpenCode

Hermes models providers as reusable `ProviderProfile` plugins. Loro mirrors that idea with a central provider profile registry.

The Nous profile follows Hermes' bundled Nous plugin:

- Provider ID: `nous`
- Aliases: `nous-portal`, `nousresearch`
- API key env var: `NOUS_API_KEY`
- Base URL: `https://inference-api.nousresearch.com/v1`
- Default models: `hermes-3-405b`, `hermes-3-70b`
- Live-tested DeepSeek model through Nous: `deepseek/deepseek-v4-flash`

OpenCode documents OpenCode Zen and OpenCode Go as optional OpenCode-team providers that are connected through `/connect`, authenticated through `opencode.ai/auth`, and then selected through the OpenCode model list. Hermes also ships an `opencode-zen` provider plugin that defines both Zen and Go:

- OpenCode Zen provider ID: `opencode-zen`
- Zen aliases: `opencode`, `opencode_zen`, `zen`
- Zen API key env var: `OPENCODE_ZEN_API_KEY`
- Zen base URL: `https://opencode.ai/zen/v1`
- OpenCode Go provider ID: `opencode-go`
- Go aliases: `opencode_go`, `go`, `opencode-go-sub`
- Go API key env var: `OPENCODE_GO_API_KEY`
- Go base URL: `https://opencode.ai/zen/go/v1`

Zen and Go model IDs should be configured after consulting the current OpenCode model catalog.
OpenCode Zen has been live-tested with `deepseek-v4-flash`.
