from pathlib import Path

import pytest

from loro.config import load_config
from loro.identity import IdentityConfigurationError, resolve_identity


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


def test_managed_config_content_is_non_overridable(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[permissions]\nshell = "allow"\nweb = "allow"\n',
    )
    monkeypatch.setenv(
        "LORO_MANAGED_CONFIG_CONTENT",
        '[permissions]\nshell = "deny"\nweb = "deny"\n',
    )

    config = load_config(Path.cwd())

    assert config.permissions.shell == "deny"
    assert config.permissions.web == "deny"


def test_managed_config_file_is_applied_after_runtime_config(tmp_path, monkeypatch) -> None:
    runtime_config = tmp_path / "runtime.toml"
    managed_config = tmp_path / "managed.toml"
    runtime_config.write_text('[audit]\ninclude_prompt_preview = true\n', encoding="utf-8")
    managed_config.write_text('[audit]\ninclude_prompt_preview = false\n', encoding="utf-8")
    monkeypatch.setenv("LORO_CONFIG", str(runtime_config))
    monkeypatch.setenv("LORO_MANAGED_CONFIG", str(managed_config))
    monkeypatch.delenv("LORO_CONFIG_CONTENT", raising=False)

    config = load_config(Path.cwd())

    assert config.audit.include_prompt_preview is False


def test_permission_rules_load_from_config_content(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        """
        [[permissions.rules]]
        tool = "edit"
        action = "read*"
        target = "docs/*"
        decision = "allow"
        reason = "docs are readable"
        """,
    )
    config = load_config(Path.cwd())
    assert len(config.permissions.rules) == 1
    assert config.permissions.rules[0].tool == "edit"
    assert config.permissions.rules[0].decision == "allow"


def test_managed_identity_required_fields_cannot_be_removed(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[identity]\nrequired_fields = []\nsubject = "project-user"\n',
    )
    monkeypatch.setenv(
        "LORO_MANAGED_CONFIG_CONTENT",
        '[identity]\nrequired_fields = ["organization"]\n',
    )

    config = load_config(Path.cwd())

    assert config.identity.required_fields == ["organization"]
    with pytest.raises(IdentityConfigurationError, match="organization"):
        resolve_identity(config.identity, environ={})
