from dataclasses import dataclass
from pathlib import Path

import tomli_w

from loro.config import LoroConfig, ModelConfig


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    display_name: str
    default_model: str
    small_model: str
    api_key_env: str | None = None
    base_url: str | None = None
    protocol: str = "openai-compatible"
    notes: str = ""


PROVIDER_PROFILES: dict[str, ProviderProfile] = {
    "mock": ProviderProfile(
        name="mock",
        display_name="Mock",
        default_model="mock-agent",
        small_model="mock-small",
        protocol="local",
        notes="Deterministic scaffold provider for tests and offline development.",
    ),
    "openai": ProviderProfile(
        name="openai",
        display_name="OpenAI",
        default_model="gpt-4.1",
        small_model="gpt-4.1-mini",
        api_key_env="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
    ),
    "anthropic": ProviderProfile(
        name="anthropic",
        display_name="Anthropic",
        default_model="claude-sonnet-4-5",
        small_model="claude-haiku-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        base_url="https://api.anthropic.com",
        protocol="anthropic",
    ),
    "gemini": ProviderProfile(
        name="gemini",
        display_name="Google Gemini",
        default_model="gemini-2.5-pro",
        small_model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com",
        protocol="gemini",
    ),
    "mistral": ProviderProfile(
        name="mistral",
        display_name="Mistral",
        default_model="mistral-large-latest",
        small_model="mistral-small-latest",
        api_key_env="MISTRAL_API_KEY",
        base_url="https://api.mistral.ai/v1",
    ),
    "groq": ProviderProfile(
        name="groq",
        display_name="Groq",
        default_model="llama-3.3-70b-versatile",
        small_model="llama-3.1-8b-instant",
        api_key_env="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
    ),
    "cerebras": ProviderProfile(
        name="cerebras",
        display_name="Cerebras",
        default_model="llama3.1-70b",
        small_model="llama3.1-8b",
        api_key_env="CEREBRAS_API_KEY",
        base_url="https://api.cerebras.ai/v1",
    ),
    "together": ProviderProfile(
        name="together",
        display_name="Together AI",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        small_model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        api_key_env="TOGETHER_API_KEY",
        base_url="https://api.together.xyz/v1",
    ),
    "fireworks": ProviderProfile(
        name="fireworks",
        display_name="Fireworks AI",
        default_model="accounts/fireworks/models/llama-v3p1-70b-instruct",
        small_model="accounts/fireworks/models/llama-v3p1-8b-instruct",
        api_key_env="FIREWORKS_API_KEY",
        base_url="https://api.fireworks.ai/inference/v1",
    ),
    "deepseek": ProviderProfile(
        name="deepseek",
        display_name="DeepSeek",
        default_model="deepseek-chat",
        small_model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
    ),
    "xai": ProviderProfile(
        name="xai",
        display_name="xAI",
        default_model="grok-4",
        small_model="grok-4-fast",
        api_key_env="XAI_API_KEY",
        base_url="https://api.x.ai/v1",
    ),
    "perplexity": ProviderProfile(
        name="perplexity",
        display_name="Perplexity",
        default_model="sonar-pro",
        small_model="sonar",
        api_key_env="PERPLEXITY_API_KEY",
        base_url="https://api.perplexity.ai",
    ),
    "openrouter": ProviderProfile(
        name="openrouter",
        display_name="OpenRouter",
        default_model="anthropic/claude-sonnet-4.5",
        small_model="openai/gpt-4.1-mini",
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
    ),
    "azure-openai": ProviderProfile(
        name="azure-openai",
        display_name="Azure OpenAI",
        default_model="deployment-name",
        small_model="small-deployment-name",
        api_key_env="AZURE_OPENAI_API_KEY",
        base_url="https://YOUR-RESOURCE.openai.azure.com/openai/v1",
    ),
    "bedrock": ProviderProfile(
        name="bedrock",
        display_name="AWS Bedrock",
        default_model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        small_model="anthropic.claude-3-haiku-20240307-v1:0",
        api_key_env=None,
        protocol="bedrock",
        notes="Uses AWS environment or profile credentials in future provider adapter.",
    ),
    "ollama": ProviderProfile(
        name="ollama",
        display_name="Ollama",
        default_model="llama3.1",
        small_model="llama3.1",
        base_url="http://localhost:11434",
        protocol="ollama",
    ),
    "lmstudio": ProviderProfile(
        name="lmstudio",
        display_name="LM Studio",
        default_model="local-model",
        small_model="local-model",
        base_url="http://localhost:1234/v1",
    ),
    "vllm": ProviderProfile(
        name="vllm",
        display_name="vLLM",
        default_model="served-model",
        small_model="served-model",
        base_url="http://localhost:8000/v1",
    ),
    "generic-openai": ProviderProfile(
        name="generic-openai",
        display_name="Generic OpenAI-Compatible",
        default_model="model-name",
        small_model="small-model-name",
        api_key_env="LORO_API_KEY",
        base_url="https://example.com/v1",
    ),
}


def provider_names() -> list[str]:
    return sorted(PROVIDER_PROFILES)


def get_provider_profile(name: str) -> ProviderProfile:
    try:
        return PROVIDER_PROFILES[name]
    except KeyError as error:
        raise ValueError(f"Unsupported provider: {name}") from error


def model_config_from_profile(
    provider: str,
    *,
    model: str | None = None,
    small_model: str | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
) -> ModelConfig:
    profile = get_provider_profile(provider)
    return ModelConfig(
        provider=profile.name,
        model=model or profile.default_model,
        small_model=small_model or profile.small_model,
        api_key_env=api_key_env if api_key_env is not None else profile.api_key_env,
        base_url=base_url if base_url is not None else profile.base_url,
    )


def write_local_model_config(path: Path, config: LoroConfig) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "model": {
            "provider": config.model.provider,
            "model": config.model.model,
            "small_model": config.model.small_model,
            "timeout_seconds": config.model.timeout_seconds,
            "temperature": config.model.temperature,
        }
    }
    if config.model.api_key_env:
        data["model"]["api_key_env"] = config.model.api_key_env
    if config.model.base_url:
        data["model"]["base_url"] = config.model.base_url
    if config.model.max_tokens:
        data["model"]["max_tokens"] = config.model.max_tokens
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    return path
