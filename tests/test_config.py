from pathlib import Path

from loro.config import load_config


def test_load_project_config() -> None:
    config = load_config(Path.cwd())
    assert config.model.provider == "mock"
    assert config.permissions.web == "deny"
    assert config.memory.shared.write_policy == "explicit_user_dictation_only"


def test_loro_config_content_override(monkeypatch) -> None:
    monkeypatch.setenv("LORO_CONFIG_CONTENT", "[model]\nprovider = \"env-provider\"\n")
    config = load_config(Path.cwd())
    assert config.model.provider == "env-provider"
