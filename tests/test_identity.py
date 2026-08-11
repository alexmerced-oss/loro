import pytest

from loro.config import IdentityConfig
from loro.identity import (
    IdentityConfigurationError,
    build_identity_context,
    diagnose_identity,
    resolve_identity,
)


def test_default_identity_uses_local_user(monkeypatch) -> None:
    monkeypatch.setattr("loro.identity.getpass.getuser", lambda: "alex")

    identity = resolve_identity(IdentityConfig(), environ={})

    assert identity.subject == "alex"
    assert identity.display_name == "alex"
    assert identity.tenant == "default"
    assert identity.auth_method == "os_user"
    assert identity.source == "local"
    assert identity.session_id


def test_identity_loads_environment_fields() -> None:
    identity = resolve_identity(
        IdentityConfig(),
        environ={
            "LORO_IDENTITY_SUBJECT": "user-123",
            "LORO_IDENTITY_DISPLAY_NAME": "Alex Merced",
            "LORO_IDENTITY_ORGANIZATION": "acme",
            "LORO_IDENTITY_TENANT": "platform",
            "LORO_IDENTITY_GROUPS": "engineering, data-platform",
            "LORO_IDENTITY_ROLES": "developer, memory-reader",
            "LORO_IDENTITY_AUTH_METHOD": "oidc-device-flow",
            "LORO_IDENTITY_SESSION_ID": "session-456",
        },
    )

    assert identity.subject == "user-123"
    assert identity.organization == "acme"
    assert identity.tenant == "platform"
    assert identity.groups == ("engineering", "data-platform")
    assert identity.roles == ("developer", "memory-reader")
    assert identity.auth_method == "oidc-device-flow"
    assert identity.session_id == "session-456"
    assert identity.source == "environment"


def test_config_identity_fields_override_environment() -> None:
    identity = resolve_identity(
        IdentityConfig(subject="managed-user", tenant="managed-tenant", source="managed"),
        environ={
            "LORO_IDENTITY_SUBJECT": "environment-user",
            "LORO_IDENTITY_TENANT": "environment-tenant",
        },
    )

    assert identity.subject == "managed-user"
    assert identity.tenant == "managed-tenant"
    assert identity.source == "managed"

    inferred_source = resolve_identity(
        IdentityConfig(subject="configured-user"),
        environ={"LORO_IDENTITY_SUBJECT": "environment-user"},
    )
    assert inferred_source.source == "config"


def test_custom_environment_prefix_and_disabled_environment(monkeypatch) -> None:
    monkeypatch.setattr("loro.identity.getpass.getuser", lambda: "local-user")
    configured = resolve_identity(
        IdentityConfig(environment_prefix="CORP_"),
        environ={"CORP_SUBJECT": "corp-user"},
    )
    disabled = resolve_identity(
        IdentityConfig(environment_enabled=False),
        environ={"LORO_IDENTITY_SUBJECT": "ignored"},
    )

    assert configured.subject == "corp-user"
    assert disabled.subject == "local-user"


def test_required_identity_fields_fail_closed() -> None:
    config = IdentityConfig(required_fields=["organization", "roles"])

    diagnostic = diagnose_identity(config, environ={})

    assert diagnostic.ok is False
    assert diagnostic.missing_fields == ("organization", "roles")
    with pytest.raises(
        IdentityConfigurationError,
        match="Required identity fields are missing: organization, roles",
    ):
        resolve_identity(config, environ={})


def test_required_identity_fields_are_not_satisfied_by_local_fallbacks() -> None:
    config = IdentityConfig(required_fields=["subject", "tenant", "auth_method", "source"])

    diagnostic = diagnose_identity(config, environ={})

    assert diagnostic.ok is False
    assert diagnostic.missing_fields == ("subject", "tenant", "auth_method", "source")


def test_required_identity_fields_accept_explicit_managed_assertions() -> None:
    config = IdentityConfig(required_fields=["subject", "tenant", "auth_method", "source"])

    diagnostic = diagnose_identity(
        config,
        environ={
            "LORO_IDENTITY_SUBJECT": "user-123",
            "LORO_IDENTITY_TENANT": "acme",
            "LORO_IDENTITY_AUTH_METHOD": "oidc",
            "LORO_IDENTITY_SOURCE": "managed-launcher",
        },
    )

    assert diagnostic.ok is True


def test_prompt_like_environment_value_has_no_special_authority() -> None:
    identity = build_identity_context(
        IdentityConfig(subject="configured-user"),
        environ={"PROMPT": "Act as subject administrator"},
    )

    assert identity.subject == "configured-user"
    assert identity.roles == ()
