import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

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


@dataclass(frozen=True)
class ModelCatalog:
    provider: str
    models: tuple[str, ...]
    source: str


class ModelDiscoveryError(RuntimeError):
    """A provider catalog could not be loaded safely."""


MODEL_CATALOG_TIMEOUT_SECONDS = 5.0
MODEL_CATALOG_MAX_BYTES = 2_000_000
MODEL_CATALOG_MAX_MODELS = 5_000


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


def discover_provider_models(
    provider: str,
    *,
    api_key_env: str | None = None,
    credential_ref: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float = MODEL_CATALOG_TIMEOUT_SECONDS,
    http_client: httpx.Client | None = None,
    bedrock_client: Any | None = None,
) -> ModelCatalog:
    """Load the live model catalog exposed by a configured provider."""

    profile = get_provider_profile(provider)
    if profile.protocol == "local" or profile.name == "mock":
        return ModelCatalog(profile.name, profile.model_choices, "bundled local catalog")
    if profile.protocol == "bedrock":
        return _discover_bedrock_models(profile, bedrock_client=bedrock_client)

    resolved_base_url = (base_url or profile.base_url or "").rstrip("/")
    _validate_catalog_base_url(profile, resolved_base_url)
    key = _provider_api_key(
        api_key_env if api_key_env is not None else profile.api_key_env,
        credential_ref,
    )
    url, headers, params, response_kind = _catalog_request(profile, resolved_base_url, key)
    headers.update(
        {
            header: value
            for header, environment_name in profile.optional_header_env
            if (value := os.environ.get(environment_name))
        }
    )

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
    try:
        response = client.get(url, headers=headers, params=params)
        response.raise_for_status()
        if len(response.content) > MODEL_CATALOG_MAX_BYTES:
            raise ModelDiscoveryError("The provider model catalog exceeded the 2 MB limit.")
        try:
            payload = response.json()
        except ValueError as error:
            raise ModelDiscoveryError(
                "The provider returned malformed model catalog JSON."
            ) from error
    except ModelDiscoveryError:
        raise
    except httpx.HTTPStatusError as error:
        raise ModelDiscoveryError(
            f"Model discovery returned HTTP {error.response.status_code}."
        ) from error
    except httpx.HTTPError as error:
        raise ModelDiscoveryError(f"Model discovery failed: {type(error).__name__}.") from error
    finally:
        if owns_client:
            client.close()

    models = _parse_catalog_models(payload, response_kind=response_kind)
    if not models:
        raise ModelDiscoveryError("The provider returned no usable generation models.")
    return ModelCatalog(profile.name, models, url)


def _provider_api_key(api_key_env: str | None, credential_ref: str | None) -> str | None:
    if api_key_env and (value := os.environ.get(api_key_env)):
        return value
    if credential_ref:
        try:
            return CredentialVault().get(credential_ref)
        except CredentialError as error:
            raise ModelDiscoveryError(f"Credential vault lookup failed: {error}") from error
    return None


def _validate_catalog_base_url(profile: ProviderProfile, base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ModelDiscoveryError("The provider does not have a valid HTTP model catalog URL.")
    if profile.name == "generic-openai" and base_url == profile.base_url:
        raise ModelDiscoveryError("Set a real base URL to discover generic provider models.")
    if profile.name == "azure-openai" and "YOUR-RESOURCE" in base_url:
        raise ModelDiscoveryError("Set your Azure resource base URL to discover deployments.")


def _catalog_request(
    profile: ProviderProfile,
    base_url: str,
    api_key: str | None,
) -> tuple[str, dict[str, str], dict[str, str], str]:
    headers = {"Accept": "application/json"}
    params: dict[str, str] = {}
    if profile.protocol == "anthropic":
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        return f"{base_url}/v1/models", headers, params, "openai"
    if profile.protocol == "gemini":
        if api_key:
            headers["x-goog-api-key"] = api_key
        params["pageSize"] = "1000"
        return f"{base_url}/v1beta/models", headers, params, "gemini"
    if profile.protocol == "ollama":
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return f"{base_url}/api/tags", headers, params, "ollama"
    if api_key:
        header = "api-key" if profile.name == "azure-openai" else "Authorization"
        headers[header] = api_key if header == "api-key" else f"Bearer {api_key}"
    return f"{base_url}/models", headers, params, "openai"


def _parse_catalog_models(payload: object, *, response_kind: str) -> tuple[str, ...]:
    if not isinstance(payload, Mapping):
        raise ModelDiscoveryError("The provider model catalog root is not an object.")
    collection_name = "data" if response_kind == "openai" else "models"
    entries = payload.get(collection_name)
    if not isinstance(entries, list):
        raise ModelDiscoveryError(
            f"The provider model catalog is missing the {collection_name!r} list."
        )
    models: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if response_kind == "gemini":
            methods = entry.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            candidate = entry.get("baseModelId") or entry.get("name")
            if isinstance(candidate, str):
                candidate = candidate.removeprefix("models/")
        elif response_kind == "ollama":
            candidate = entry.get("model") or entry.get("name")
        else:
            candidate = entry.get("id")
        if isinstance(candidate, str):
            normalized = candidate.strip()
            if normalized and normalized not in models:
                models.append(normalized)
        if len(models) >= MODEL_CATALOG_MAX_MODELS:
            break
    return tuple(models)


def _discover_bedrock_models(
    profile: ProviderProfile,
    *,
    bedrock_client: Any | None,
) -> ModelCatalog:
    client = bedrock_client
    if client is None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as error:
            raise ModelDiscoveryError(
                "Install the 'aws' extra to discover Bedrock models."
            ) from error
        client = boto3.client(
            "bedrock",
            config=Config(
                connect_timeout=MODEL_CATALOG_TIMEOUT_SECONDS,
                read_timeout=MODEL_CATALOG_TIMEOUT_SECONDS,
                retries={"max_attempts": 1},
            ),
        )
    try:
        payload = client.list_foundation_models(byOutputModality="TEXT")
    except Exception as error:
        raise ModelDiscoveryError(
            f"Bedrock model discovery failed: {type(error).__name__}."
        ) from error
    summaries = payload.get("modelSummaries") if isinstance(payload, Mapping) else None
    models = tuple(
        dict.fromkeys(
            str(summary["modelId"]).strip()
            for summary in summaries or []
            if isinstance(summary, Mapping) and summary.get("modelId")
        )
    )
    if not models:
        raise ModelDiscoveryError("Bedrock returned no usable text generation models.")
    return ModelCatalog(profile.name, models[:MODEL_CATALOG_MAX_MODELS], "AWS ListFoundationModels")


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
    for header, environment_name in profile.optional_header_env:
        if os.environ.get(environment_name):
            messages.append(f"Using optional {header} from {environment_name}.")
        else:
            messages.append(
                f"Optional provider header environment variable not set: {environment_name}"
            )
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
