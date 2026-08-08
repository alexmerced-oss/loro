import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Literal

from loro.config import PermissionsConfig
from loro.resources import NormalizedResource

Decision = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class PermissionRequest:
    tool: str
    action: str
    target: str | None = None
    resource: NormalizedResource | None = None


@dataclass(frozen=True)
class PermissionResult:
    decision: Decision
    reason: str
    policy_version: str
    policy_source: str
    matched_rule: int | None
    normalized_resource: NormalizedResource | None


class PermissionEngine:
    def __init__(self, config: PermissionsConfig) -> None:
        self.config = config

    def evaluate(self, request: PermissionRequest) -> PermissionResult:
        target = request.target or (request.resource.target if request.resource is not None else "")
        for index, rule in enumerate(self.config.rules):
            if _matches(rule.tool, request.tool) and _matches(rule.action, request.action):
                if _matches(rule.target, target) and _matches_resource(
                    rule.resource_kind,
                    rule.resource,
                    request.resource,
                ):
                    reason = rule.reason or "matched configured permission rule"
                    return PermissionResult(
                        decision=rule.decision,
                        reason=reason,
                        policy_version=self.config.version,
                        policy_source=f"permissions.rules[{index}]",
                        matched_rule=index,
                        normalized_resource=request.resource,
                    )
        decision = getattr(self.config, request.tool, self.config.default)
        return PermissionResult(
            decision=decision,
            reason=f"{request.tool} uses configured policy",
            policy_version=self.config.version,
            policy_source=f"permissions.{request.tool}",
            matched_rule=None,
            normalized_resource=request.resource,
        )

    def require_allowed(
        self,
        request: PermissionRequest,
        approved: bool = False,
    ) -> PermissionResult:
        result = self.evaluate(request)
        if result.decision == "deny":
            raise PermissionError(f"{request.tool} is denied by policy: {request.action}")
        if result.decision == "ask" and not approved:
            raise PermissionError(
                f"{request.tool} requires approval. Re-run with an explicit approval flag."
            )
        return result


def _matches(pattern: str, value: str) -> bool:
    return fnmatchcase(value.casefold(), pattern.casefold())


def _matches_resource(
    kind_pattern: str,
    field_patterns: dict[str, str],
    resource: NormalizedResource | None,
) -> bool:
    if kind_pattern != "*" or field_patterns:
        if resource is None or not _matches(kind_pattern, resource.kind):
            return False
    if resource is None:
        return True
    for field, pattern in field_patterns.items():
        if field not in resource.fields:
            return False
        value = _field_value(resource.fields[field])
        if not _resource_field_matches(resource.kind, field, pattern, value):
            return False
    return True


def _field_value(value: Any) -> str:
    if isinstance(value, list | dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return str(value)


def _resource_field_matches(kind: str, field: str, pattern: str, value: str) -> bool:
    if kind in {"filesystem", "git"} and field in {
        "path",
        "workspace_root",
        "repository",
        "paths",
    }:
        return fnmatchcase(value, pattern)
    return _matches(pattern, value)
