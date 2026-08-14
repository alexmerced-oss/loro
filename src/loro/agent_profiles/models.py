from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OAPModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ProfileMetadata(OAPModel):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    revision: int = Field(default=1, ge=1)
    description: str = ""
    trust: str | None = None


class RoleSpec(OAPModel):
    instructions: str = ""
    objectives: list[str] = Field(default_factory=list)
    persona: str = ""
    constraints: list[str] = Field(default_factory=list)
    examples: list[Any] = Field(default_factory=list)


class ModelSpec(OAPModel):
    provider: str | None = None
    id: str | None = None
    tier: Literal["minimal", "standard", "advanced", "frontier"] | None = None
    fallbacks: list[str] = Field(default_factory=list)


class ToolSpec(OAPModel):
    policy: Literal["allowlist", "denylist", "inherit"] = "inherit"
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class ProfilePermissions(OAPModel):
    default: Literal["allow", "ask", "deny"] | None = None
    shell: Literal["allow", "ask", "deny"] | None = None
    edit: Literal["allow", "ask", "deny"] | None = None
    artifact: Literal["allow", "ask", "deny"] | None = None
    shared_memory: Literal["allow", "ask", "deny"] | None = None
    governed_data: Literal["allow", "ask", "deny"] | None = None
    mcp: Literal["allow", "ask", "deny"] | None = None
    skills: Literal["allow", "ask", "deny"] | None = None
    session_message: Literal["allow", "ask", "deny"] | None = None
    web: Literal["allow", "ask", "deny"] | None = None
    workspace_roots: list[str] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)


class RuntimeSpec(OAPModel):
    max_turns: int | None = Field(default=None, ge=1)
    max_steps: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_cost_usd: float | None = Field(default=None, gt=0)


class ContextFile(OAPModel):
    path: str
    mode: Literal["always", "on_demand"] = "always"


class ContextBudget(OAPModel):
    max_state_tokens: int | None = Field(default=None, ge=0)


class ContextSpec(OAPModel):
    files: list[ContextFile] = Field(default_factory=list)
    budget: ContextBudget = Field(default_factory=ContextBudget)


class ProfileSpec(OAPModel):
    role: RoleSpec = Field(default_factory=RoleSpec)
    model: ModelSpec = Field(default_factory=ModelSpec)
    tools: ToolSpec = Field(default_factory=ToolSpec)
    permissions: ProfilePermissions = Field(default_factory=ProfilePermissions)
    runtime: RuntimeSpec = Field(default_factory=RuntimeSpec)
    context: ContextSpec = Field(default_factory=ContextSpec)
    writeback: Literal["off", "propose", "auto"] = "propose"


class StateEntry(OAPModel):
    id: str
    content: str
    pinned: bool = False
    updated_at: str | None = None


class HistoryEntry(OAPModel):
    revision: int
    session_id: str | None = None
    timestamp: str
    digest: str


class AgentProfileModel(OAPModel):
    api_version: str = Field(default="oap/v1", alias="apiVersion")
    kind: str = "AgentProfile"
    metadata: ProfileMetadata
    spec: ProfileSpec = Field(default_factory=ProfileSpec)
    state: list[StateEntry] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value != "AgentProfile":
            raise ValueError("kind must be AgentProfile")
        return value


class DeltaOperation(OAPModel):
    op: Literal["add", "replace", "remove"]
    path: str
    value: Any = None


class AgentStateDelta(OAPModel):
    profile: str
    base_revision: int = Field(ge=1)
    spec_digest: str
    session_id: str | None = None
    operations: list[DeltaOperation] = Field(default_factory=list)
    proposals: list[DeltaOperation] = Field(default_factory=list)
