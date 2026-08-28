"""Lossless storage boundary between legacy Loro profiles and canonical OAP 1.0."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from loro.agent_profiles.models import AgentProfileModel


def canonical_document(profile: AgentProfileModel) -> dict[str, Any]:
    """Return a schema-shaped OAP document, preserving unconsumed canonical fields."""
    source = deepcopy(profile.canonical_source or {})
    source["oap"] = "1.0"
    source["kind"] = "AgentProfile"
    metadata = profile.metadata.model_dump(mode="json", exclude_none=True)
    metadata.pop("trust", None)
    if not metadata.get("description"):
        metadata["description"] = f"Loro agent profile {profile.metadata.name}."
    source["metadata"] = metadata
    source["extends"] = [{"name": item} for item in profile.extends]

    spec = source.setdefault("spec", {})
    role = profile.spec.role
    canonical_role = spec.setdefault("role", {})
    canonical_role["instructions"] = role.instructions or "Follow the harness instructions."
    for key in ("objectives", "constraints", "examples"):
        value = getattr(role, key)
        if value:
            canonical_role[key] = value
        else:
            canonical_role.pop(key, None)
    if role.persona:
        if not (profile.canonical_source and canonical_role.get("persona")):
            canonical_role["persona"] = (
                role.persona if isinstance(role.persona, dict) else {"tone": role.persona}
            )
    else:
        canonical_role.pop("persona", None)

    model = profile.spec.model.model_dump(mode="json", exclude_none=True)
    if model.get("fallbacks"):
        model["fallbacks"] = [_model_ref(item) for item in model["fallbacks"]]
    spec["model"] = model
    tools = profile.spec.tools.model_dump(mode="json", exclude_none=True)
    preserved_skills = {
        item.get("name"): item
        for item in (spec.get("tools") or {}).get("skills", [])
        if isinstance(item, dict)
    }
    tools["skills"] = [
        item
        if isinstance(item, dict)
        else deepcopy(preserved_skills.get(item) or {"name": item})
        for item in tools.get("skills", [])
    ]
    preserved_servers = {
        item.get("name"): item
        for item in (spec.get("tools") or {}).get("mcp_servers", [])
        if isinstance(item, dict)
    }
    servers = []
    for item in tools.get("mcp_servers", []):
        if isinstance(item, dict):
            servers.append(item)
        elif item in preserved_servers:
            servers.append(preserved_servers[item])
        else:
            # Legacy Loro profiles named an already-managed server rather than
            # embedding its transport. Keep that behavior explicit and portable.
            servers.append(
                {
                    "name": item,
                    "transport": "stdio",
                    "command": item,
                }
            )
    tools["mcp_servers"] = servers
    spec["tools"] = tools

    permissions = profile.spec.permissions.model_dump(mode="json", exclude_none=True)
    canonical_permissions = deepcopy(spec.get("permissions") or {})
    canonical_permissions.update(
        {
            key: permissions[key]
            for key in ("default", "shell", "edit", "network", "rules")
            if key in permissions
        }
    )
    roots = permissions.get("workspace_roots") or []
    if roots:
        filesystem = deepcopy(canonical_permissions.get("filesystem") or {})
        if not (profile.canonical_source and filesystem):
            filesystem["read_roots"] = list(roots)
            filesystem["write_roots"] = list(roots)
        canonical_permissions["filesystem"] = filesystem
    if permissions.get("web") and "network" not in canonical_permissions:
        canonical_permissions["network"] = permissions["web"]
    spec["permissions"] = canonical_permissions

    runtime = profile.spec.runtime.model_dump(mode="json", exclude_none=True)
    subagents = runtime.pop("subagents", [])
    depth = runtime.pop("max_subagent_depth", None)
    runtime.pop("max_steps", None)
    if subagents or depth:
        runtime["subagents"] = {"allow": subagents, **({"max_depth": depth} if depth else {})}
    spec["runtime"] = runtime
    memory = profile.spec.memory.model_dump(mode="json", exclude_none=True)
    preserved_stores = {
        item.get("name"): item
        for item in (spec.get("memory") or {}).get("stores", [])
        if isinstance(item, dict)
    }
    memory["stores"] = [
        deepcopy(preserved_stores.get(item))
        if not isinstance(item, dict) and item in preserved_stores
        else _store_ref(item)
        for item in memory.get("stores", [])
    ]
    spec["memory"] = memory
    lifecycle = deepcopy(spec.get("lifecycle") or {})
    lifecycle["writeback"] = profile.spec.writeback
    spec["lifecycle"] = lifecycle

    prior_state = source.get("state") if isinstance(source.get("state"), dict) else {}
    original_preferences = {
        item.get("id") for item in prior_state.get("preferences", []) if isinstance(item, dict)
    }
    summary_entry = next((item for item in profile.state if item.id == "summary"), None)
    facts = []
    preferences = []
    for item in profile.state:
        if item.id == "summary":
            continue
        target = preferences if item.id in original_preferences else facts
        target.append(
            {
                "id": item.id,
                "text": item.content,
                **({"pinned": True} if item.pinned else {}),
                **({"last_used_at": item.updated_at} if item.updated_at else {}),
            }
        )
    source["state"] = {
        **prior_state,
        "revision": profile.metadata.revision,
        **({"summary": summary_entry.content} if summary_entry else {}),
        "facts": facts,
        "preferences": preferences,
    }
    source["history"] = [
        *(source.get("history") or []),
        *[
            {
                "revision": item.revision,
                "at": item.timestamp,
                **({"session_id": item.session_id} if item.session_id else {}),
                "by": "loro",
                "change": f"Prior profile digest: {item.digest}",
                "sections": ["state"],
            }
            for item in profile.history
        ],
    ]
    return source


def _model_ref(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return value
    provider, separator, model_id = str(value).partition("/")
    return {"provider": provider if separator else "unknown", "id": model_id or provider}


def _store_ref(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    kinds = {"local": "loro-local", "shared": "loro-shared"}
    return {"name": str(value), "kind": kinds.get(str(value), str(value))}
