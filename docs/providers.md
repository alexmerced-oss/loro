# AI Providers

Loro supports provider configuration through built-in profiles and a local setup wizard.

## Commands

```bash
loro providers list
loro providers show openai
loro providers check openai
loro providers request "hello" --provider openai --model gpt-4.1
loro providers smoke "hello" --provider openai --model gpt-4.1
loro providers smoke "hello" --provider openai --model gpt-4.1 --execute
loro providers smoke "hello" --provider openai --model gpt-4.1 --execute --stream
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
