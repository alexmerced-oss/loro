from __future__ import annotations

import getpass
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any
from uuid import uuid4

from loro.config import IdentityConfig, IdentityField, OIDCConfig


class IdentityConfigurationError(ValueError):
    """Raised when the resolved identity does not satisfy managed requirements."""


@dataclass(frozen=True)
class IdentityContext:
    subject: str
    display_name: str
    organization: str | None
    tenant: str
    groups: tuple[str, ...]
    roles: tuple[str, ...]
    auth_method: str
    session_id: str
    source: str
    # True only when the identity came from a token whose signature and claims Loro checked.
    verified: bool = False

    def to_payload(self) -> dict[str, str | list[str] | None]:
        payload = asdict(self)
        payload["groups"] = list(self.groups)
        payload["roles"] = list(self.roles)
        return payload


@dataclass(frozen=True)
class IdentityDiagnostic:
    context: IdentityContext
    required_fields: tuple[IdentityField, ...]
    missing_fields: tuple[IdentityField, ...]

    @property
    def ok(self) -> bool:
        return not self.missing_fields

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "required_fields": list(self.required_fields),
            "missing_fields": list(self.missing_fields),
            "identity": self.context.to_payload(),
        }


_PROCESS_SESSION_ID = str(uuid4())
_SCALAR_FIELDS = (
    "subject",
    "display_name",
    "organization",
    "tenant",
    "auth_method",
    "session_id",
    "source",
)


