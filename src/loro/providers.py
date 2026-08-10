import os
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from loro.config import LoroConfig, ModelConfig, write_config_sections
from loro.credentials import CredentialError, CredentialVault
from loro.provider_profiles import PROVIDER_PROFILES, ProviderProfile


@dataclass(frozen=True)
class ProviderCheck:
    provider: str
    ok: bool
    api_key_env: str | None
    api_key_present: bool
    credential_ref: str | None
    credential_present: bool
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
    credential_ref: str | None = None,
    base_url: str | None = None,
) -> ModelConfig:
    profile = get_provider_profile(provider)
    return ModelConfig(
        provider=profile.name,
        model=model or profile.default_model,
        small_model=small_model or profile.small_model,
        api_key_env=api_key_env if api_key_env is not None else profile.api_key_env,
        credential_ref=credential_ref,
        base_url=base_url if base_url is not None else profile.base_url,
    )


def check_provider_config(config: ModelConfig) -> ProviderCheck:
    profile = get_provider_profile(config.provider)
    api_key_env = config.api_key_env if config.api_key_env is not None else profile.api_key_env
    base_url = config.base_url if config.base_url is not None else profile.base_url
    messages: list[str] = []
    api_key_present = not api_key_env and not config.credential_ref
    credential_present = False
    if api_key_env:
        api_key_present = bool(os.environ.get(api_key_env))
        if api_key_present:
            messages.append(f"Found API key environment variable: {api_key_env}")
        else:
            messages.append(f"Missing API key environment variable: {api_key_env}")
    else:
        messages.append("No API key environment variable required by this profile.")
    if not api_key_present and config.credential_ref:
        try:
            credential_present = CredentialVault().get(config.credential_ref) is not None
        except CredentialError as error:
            messages.append(f"Credential vault unavailable: {error}")
        else:
            messages.append(
                "Found credential vault entry."
                if credential_present
                else "Credential vault entry is missing."
            )
        api_key_present = credential_present
    if base_url:
        messages.append(f"Base URL: {base_url}")
    else:
        messages.append("No base URL configured.")
    if profile.protocol == "bedrock":
        if find_spec("boto3") and find_spec("botocore"):
            messages.append("boto3 and botocore are importable for Bedrock.")
            bedrock_ready = True
        else:
            messages.append("Bedrock requires boto3 and botocore. Install the aws extra.")
            bedrock_ready = False
    else:
        bedrock_ready = True
    ok = api_key_present and bedrock_ready
    return ProviderCheck(
        provider=profile.name,
        ok=ok,
        api_key_env=api_key_env,
        api_key_present=api_key_present,
        credential_ref=config.credential_ref,
        credential_present=credential_present,
        base_url=base_url,
        protocol=profile.protocol,
        messages=messages,
    )


def write_local_model_config(path: Path, config: LoroConfig) -> Path:
    return write_config_sections(path, config, ["model"])
