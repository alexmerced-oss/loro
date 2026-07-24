import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


PermissionDecision = Literal["allow", "ask", "deny"]


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


class PermissionsConfig(BaseModel):
    default: PermissionDecision = "ask"
    shell: PermissionDecision = "ask"
    edit: PermissionDecision = "ask"
    web: PermissionDecision = "deny"
    rules: list["PermissionRuleConfig"] = Field(default_factory=list)


class PermissionRuleConfig(BaseModel):
    tool: str = "*"
    action: str = "*"
    target: str = "*"
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


class AuditConfig(BaseModel):
    enabled: bool = True
    path: str = "~/.local/state/loro/audit.jsonl"
    include_prompt_preview: bool = True


class SessionConfig(BaseModel):
    path: str = ".loro/sessions"


class SafetyConfig(BaseModel):
    enabled: bool = True
    block_on_findings: bool = True


class LoroConfig(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    polaris: PolarisConfig = Field(default_factory=PolarisConfig)
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


def config_paths(project_root: Path | None = None) -> list[Path]:
    root = project_root or Path.cwd()
    return [
        Path("/etc/loro/config.toml"),
        Path.home() / ".config" / "loro" / "config.toml",
        root / ".loro" / "config.toml",
        root / ".loro" / "config.local.toml",
    ]


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
    return LoroConfig.model_validate(data)
