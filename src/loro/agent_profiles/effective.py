from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from loro.agent_profiles.registry import ResolvedProfile
from loro.config import LoroConfig, ModelConfig, ModelTierConfig, PermissionsConfig, RuntimeConfig
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
    adjustments: tuple[Adjustment, ...]


def narrow_decision(policy: str, requested: str) -> str:
    return min(policy, requested, key=lambda value: _ORDER[value])


def build_effective_profile(resolved: ResolvedProfile, config: LoroConfig) -> EffectiveProfile:
    adjustments: list[Adjustment] = []
    requested_permissions = resolved.document.spec.permissions
    permission_data = config.permissions.model_dump()
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
                    reason="managed policy ceiling",
                )
            )
    if requested_permissions.workspace_roots:
        roots = _intersect_roots(
            config.permissions.workspace_roots,
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
    if requested_permissions.rules:
        adjustments.append(
            Adjustment(
                "permissions.rules",
                requested_permissions.rules,
                [],
                "profile rules cannot outrank managed first-match policy",
            )
        )
    permissions = PermissionsConfig.model_validate(permission_data)

    available = {item.name for item in tool_catalog(config)}
    tool_spec = resolved.document.spec.tools
    if tool_spec.policy == "allowlist":
        tools = _expand(tool_spec.allow, available)
    else:
        tools = set(available)
    tools -= _expand(tool_spec.deny, available)
    requested_tools = (
        sorted(_expand(tool_spec.allow, available)) if tool_spec.allow else sorted(available)
    )
    if sorted(tools) != requested_tools:
        adjustments.append(Adjustment("tools", requested_tools, sorted(tools), "tool intersection"))

    runtime_data = config.runtime.model_dump()
    runtime_spec = resolved.document.spec.runtime
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
    runtime = RuntimeConfig.model_validate(runtime_data)

    model = _resolve_model(resolved, config, adjustments)
    requested_writeback = resolved.document.spec.writeback
    writeback = min(
        config.agent_profiles.writeback, requested_writeback, key=lambda value: _WRITEBACK[value]
    )
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
        adjustments=tuple(adjustments),
    )


def effective_config(config: LoroConfig, profile: EffectiveProfile) -> LoroConfig:
    return config.model_copy(
        update={
            "permissions": profile.permissions,
            "model": profile.model,
            "runtime": profile.runtime,
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
