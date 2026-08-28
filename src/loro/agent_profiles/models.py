from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    mcp_servers: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


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
    subagents: list[str] = Field(default_factory=list)
    max_subagent_depth: int | None = Field(default=None, ge=0, le=20)


class MemorySpec(OAPModel):
    stores: list[Literal["oap-state", "local", "shared"]] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)


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
    memory: MemorySpec = Field(default_factory=MemorySpec)
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
    # Exact canonical input retained in memory so editing a profile never drops
    # normative fields that Loro's runtime projection does not consume yet.
    canonical_source: dict[str, Any] | None = Field(default=None, exclude=True)
    api_version: str = Field(default="oap/v1", alias="apiVersion")
    kind: str = "AgentProfile"
    metadata: ProfileMetadata
    extends: list[str] = Field(default_factory=list)
    spec: ProfileSpec = Field(default_factory=ProfileSpec)
    state: list[StateEntry] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)

    @field_validator("extends", mode="before")
    @classmethod
    def _extends(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return [value] if isinstance(value, str) else value

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


class DeltaProposal(OAPModel):
    path: str = Field(pattern=r"^/(metadata|spec)(/.*)?$")
    rationale: str = Field(min_length=1)
    op: Literal["add", "replace", "remove"] = "replace"
    value: Any = None
    risk: Literal["low", "medium", "high"] | None = None


class AgentStateDelta(OAPModel):
    profile: str
    base_revision: int = Field(ge=1)
    spec_digest: str
    session_id: str | None = None
    operations: list[DeltaOperation] = Field(default_factory=list)
    proposals: list[DeltaProposal] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _canonical_input(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("kind") != "AgentStateDelta":
            return value
        target = value.get("target") or {}
        session = value.get("session") or {}
        operations = []
        for operation in value.get("operations") or []:
            item = dict(operation)
            path = str(item.get("path", ""))
            if path.startswith("/state/facts/id:"):
                item["path"] = "/state/" + path.rsplit("id:", 1)[-1]
            payload = item.get("value")
            if isinstance(payload, dict) and "text" in payload:
                item["value"] = {**payload, "content": payload["text"]}
                item["value"].pop("text", None)
            operations.append(item)
        return {
            "profile": target.get("name"),
            "base_revision": target.get("revision"),
            "spec_digest": target.get("digest") or "sha256:" + ("0" * 64),
            "session_id": session.get("id"),
            "operations": operations,
            "proposals": value.get("proposals") or [],
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        operations = []
        for operation in self.operations:
            item = operation.model_dump(mode="json", exclude_none=True)
            entry_id = item["path"].removeprefix("/state/")
            if entry_id and "/" not in entry_id:
                item["path"] = f"/state/facts/id:{entry_id}"
            payload = item.get("value")
            if isinstance(payload, dict) and "content" in payload:
                item["value"] = {**payload, "text": payload["content"]}
                item["value"].pop("content", None)
            operations.append(item)
        return {
            "oap": "1.0",
            "kind": "AgentStateDelta",
            "target": {
                "name": self.profile,
                "revision": self.base_revision,
            },
            "session": {"id": self.session_id or "loro-unknown", "harness": "loro"},
            "operations": operations,
            "proposals": [
                item.model_dump(mode="json", exclude_none=True) for item in self.proposals
            ],
        }
