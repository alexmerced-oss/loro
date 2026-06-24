from loro.config import LoroConfig
from loro.providers import (
    get_provider_profile,
    model_config_from_profile,
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


def test_model_config_from_profile() -> None:
    config = model_config_from_profile("openai")
    assert config.provider == "openai"
    assert config.api_key_env == "OPENAI_API_KEY"
    assert config.base_url == "https://api.openai.com/v1"


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
