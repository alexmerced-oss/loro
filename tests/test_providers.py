import httpx
import pytest

from loro.config import LoroConfig
from loro.providers import (
    ModelDiscoveryError,
    check_provider_config,
    discover_provider_models,
    get_provider_profile,
    model_config_from_profile,
    provider_aliases,
    provider_names,
    write_local_model_config,
)


def _catalog_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_provider_profiles_include_common_targets() -> None:
    names = provider_names()
    assert "openai" in names
    assert "anthropic" in names
    assert "gemini" in names
    assert "ollama" in names
    assert "generic-openai" in names
    assert "nous" in names
    assert "opencode-zen" in names
    assert "opencode-go" in names
    assert "trustedrouter" in names
    assert "prime-intellect" in names


def test_model_config_from_profile() -> None:
    config = model_config_from_profile("openai")
    assert config.provider == "openai"
    assert config.api_key_env == "OPENAI_API_KEY"
    assert config.base_url == "https://api.openai.com/v1"


def test_nous_profile_matches_hermes_plugin_metadata() -> None:
    config = model_config_from_profile("nous-portal")
    assert config.provider == "nous"
    assert config.api_key_env == "NOUS_API_KEY"
    assert config.base_url == "https://inference-api.nousresearch.com/v1"


def test_opencode_profiles_match_hermes_plugin_metadata() -> None:
    zen = model_config_from_profile("zen")
    go = model_config_from_profile("go")
    assert zen.provider == "opencode-zen"
    assert zen.api_key_env == "OPENCODE_ZEN_API_KEY"
    assert zen.base_url == "https://opencode.ai/zen/v1"
    assert go.provider == "opencode-go"
    assert go.api_key_env == "OPENCODE_GO_API_KEY"
    assert go.base_url == "https://opencode.ai/zen/go/v1"


def test_trustedrouter_and_prime_intellect_profiles_match_public_api_contracts() -> None:
    trusted = model_config_from_profile("trusted-provider")
    prime = model_config_from_profile("prime")

    assert trusted.provider == "trustedrouter"
    assert trusted.api_key_env == "TRUSTEDROUTER_API_KEY"
    assert trusted.base_url == "https://api.trustedrouter.com/v1"
    assert trusted.model == "trustedrouter/cheap"
    assert prime.provider == "prime-intellect"
    assert prime.api_key_env == "PRIME_API_KEY"
    assert prime.base_url == "https://api.pinference.ai/api/v1"
    assert prime.small_model == "openai/gpt-oss-20b"


def test_provider_aliases() -> None:
    aliases = provider_aliases()
    assert aliases["nous-portal"] == "nous"
    assert aliases["opencode"] == "opencode-zen"
    assert aliases["opencode-go-sub"] == "opencode-go"
    assert aliases["trusted"] == "trustedrouter"
    assert aliases["pinference"] == "prime-intellect"


def test_get_provider_profile_rejects_unknown() -> None:
    try:
        get_provider_profile("nope")
    except ValueError as error:
        assert "Unsupported provider" in str(error)
    else:
        raise AssertionError("Expected ValueError")


def test_write_local_model_config(tmp_path) -> None:
    config = LoroConfig()
    config.model = model_config_from_profile("ollama", model="llama3.2")
    path = write_local_model_config(tmp_path / "config.local.toml", config)
    text = path.read_text(encoding="utf-8")
    assert 'provider = "ollama"' in text
    assert 'model = "llama3.2"' in text


def test_check_provider_config_missing_key(monkeypatch) -> None:
    monkeypatch.delenv("NOUS_API_KEY", raising=False)
    config = model_config_from_profile("nous")
    check = check_provider_config(config)
    assert check.ok is False
    assert check.api_key_env == "NOUS_API_KEY"


def test_check_provider_config_present_key(monkeypatch) -> None:
    monkeypatch.setenv("NOUS_API_KEY", "test-key")
    config = model_config_from_profile("nous")
    check = check_provider_config(config)
    assert check.ok is True
    assert check.api_key_present is True
    assert "No API key environment variable required by this profile." not in check.messages


def test_check_provider_config_uses_named_vault_fallback(monkeypatch) -> None:
    class Vault:
        def get(self, ref: str) -> str:
            assert ref == "vault://provider/nous/work"
            return "vault-value"

    monkeypatch.delenv("NOUS_API_KEY", raising=False)
    monkeypatch.setattr("loro.providers.CredentialVault", Vault)
    config = model_config_from_profile("nous", credential_ref="vault://provider/nous/work")

    check = check_provider_config(config)

    assert check.ok is True
    assert check.credential_present is True


def test_discovers_openai_compatible_models_with_auth(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "catalog-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/models"
        assert request.headers["Authorization"] == "Bearer catalog-key"
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-test"}, {"id": "gpt-small"}]},
        )

    with _catalog_client(handler) as client:
        catalog = discover_provider_models("openai", http_client=client)

    assert catalog.models == ("gpt-test", "gpt-small")


def test_discovers_public_opencode_go_models_without_auth(monkeypatch) -> None:
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://opencode.ai/zen/go/v1/models"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"id": "glm-5"}, {"id": "kimi-k3"}]})

    with _catalog_client(handler) as client:
        catalog = discover_provider_models("opencode-go", http_client=client)

    assert catalog.models == ("glm-5", "kimi-k3")


def test_discovers_anthropic_models_with_native_headers(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.anthropic.com/v1/models"
        assert request.headers["x-api-key"] == "claude-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(200, json={"data": [{"id": "claude-test"}]})

    with _catalog_client(handler) as client:
        catalog = discover_provider_models("anthropic", http_client=client)

    assert catalog.models == ("claude-test",)


def test_discovers_only_generation_capable_gemini_models(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["pageSize"] == "1000"
        assert request.headers["x-goog-api-key"] == "gemini-key"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-test",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/text-embedding-test",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )

    with _catalog_client(handler) as client:
        catalog = discover_provider_models("gemini", http_client=client)

    assert catalog.models == ("gemini-test",)


def test_discovers_ollama_tags() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://localhost:11434/api/tags"
        return httpx.Response(200, json={"models": [{"model": "llama3.2:latest"}]})

    with _catalog_client(handler) as client:
        catalog = discover_provider_models("ollama", http_client=client)

    assert catalog.models == ("llama3.2:latest",)


def test_discovers_bedrock_text_models() -> None:
    class Bedrock:
        def list_foundation_models(self, **kwargs):
            assert kwargs == {"byOutputModality": "TEXT"}
            return {
                "modelSummaries": [
                    {"modelId": "anthropic.claude-test-v1"},
                    {"modelId": "amazon.nova-test-v1"},
                ]
            }

    catalog = discover_provider_models("bedrock", bedrock_client=Bedrock())

    assert catalog.models == ("anthropic.claude-test-v1", "amazon.nova-test-v1")


def test_model_discovery_rejects_placeholder_and_bad_catalogs() -> None:
    with pytest.raises(ModelDiscoveryError, match="real base URL"):
        discover_provider_models("generic-openai")

    with _catalog_client(lambda _request: httpx.Response(200, json={"data": "invalid"})) as client:
        with pytest.raises(ModelDiscoveryError, match="missing the 'data' list"):
            discover_provider_models("openai", http_client=client)
