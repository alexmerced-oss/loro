from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from loro.agent_profiles.registry import AgentProfileRegistry, ResolvedProfile
from loro.config import LoroConfig, ModelConfig, ModelTierConfig, PermissionsConfig, RuntimeConfig
from loro.identity import resolve_identity
from loro.skills import SkillRegistry
from loro.tool_schemas import tool_catalog

_ORDER = {"deny": 0, "ask": 1, "allow": 2}
_WRITEBACK = {"off": 0, "propose": 1, "auto": 2}
_PERMISSION_FIELDS = (
    "default",
    "shell",
    "edit",
    "artifact",
    "shared_memory",
    "governed_data",
    "mcp",
    "skills",
    "session_message",
    "web",
)


@dataclass(frozen=True)
class Adjustment:
    field: str
    requested: Any
    effective: Any
    reason: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "requested": self.requested,
            "effective": self.effective,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EffectiveProfile:
    resolved: ResolvedProfile
    permissions: PermissionsConfig
    tools: frozenset[str]
    model: ModelConfig
    runtime: RuntimeConfig
    writeback: str
    mcp_servers: frozenset[str]
    skills: frozenset[str]
    subagents: frozenset[str]
    max_subagent_depth: int
    memory_stores: frozenset[str]
    memory_scopes: frozenset[str]
    adjustments: tuple[Adjustment, ...]


def narrow_decision(policy: str, requested: str) -> str:
    return min(policy, requested, key=lambda value: _ORDER[value])


def build_effective_profile(resolved: ResolvedProfile, config: LoroConfig) -> EffectiveProfile:
    adjustments: list[Adjustment] = []
    layers = resolved.layers or (resolved.document,)
    permission_data = config.permissions.model_dump()
    for layer in layers:
        requested_permissions = layer.spec.permissions
        for field in _PERMISSION_FIELDS:
            requested = getattr(requested_permissions, field)
            if requested is None:
                continue
            effective = narrow_decision(permission_data[field], requested)
            permission_data[field] = effective
            if effective != requested:
                adjustments.append(
                    Adjustment(
                        field=f"permissions.{field}",
                        requested=requested,
                        effective=effective,
                        reason="managed or inherited policy ceiling",
                    )
                )
        if requested_permissions.workspace_roots:
            roots = _intersect_roots(
                permission_data["workspace_roots"],
                requested_permissions.workspace_roots,
                Path.cwd(),
            )
            permission_data["workspace_roots"] = roots
            if roots != requested_permissions.workspace_roots:
                adjustments.append(
                    Adjustment(
                        "permissions.workspace_roots",
                        requested_permissions.workspace_roots,
                        roots,
                        "workspace containment ceiling",
                    )
                )
    # Profile rules are not merged into the first-match policy engine. Their requested
    # decisions are represented by the scalar ceilings above until a two-policy evaluator
    # can prove each rule no more permissive for every request.
    requested_rules = [rule for layer in layers for rule in layer.spec.permissions.rules]
    if requested_rules:
        adjustments.append(
            Adjustment(
                "permissions.rules",
                requested_rules,
                [],
                "profile rules cannot outrank managed first-match policy",
            )
        )
    permissions = PermissionsConfig.model_validate(permission_data)

    available = {item.name for item in tool_catalog(config)}
    tools = set(available)
    enabled_mcp = {name for name, server in config.mcp.servers.items() if server.enabled}
    mcp_servers = set(enabled_mcp if config.mcp.enabled else ())
    enabled_skills = {
        item.name for item in SkillRegistry(config.skills).discover() if item.state == "enabled"
    }
    skills = set(enabled_skills)
    discovered_profiles = set(resolved.available_profiles)
    if not discovered_profiles:
        discovered_profiles = {
            item.name for item in AgentProfileRegistry(config.agent_profiles).discover()
        }
    available_subagents = discovered_profiles - set(resolved.lineage)
    subagents: set[str] = set()
    subagents_declared = False
    memory_stores = {
        "oap-state",
        *(["local"] if config.memory.local.enabled else []),
        *(["shared"] if config.memory.shared.enabled else []),
    }
    memory_scopes = {resolve_identity(config.identity).tenant}
    for layer in layers:
        tool_spec = layer.spec.tools
        if tool_spec.policy == "allowlist":
            tools &= _expand(tool_spec.allow, available)
        tools -= _expand(tool_spec.deny, available)
        if "mcp_servers" in tool_spec.model_fields_set:
            mcp_servers &= set(tool_spec.mcp_servers)
        if "skills" in tool_spec.model_fields_set:
            skills &= set(tool_spec.skills)
        if "subagents" in layer.spec.runtime.model_fields_set:
            requested_subagents = set(layer.spec.runtime.subagents) & available_subagents
            subagents = (
                subagents & requested_subagents if subagents_declared else requested_subagents
            )
            subagents_declared = True
        if "stores" in layer.spec.memory.model_fields_set:
            memory_stores &= set(layer.spec.memory.stores)
        if "scopes" in layer.spec.memory.model_fields_set:
            memory_scopes &= set(layer.spec.memory.scopes)
    if not memory_scopes:
        memory_stores.discard("shared")

    runtime_data = config.runtime.model_dump()
    max_subagent_depth = config.agent_profiles.max_subagent_depth
    for layer in layers:
        runtime_spec = layer.spec.runtime
        for source, target in (
            ("max_turns", "max_steps"),
            ("max_steps", "max_steps"),
            ("max_tool_calls", "max_tool_calls"),
            ("max_cost_usd", "max_cost_usd"),
        ):
            requested = getattr(runtime_spec, source)
            if requested is None:
                continue
            policy = runtime_data[target]
            effective = requested if policy is None else min(policy, requested)
            runtime_data[target] = effective
            if effective != requested:
                adjustments.append(
                    Adjustment(f"runtime.{source}", requested, effective, "runtime budget ceiling")
                )
        if runtime_spec.max_subagent_depth is not None:
            max_subagent_depth = min(max_subagent_depth, runtime_spec.max_subagent_depth)
    runtime = RuntimeConfig.model_validate(runtime_data)

    model = _resolve_model(resolved, config, adjustments)
    requested_writeback = resolved.document.spec.writeback
    writeback = config.agent_profiles.writeback
    for layer in layers:
        writeback = min(writeback, layer.spec.writeback, key=lambda value: _WRITEBACK[value])
    if len(layers) > 1:
        writeback = "off"
    if writeback != requested_writeback:
        adjustments.append(
            Adjustment("writeback", requested_writeback, writeback, "managed writeback ceiling")
        )
    return EffectiveProfile(
        resolved=resolved,
        permissions=permissions,
        tools=frozenset(tools),
        model=model,
        runtime=runtime,
        writeback=writeback,
        mcp_servers=frozenset(mcp_servers),
        skills=frozenset(skills),
        subagents=frozenset(subagents),
        max_subagent_depth=max_subagent_depth,
        memory_stores=frozenset(memory_stores),
        memory_scopes=frozenset(memory_scopes),
        adjustments=tuple(adjustments),
    )


