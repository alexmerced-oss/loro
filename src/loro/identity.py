from __future__ import annotations

import getpass
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from uuid import uuid4

from loro.config import IdentityConfig, IdentityField


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
) -> IdentityDiagnostic:
    values = environ if environ is not None else os.environ
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
) -> IdentityContext:
    diagnostic = diagnose_identity(config, environ=environ)
    if not diagnostic.ok:
        fields = ", ".join(diagnostic.missing_fields)
        raise IdentityConfigurationError(f"Required identity fields are missing: {fields}")
    return diagnostic.context


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
