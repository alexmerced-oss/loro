import os
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
    aliases: tuple[str, ...] = ()
    api_key_env: str | None = None
    base_url: str | None = None
    protocol: str = "openai-compatible"
    notes: str = ""


@dataclass(frozen=True)
class ProviderCheck:
    provider: str
    ok: bool
    api_key_env: str | None
    api_key_present: bool
    base_url: str | None
    protocol: str
    messages: list[str]


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
    "nous": ProviderProfile(
        name="nous",
        display_name="Nous Research",
        default_model="hermes-3-405b",
        small_model="hermes-3-70b",
        aliases=("nous-portal", "nousresearch"),
        api_key_env="NOUS_API_KEY",
        base_url="https://inference.nousresearch.com/v1",
        notes=(
            "Mirrors Hermes' Nous provider profile: Nous Portal / Nous Research "
            "Hermes model family."
        ),
    ),
    "opencode-zen": ProviderProfile(
        name="opencode-zen",
        display_name="OpenCode Zen",
        default_model="opencode-zen-selected-model",
        small_model="gemini-3-flash",
        aliases=("opencode", "opencode_zen", "zen"),
        api_key_env="OPENCODE_ZEN_API_KEY",
        base_url="https://opencode.ai/zen/v1",
        notes=(
            "OpenCode team provider. Pick a concrete model from the OpenCode "
            "Zen catalog after connecting."
        ),
    ),
    "opencode-go": ProviderProfile(
        name="opencode-go",
        display_name="OpenCode Go",
        default_model="opencode-go-selected-model",
        small_model="glm-5",
        aliases=("opencode_go", "go", "opencode-go-sub"),
        api_key_env="OPENCODE_GO_API_KEY",
        base_url="https://opencode.ai/zen/go/v1",
        notes=(
            "Low-cost OpenCode subscription provider. Pick a concrete model from "
            "the OpenCode Go catalog after connecting."
        ),
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


def provider_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for name, profile in PROVIDER_PROFILES.items():
        aliases[name] = name
        for alias in profile.aliases:
            aliases[alias] = name
    return aliases


def get_provider_profile(name: str) -> ProviderProfile:
    canonical = provider_aliases().get(name, name)
    try:
        return PROVIDER_PROFILES[canonical]
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


def check_provider_config(config: ModelConfig) -> ProviderCheck:
    profile = get_provider_profile(config.provider)
    api_key_env = config.api_key_env if config.api_key_env is not None else profile.api_key_env
    base_url = config.base_url if config.base_url is not None else profile.base_url
    messages: list[str] = []
    api_key_present = True
    if api_key_env:
        api_key_present = bool(os.environ.get(api_key_env))
        if api_key_present:
            messages.append(f"Found API key environment variable: {api_key_env}")
        else:
            messages.append(f"Missing API key environment variable: {api_key_env}")
    else:
        messages.append("No API key environment variable required by this profile.")
    if base_url:
        messages.append(f"Base URL: {base_url}")
    else:
        messages.append("No base URL configured.")
    if profile.protocol == "bedrock":
        messages.append("Bedrock profile exists, but runtime adapter is not implemented yet.")
    ok = api_key_present and profile.protocol != "bedrock"
    return ProviderCheck(
        provider=profile.name,
        ok=ok,
        api_key_env=api_key_env,
        api_key_present=api_key_present,
        base_url=base_url,
        protocol=profile.protocol,
        messages=messages,
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