def effective_config(config: LoroConfig, profile: EffectiveProfile) -> LoroConfig:
    mcp = config.mcp.model_copy(
        update={
            "servers": {
                key: value
                for key, value in config.mcp.servers.items()
                if key in profile.mcp_servers
            }
        }
    )
    memory = config.memory.model_copy(
        update={
            "local": config.memory.local.model_copy(
                update={"enabled": config.memory.local.enabled and "local" in profile.memory_stores}
            ),
            "shared": config.memory.shared.model_copy(
                update={
                    "enabled": config.memory.shared.enabled and "shared" in profile.memory_stores
                }
            ),
        }
    )
    return config.model_copy(
        update={
            "permissions": profile.permissions,
            "model": profile.model,
            "runtime": profile.runtime,
            "mcp": mcp,
            "memory": memory,
        }
    )


def _resolve_model(
    resolved: ResolvedProfile, config: LoroConfig, adjustments: list[Adjustment]
) -> ModelConfig:
    requested = resolved.document.spec.model
    routes = _configured_model_routes(config)
    if requested.provider and requested.id:
        exact = routes.get(f"{requested.provider}/{requested.id}")
        if exact is not None:
            return exact
    if requested.fallbacks:
        fallback = next((item for item in requested.fallbacks if item in routes), None)
        if fallback is not None:
            adjustments.append(
                Adjustment("model", requested.model_dump(), fallback, "first serviceable fallback")
            )
            return routes[fallback]
    if requested.tier and requested.tier in config.model.tiers:
        tier = config.model.tiers[requested.tier]
        adjustments.append(
            Adjustment("model", requested.model_dump(), requested.tier, "configured model tier")
        )
        return _model_from_tier(config.model, tier)
    if any((requested.provider, requested.id, requested.tier, requested.fallbacks)):
        adjustments.append(
            Adjustment(
                "model", requested.model_dump(), config.model.model, "configured default model"
            )
        )
    return config.model


def _configured_model_routes(config: LoroConfig) -> dict[str, ModelConfig]:
    routes = {
        f"{config.model.provider}/{config.model.model}": config.model,
        f"{config.model.provider}/{config.model.small_model}": config.model.model_copy(
            update={"model": config.model.small_model}
        ),
    }
    for tier in config.model.tiers.values():
        routes[f"{tier.provider}/{tier.model}"] = _model_from_tier(config.model, tier)
    return routes


def _model_from_tier(model: ModelConfig, tier: ModelTierConfig) -> ModelConfig:
    return model.model_copy(
        update={
            "provider": tier.provider,
            "model": tier.model,
            "api_key_env": tier.api_key_env,
            "credential_ref": tier.credential_ref,
            "base_url": tier.base_url,
        }
    )


def _expand(patterns: list[str], available: set[str]) -> set[str]:
    return {tool for tool in available if any(fnmatchcase(tool, pattern) for pattern in patterns)}


def _intersect_roots(policy: list[str], requested: list[str], cwd: Path) -> list[str]:
    policy_paths = [
        (cwd / item).resolve() if not Path(item).is_absolute() else Path(item).resolve()
        for item in policy
    ]
    if not policy_paths:
        return []
    result: list[str] = []
    for item in requested:
        path = (cwd / item).resolve() if not Path(item).is_absolute() else Path(item).resolve()
        if any(path == root or path.is_relative_to(root) for root in policy_paths):
            result.append(item)
    return result
