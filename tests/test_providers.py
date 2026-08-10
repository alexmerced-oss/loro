from loro.config import LoroConfig
from loro.providers import (
    check_provider_config,
    get_provider_profile,
    model_config_from_profile,
    provider_aliases,
    provider_names,
    write_local_model_config,
)


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


def test_provider_aliases() -> None:
    aliases = provider_aliases()
    assert aliases["nous-portal"] == "nous"
    assert aliases["opencode"] == "opencode-zen"
    assert aliases["opencode-go-sub"] == "opencode-go"


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
