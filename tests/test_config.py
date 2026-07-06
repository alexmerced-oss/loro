from pathlib import Path

from loro.config import load_config


def test_load_project_config() -> None:
    config = load_config(Path.cwd())
    assert config.model.provider == "mock"
    assert config.permissions.web == "deny"
    assert config.memory.shared.write_policy == "explicit_user_dictation_only"
    assert config.safety.enabled is True


def test_loro_config_content_override(monkeypatch) -> None:
    monkeypatch.setenv("LORO_CONFIG_CONTENT", "[model]\nprovider = \"env-provider\"\n")
    config = load_config(Path.cwd())
    assert config.model.provider == "env-provider"


def test_loro_config_file_override(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[model]\nprovider = "file-provider"\n', encoding="utf-8")
    monkeypatch.setenv("LORO_CONFIG", str(config_path))
    monkeypatch.delenv("LORO_CONFIG_CONTENT", raising=False)
    config = load_config(Path.cwd())
    assert config.model.provider == "file-provider"
