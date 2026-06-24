# AI Providers

Loro supports provider configuration through built-in profiles and a local setup wizard.

## Commands

```bash
loro providers list
loro providers show openai
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
- Azure OpenAI
- AWS Bedrock

Local and self-hosted profiles:

- Ollama
- LM Studio
- vLLM
- Generic OpenAI-compatible endpoint

## Current Scope

The MVP stores provider configuration and exposes provider metadata. The runtime still uses deterministic scaffolding until model adapters are implemented. The provider profiles are designed so future adapters can support native protocols and OpenAI-compatible APIs without changing user config.
