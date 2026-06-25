from loro.config import LoroConfig
from loro.providers import (
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
    assert config.base_url == "https://inference.nousresearch.com/v1"


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
