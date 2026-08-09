from pathlib import Path

import pytest

from loro.config import ManagedConfigIntegrityError, load_config, managed_config_digest
from loro.identity import IdentityConfigurationError, resolve_identity


def test_load_project_config() -> None:
    config = load_config(Path.cwd())
    assert config.model.provider == "mock"
    assert config.permissions.web == "deny"
    assert config.permissions.mcp == "ask"
    assert config.mcp.enabled is False
    assert config.memory.shared.write_policy == "explicit_user_dictation_only"
    assert config.safety.enabled is True


def test_loro_config_content_override(monkeypatch) -> None:
    monkeypatch.setenv("LORO_CONFIG_CONTENT", '[model]\nprovider = "env-provider"\n')
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


def test_managed_config_digest_is_verified_before_merge(monkeypatch) -> None:
    content = b'[permissions]\nshell = "deny"\n'
    label = "environment:LORO_MANAGED_CONFIG_CONTENT"
    monkeypatch.setenv("LORO_MANAGED_CONFIG_CONTENT", content.decode())
    monkeypatch.setenv(
        "LORO_MANAGED_CONFIG_SHA256",
        managed_config_digest([(label, content)]),
    )
    assert load_config(Path.cwd()).permissions.shell == "deny"

    monkeypatch.setenv("LORO_MANAGED_CONFIG_SHA256", "sha256:" + "0" * 64)
    with pytest.raises(ManagedConfigIntegrityError, match="digest mismatch"):
        load_config(Path.cwd())


def test_required_managed_config_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("LORO_MANAGED_CONFIG", raising=False)
    monkeypatch.delenv("LORO_MANAGED_CONFIG_CONTENT", raising=False)
    monkeypatch.delenv("LORO_MANAGED_CONFIG_SHA256", raising=False)
    monkeypatch.setenv("LORO_MANAGED_CONFIG_REQUIRED", "true")

    with pytest.raises(ManagedConfigIntegrityError, match="required"):
        load_config(Path.cwd())


def test_managed_config_file_is_applied_after_runtime_config(tmp_path, monkeypatch) -> None:
    runtime_config = tmp_path / "runtime.toml"
    managed_config = tmp_path / "managed.toml"
    runtime_config.write_text("[audit]\ninclude_prompt_preview = true\n", encoding="utf-8")
    managed_config.write_text("[audit]\ninclude_prompt_preview = false\n", encoding="utf-8")
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


def test_structured_permission_rule_and_policy_version_load(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        """
        [permissions]
        version = "enterprise-7"
        workspace_roots = ["/workspace"]

        [[permissions.rules]]
        tool = "governed_data"
        action = "tables"
        resource_kind = "polaris"
        decision = "allow"

        [permissions.rules.resource]
        catalog = "prod"
        namespace = "analytics"
        """,
    )

    config = load_config(Path.cwd())

    assert config.permissions.version == "enterprise-7"
    assert config.permissions.workspace_roots == ["/workspace"]
    assert config.permissions.rules[0].resource == {
        "catalog": "prod",
        "namespace": "analytics",
    }


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


def test_managed_config_can_disable_non_interactive_approvals(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[approvals]\nallow_non_interactive = true\nallow_session_scope = true\n",
    )
    monkeypatch.setenv(
        "LORO_MANAGED_CONFIG_CONTENT",
        "[approvals]\nallow_non_interactive = false\nallow_session_scope = false\n",
    )

    config = load_config(Path.cwd())

    assert config.approvals.allow_non_interactive is False
    assert config.approvals.allow_session_scope is False


def test_managed_sandbox_profile_is_non_overridable(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[sandbox.profiles.controlled-shell]\nbackend = "process"\n'
        'require_os_enforcement = false\nnetwork = "inherit"\n',
    )
    monkeypatch.setenv(
        "LORO_MANAGED_CONFIG_CONTENT",
        '[sandbox.profiles.controlled-shell]\nbackend = "bubblewrap"\n'
        'require_os_enforcement = true\nnetwork = "deny"\n',
    )

    config = load_config(Path.cwd())
    profile = config.sandbox.profiles["controlled-shell"]

    assert profile.backend == "bubblewrap"
    assert profile.require_os_enforcement is True
    assert profile.network == "deny"


def test_managed_mcp_protocol_minimum_is_non_overridable(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[mcp]\nenabled = true\n"
        '[mcp.servers.catalog]\ntransport = "streamable_http"\n'
        'url = "https://mcp.example/mcp"\nminimum_protocol_version = "2024-11-05"\n',
    )
    monkeypatch.setenv(
        "LORO_MANAGED_CONFIG_CONTENT",
        '[mcp.servers.catalog]\nminimum_protocol_version = "2025-11-25"\n',
    )

    config = load_config(Path.cwd())

    assert config.mcp.servers["catalog"].minimum_protocol_version == "2025-11-25"


def test_external_audit_configuration_loads(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        """
        [audit]
        schema_version = "1.0"
        sink = "http"
        http_url = "https://audit.example/events"
        http_token_env = "LORO_AUDIT_TOKEN"
        failure_mode = "fail"
        buffer_path = "/tmp/loro-audit-buffer.jsonl"
        max_buffer_events = 250
        max_retries = 4
        backoff_seconds = 0.5
        timeout_seconds = 15
        """,
    )

    config = load_config(Path.cwd())

    assert config.audit.sink == "http"
    assert config.audit.failure_mode == "fail"
    assert config.audit.max_buffer_events == 250
    assert config.audit.max_retries == 4


def test_managed_data_protection_policy_is_non_overridable(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[safety]\nallow_sensitive_override = true\n"
        '[safety.surfaces.model_input]\nmaximum_classification = "restricted"\n',
    )
    monkeypatch.setenv(
        "LORO_MANAGED_CONFIG_CONTENT",
        "[safety]\nallow_sensitive_override = false\n"
        '[safety.surfaces.model_input]\nmaximum_classification = "internal"\n',
    )

    config = load_config(Path.cwd())

    assert config.safety.allow_sensitive_override is False
    assert config.safety.surfaces["model_input"].maximum_classification == "internal"
    assert config.safety.surfaces["artifact"].action == "block"


def test_managed_tenant_isolation_is_non_overridable(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[memory.shared]\ntenant_isolation = "disabled"\n',
    )
    monkeypatch.setenv(
        "LORO_MANAGED_CONFIG_CONTENT",
        '[memory.shared]\ntenant_isolation = "identity"\n',
    )

    config = load_config(Path.cwd())

    assert config.memory.shared.tenant_isolation == "identity"
