import os
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from loro.config import LoroConfig, ModelConfig
from loro.provider_profiles import PROVIDER_PROFILES, ProviderProfile


@dataclass(frozen=True)
class ProviderCheck:
    provider: str
    ok: bool
    api_key_env: str | None
    api_key_present: bool
    base_url: str | None
    protocol: str
    messages: list[str]


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
