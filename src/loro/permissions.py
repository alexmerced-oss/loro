from dataclasses import dataclass
from typing import Literal

from loro.config import PermissionsConfig

Decision = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class PermissionRequest:
    tool: str
    action: str
    target: str | None = None


@dataclass(frozen=True)
class PermissionResult:
    decision: Decision
    reason: str


class PermissionEngine:
    def __init__(self, config: PermissionsConfig) -> None:
        self.config = config

    def evaluate(self, request: PermissionRequest) -> PermissionResult:
        decision = getattr(self.config, request.tool, self.config.default)
        return PermissionResult(decision=decision, reason=f"{request.tool} uses configured policy")

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
