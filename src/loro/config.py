import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import tomli_w
from pydantic import BaseModel, Field, field_validator, model_validator

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


PermissionDecision = Literal["allow", "ask", "deny"]
IdentityField = Literal[
    "subject",
    "display_name",
    "organization",
    "tenant",
    "groups",
    "roles",
    "auth_method",
    "session_id",
    "source",
]


class ModelConfig(BaseModel):
    provider: str = "mock"
    model: str = "mock-agent"
    small_model: str = "mock-small"
    api_key_env: str | None = None
    base_url: str | None = None
    timeout_seconds: int = 120
    temperature: float = 0.2
    max_tokens: int | None = None


class RuntimeConfig(BaseModel):
    max_steps: int = 5


class IdentityConfig(BaseModel):
    subject: str | None = None
    display_name: str | None = None
    organization: str | None = None
    tenant: str | None = None
    groups: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    auth_method: str | None = None
    session_id: str | None = None
    source: str | None = None
    environment_enabled: bool = True
    environment_prefix: str = "LORO_IDENTITY_"
    required_fields: list[IdentityField] = Field(default_factory=list)


class PermissionsConfig(BaseModel):
    version: str = "local-v1"
    default: PermissionDecision = "ask"
    shell: PermissionDecision = "ask"
    edit: PermissionDecision = "ask"
    shared_memory: PermissionDecision = "ask"
    governed_data: PermissionDecision = "allow"
    mcp: PermissionDecision = "ask"
    web: PermissionDecision = "deny"
    workspace_roots: list[str] = Field(default_factory=list)
    rules: list["PermissionRuleConfig"] = Field(default_factory=list)


class ApprovalsConfig(BaseModel):
    interactive: bool = True
    allow_non_interactive: bool = True
    allow_session_scope: bool = True
    once_ttl_seconds: int = Field(default=300, ge=1)
    session_ttl_seconds: int = Field(default=900, ge=1)


class PermissionRuleConfig(BaseModel):
    tool: str = "*"
    action: str = "*"
    target: str = "*"
    resource_kind: str = "*"
    resource: dict[str, str] = Field(default_factory=dict)
    decision: PermissionDecision
    reason: str | None = None


class LocalMemoryConfig(BaseModel):
    enabled: bool = True
    path: str = "~/.local/share/loro/memory"
    auto_propose: bool = True


class SharedMemoryConfig(BaseModel):
    enabled: bool = False
    backend: Literal["postgres", "iceberg"] = "postgres"
    write_policy: str = "explicit_user_dictation_only"
    read_policy: str = "semantic_retrieval_with_citations"
    postgres_dsn_env: str = "LORO_POSTGRES_DSN"
    postgres_schema: str = "public"
    iceberg_catalog_name: str = "default"
    iceberg_catalog_uri_env: str = "LORO_ICEBERG_CATALOG_URI"
    iceberg_credential_env: str = "LORO_ICEBERG_CREDENTIAL"
    iceberg_token_env: str = "LORO_ICEBERG_TOKEN"
    iceberg_warehouse: str | None = None
    iceberg_namespace: str = "agent_memory"
    iceberg_table: str = "shared_memories"


class MemoryConfig(BaseModel):
    local: LocalMemoryConfig = Field(default_factory=LocalMemoryConfig)
    shared: SharedMemoryConfig = Field(default_factory=SharedMemoryConfig)


class PolarisConfig(BaseModel):
    enabled: bool = False
    cli_path: str = "polaris"
    realm: str | None = None
    catalog: str | None = None
    require_role_inspection: bool = True


class MCPServerConfig(BaseModel):
    enabled: bool = True
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    cwd: str | None = None
    env_allowlist: list[str] = Field(default_factory=list)
    protocol_mode: str = "auto"
    allowed_protocol_versions: list[str] = Field(
        default_factory=lambda: ["2026-07-28", "2025-11-25", "2024-11-05"]
    )
    minimum_protocol_version: str | None = None
    timeout_seconds: float = Field(default=30, gt=0, le=300)

    @field_validator("command", "url", "cwd")
    @classmethod
    def _normalize_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("env_allowlist")
    @classmethod
    def _validate_environment_names(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            name = value.strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
                raise ValueError(f"Invalid environment variable name: {value!r}")
            if name not in normalized:
                normalized.append(name)
        return normalized

    @field_validator("allowed_protocol_versions")
    @classmethod
    def _validate_allowed_versions(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("MCP allowed_protocol_versions cannot be empty.")
        if any(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None for value in normalized):
            raise ValueError("MCP protocol versions must use YYYY-MM-DD format.")
        return normalized

    @field_validator("protocol_mode")
    @classmethod
    def _validate_protocol_mode(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"auto", "legacy"} and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", normalized
        ) is None:
            raise ValueError("MCP protocol_mode must be auto, legacy, or YYYY-MM-DD.")
        return normalized

    @model_validator(mode="after")
    def _validate_transport(self) -> "MCPServerConfig":
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("MCP stdio server requires command.")
            if self.url:
                raise ValueError("MCP stdio server cannot define url.")
        else:
            if not self.url:
                raise ValueError("MCP Streamable HTTP server requires url.")
            if self.command or self.args or self.cwd or self.env_allowlist:
                raise ValueError(
                    "MCP Streamable HTTP server cannot define command, args, cwd, or env_allowlist."
                )
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(
                    "MCP Streamable HTTP URL must be an absolute http:// or https:// URL."
                )
            if parsed.username or parsed.password:
                raise ValueError("MCP Streamable HTTP URL cannot contain credentials.")
        if self.minimum_protocol_version:
            if self.minimum_protocol_version not in self.allowed_protocol_versions:
                raise ValueError(
                    "MCP minimum_protocol_version must be present in allowed_protocol_versions."
                )
        return self


class MCPConfig(BaseModel):
    enabled: bool = False
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)

    @field_validator("servers")
    @classmethod
    def _validate_server_ids(
        cls, servers: dict[str, MCPServerConfig]
    ) -> dict[str, MCPServerConfig]:
        for server_id in servers:
            if not server_id or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
                for character in server_id
            ):
                raise ValueError(
                    "MCP server ids must use lowercase letters, numbers, hyphens, or underscores."
                )
        return servers


