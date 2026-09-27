"""Role-based access control for the Web UI API in OIDC (multi-user) mode.

Four roles, from verified identity claims: ``viewer`` reads, ``operator`` also runs agents and
changes conversations and graphs, ``approver`` reads and decides approvals, and ``admin`` does
everything, including settings, profiles, extensions, schedules and memory curation. Operators
cannot approve their own actions unless they also hold the approver role (separation of duties).

Every ``/api/`` route maps to one permission. Unlisted mutating routes require ``admin``, so a
new endpoint is closed until someone classifies it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from loro.config import RBACConfig
from loro.identity import IdentityContext

ROLES = ("viewer", "operator", "approver", "admin")
PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"read"}),
    "operator": frozenset({"read", "operate"}),
    "approver": frozenset({"read", "approve"}),
    "admin": frozenset({"read", "operate", "approve", "admin"}),
}
PERMISSION_LABELS = {
    "read": "View conversations, runs, graphs, memory and governance evidence",
    "operate": "Start conversations and runs, upload files, run graphs, use WebMCP",
    "approve": "Approve or deny protected actions and graph gates",
    "admin": "Change settings, profiles, extensions, schedules, memory, and access rules",
}

_MUTATION_RULES: tuple[tuple[str, str], ...] = (
    (r"^/api/approvals/decisions$", "approve"),
    (r"^/api/runs/[^/]+/approvals/[^/]+$", "approve"),
    (r"^/api/graphs/runs/[^/]+/gates/[^/]+$", "approve"),
    (r"^/api/conversations(/[^/]+)?$", "operate"),
    (r"^/api/conversations/[^/]+/(messages|attachments)$", "operate"),
    (r"^/api/runs/[^/]+/cancel$", "operate"),
    (r"^/api/graphs/(runs|blank|card|document|generate|generate/start)$", "operate"),
    (r"^/api/webmcp/(open|call|close)$", "operate"),
    (r"^/api/governance/(explain|verify)$", "read"),
    (r"^/api/profiles/validate$", "read"),
)


def required_permission(method: str, path: str) -> str:
    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return "admin" if path.startswith("/api/access/admin") else "read"
    for pattern, permission in _MUTATION_RULES:
        if re.fullmatch(pattern, path):
            return permission
    return "admin"


def roles_for(identity: IdentityContext, config: RBACConfig) -> list[str]:
    """Loro roles from the identity's role and group claims, then the default role."""

    names: Iterable[str] = (*identity.roles, *identity.groups)
    found: set[str] = set()
    for name in names:
        mapped = config.mappings.get(name)
        if mapped:
            found.add(mapped)
        elif config.accept_role_names and name in ROLES:
            found.add(name)
    if identity.subject in config.admins:
        found.add("admin")
    if not found and config.default_role:
        found.add(config.default_role)
    return [role for role in ROLES if role in found]


def permissions_for(roles: Iterable[str]) -> list[str]:
    granted: set[str] = set()
    for role in roles:
        granted |= PERMISSIONS.get(role, frozenset())
    return [name for name in ("read", "operate", "approve", "admin") if name in granted]