def build_identity_context(
    config: IdentityConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> IdentityContext:
    values = environ if environ is not None else os.environ
    env_values = _environment_values(config, values) if config.environment_enabled else {}
    config_values = {
        field: _clean(getattr(config, field))
        for field in _SCALAR_FIELDS
        if _clean(getattr(config, field)) is not None
    }
    effective_environment_fields = set(env_values) - set(config_values)
    if config.groups:
        effective_environment_fields.discard("groups")
    if config.roles:
        effective_environment_fields.discard("roles")
    environment_used = bool(effective_environment_fields)
    configured_identity = bool(config_values or config.groups or config.roles)

    # Resolved configuration wins so managed overlays can lock identity fields.
    merged = {**env_values, **config_values}
    local_user = _local_user()
    subject = str(merged.get("subject") or local_user)
    display_name = str(merged.get("display_name") or subject)
    tenant = str(merged.get("tenant") or "default")
    auth_method = str(merged.get("auth_method") or "os_user")
    source = str(merged.get("source") or "local")
    if environment_used:
        auth_method = str(merged.get("auth_method") or "environment")
        source = str(merged.get("source") or "environment")
    elif configured_identity:
        auth_method = str(merged.get("auth_method") or "configured")
        source = str(merged.get("source") or "config")

    groups = tuple(config.groups) if config.groups else _split_values(env_values.get("groups"))
    roles = tuple(config.roles) if config.roles else _split_values(env_values.get("roles"))
    return IdentityContext(
        subject=subject,
        display_name=display_name,
        organization=_clean(merged.get("organization")),
        tenant=tenant,
        groups=groups,
        roles=roles,
        auth_method=auth_method,
        session_id=str(merged.get("session_id") or _PROCESS_SESSION_ID),
        source=source,
    )


def diagnose_identity(
    config: IdentityConfig,
    *,
    environ: Mapping[str, str] | None = None,
    token: str | None = None,
) -> IdentityDiagnostic:
    values = environ if environ is not None else os.environ
    verified = _verified_context(config, values, token)
    if verified is not None:
        context, asserted = verified
        missing = tuple(field for field in config.required_fields if field not in asserted)
        return IdentityDiagnostic(
            context=context, required_fields=tuple(config.required_fields), missing_fields=missing
        )
    context = build_identity_context(config, environ=values)
    asserted = _asserted_fields(config, values)
    missing = tuple(
        field for field in config.required_fields if field not in asserted
    )
    return IdentityDiagnostic(
        context=context,
        required_fields=tuple(config.required_fields),
        missing_fields=missing,
    )


def resolve_identity(
    config: IdentityConfig,
    *,
    environ: Mapping[str, str] | None = None,
    token: str | None = None,
) -> IdentityContext:
    diagnostic = diagnose_identity(config, environ=environ, token=token)
    if not diagnostic.ok:
        fields = ", ".join(diagnostic.missing_fields)
        raise IdentityConfigurationError(f"Required identity fields are missing: {fields}")
    return diagnostic.context


@lru_cache(maxsize=8)
def _provider_for(serialized: str) -> Any:
    from loro.oidc import OIDCProvider

    return OIDCProvider(OIDCConfig.model_validate_json(serialized))


def oidc_provider(config: OIDCConfig) -> Any:
    """A cached provider per issuer configuration, so JWKS is not refetched on every call."""

    return _provider_for(config.model_dump_json())


def identity_from_claims(
    config: OIDCConfig, claims: Mapping[str, Any], *, session_id: str | None = None
) -> IdentityContext:
    """Map verified token claims onto a Loro identity."""

    from loro.oidc import claim_value, claim_values

    subject = str(claim_value(claims, config.subject_claim))
    return IdentityContext(
        subject=subject,
        display_name=claim_value(claims, config.display_name_claim) or subject,
        organization=claim_value(claims, config.organization_claim),
        tenant=claim_value(claims, config.tenant_claim) or "default",
        groups=claim_values(claims, config.groups_claim),
        roles=claim_values(claims, config.roles_claim),
        auth_method="oidc",
        session_id=session_id or _PROCESS_SESSION_ID,
        source=f"oidc:{str(claims.get('iss', '')).rstrip('/')}",
        verified=True,
    )


def _verified_context(
    config: IdentityConfig, environ: Mapping[str, str], token: str | None
) -> tuple[IdentityContext, set[str]] | None:
    oidc = config.oidc
    if not oidc.enabled:
        return None
    supplied = token or _clean(environ.get(oidc.token_env))
    if not supplied:
        if oidc.required:
            raise IdentityConfigurationError(
                f"A verified identity is required: set {oidc.token_env} to an ID or access "
                f"token from {oidc.issuer}, or sign in through the Web UI."
            )
        return None
    from loro.oidc import OIDCError

    try:
        claims = oidc_provider(oidc).verify(supplied)
    except OIDCError as error:
        raise IdentityConfigurationError(f"OIDC token rejected: {error}") from error
    context = identity_from_claims(oidc, claims)
    asserted = {"subject", "display_name", "auth_method", "session_id", "source"}
    for field, claim in (
        ("tenant", oidc.tenant_claim),
        ("organization", oidc.organization_claim),
        ("groups", oidc.groups_claim),
        ("roles", oidc.roles_claim),
    ):
        if claim and claim_present(claims, claim):
            asserted.add(field)
    return context, asserted


def claim_present(claims: Mapping[str, Any], name: str) -> bool:
    value: Any = claims
    for part in name.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return False
        value = value[part]
    return True


def _environment_values(
    config: IdentityConfig,
    environ: Mapping[str, str],
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for field in (*_SCALAR_FIELDS, "groups", "roles"):
        env_name = f"{config.environment_prefix}{field.upper()}"
        value = _clean(environ.get(env_name))
        if value is not None:
            resolved[field] = value
    return resolved


def _asserted_fields(config: IdentityConfig, environ: Mapping[str, str]) -> set[str]:
    asserted = {
        field for field in _SCALAR_FIELDS if _clean(getattr(config, field)) is not None
    }
    if config.groups:
        asserted.add("groups")
    if config.roles:
        asserted.add("roles")
    if config.environment_enabled:
        asserted.update(_environment_values(config, environ))
    return asserted


def _split_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _local_user() -> str:
    try:
        return getpass.getuser() or "local-user"
    except (KeyError, OSError):
        return "local-user"