class AuditConfig(BaseModel):
    enabled: bool = True
    schema_version: str = "1.0"
    sink: Literal["jsonl", "http"] = "jsonl"
    path: str = "~/.local/state/loro/audit.jsonl"
    include_prompt_preview: bool = True
    http_url: str | None = None
    http_token_env: str | None = None
    failure_mode: Literal["warn", "fail"] = "warn"
    buffer_path: str = "~/.local/state/loro/audit-buffer.jsonl"
    max_buffer_events: int = Field(default=1000, ge=1)
    max_retries: int = Field(default=2, ge=0, le=10)
    backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    timeout_seconds: float = Field(default=10, gt=0, le=300)


class SessionConfig(BaseModel):
    path: str = ".loro/sessions"


class SafetyConfig(BaseModel):
    enabled: bool = True
    block_on_findings: bool = True


class LoroConfig(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    approvals: ApprovalsConfig = Field(default_factory=ApprovalsConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    polaris: PolarisConfig = Field(default_factory=PolarisConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    sessions: SessionConfig = Field(default_factory=SessionConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as file:
        return tomllib.load(file)


def _config_section_data(config: LoroConfig, section: str) -> dict[str, Any]:
    if section == "model":
        data: dict[str, Any] = {
            "provider": config.model.provider,
            "model": config.model.model,
            "small_model": config.model.small_model,
            "timeout_seconds": config.model.timeout_seconds,
            "temperature": config.model.temperature,
        }
        if config.model.api_key_env:
            data["api_key_env"] = config.model.api_key_env
        if config.model.base_url:
            data["base_url"] = config.model.base_url
        if config.model.max_tokens:
            data["max_tokens"] = config.model.max_tokens
        return {"model": data}
    if section == "identity":
        return {"identity": config.identity.model_dump(exclude_none=True)}
    if section == "approvals":
        return {"approvals": config.approvals.model_dump(exclude_none=True)}
    if section == "memory.local":
        return {"memory": {"local": config.memory.local.model_dump(exclude_none=True)}}
    if section == "memory.shared":
        return {"memory": {"shared": config.memory.shared.model_dump(exclude_none=True)}}
    if section == "polaris":
        return {"polaris": config.polaris.model_dump(exclude_none=True)}
    if section == "mcp":
        return {"mcp": config.mcp.model_dump(exclude_none=True)}
    if section == "audit":
        return {"audit": config.audit.model_dump(exclude_none=True)}
    raise ValueError(f"Unsupported config section: {section}")


def write_config_sections(path: Path, config: LoroConfig, sections: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_toml(path)
    for section in sections:
        data = _merge(data, _config_section_data(config, section))
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    return path


def config_paths(project_root: Path | None = None) -> list[Path]:
    root = project_root or Path.cwd()
    return [
        Path("/etc/loro/config.toml"),
        Path.home() / ".config" / "loro" / "config.toml",
        root / ".loro" / "config.toml",
        root / ".loro" / "config.local.toml",
    ]


def managed_config_paths() -> list[Path]:
    return [Path("/etc/loro/managed.toml")]


def load_config(project_root: Path | None = None) -> LoroConfig:
    data: dict[str, Any] = {}
    for path in config_paths(project_root):
        data = _merge(data, _read_toml(path))
    env_config = os.environ.get("LORO_CONFIG")
    if env_config:
        data = _merge(data, _read_toml(Path(env_config).expanduser()))
    env_content = os.environ.get("LORO_CONFIG_CONTENT")
    if env_content:
        data = _merge(data, tomllib.loads(env_content))
    managed_data: dict[str, Any] = {}
    for path in managed_config_paths():
        managed_data = _merge(managed_data, _read_toml(path))
    managed_env_config = os.environ.get("LORO_MANAGED_CONFIG")
    if managed_env_config:
        managed_data = _merge(managed_data, _read_toml(Path(managed_env_config).expanduser()))
    managed_env_content = os.environ.get("LORO_MANAGED_CONFIG_CONTENT")
    if managed_env_content:
        managed_data = _merge(managed_data, tomllib.loads(managed_env_content))
    data = _merge(data, managed_data)
    return LoroConfig.model_validate(data)
