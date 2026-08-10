import hashlib
import os
import re
import secrets
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


class ManagedConfigIntegrityError(ValueError):
    """Raised when a required or digest-pinned managed policy cannot be verified."""


class ModelTierConfig(BaseModel):
    provider: str
    model: str
    context_tokens: int | None = Field(default=None, ge=1)
    api_key_env: str | None = None
    credential_ref: str | None = None
    base_url: str | None = None


class ModelConfig(BaseModel):
    provider: str = "mock"
    model: str = "mock-agent"
    small_model: str = "mock-small"
    api_key_env: str | None = None
    credential_ref: str | None = None
    base_url: str | None = None
    timeout_seconds: int = 120
    max_retries: int = Field(default=2, ge=0, le=10)
    backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    verify_tls: bool = True
    ca_bundle_env: str | None = None
    proxy_env: str | None = None
    temperature: float = 0.2
    max_tokens: int | None = None
    input_cost_per_million: float = Field(default=0, ge=0)
    output_cost_per_million: float = Field(default=0, ge=0)
    tiers: dict[Literal["minimal", "standard", "advanced", "frontier"], ModelTierConfig] = Field(
        default_factory=dict
    )


class RuntimeConfig(BaseModel):
    max_steps: int = 5
    max_tool_calls: int = Field(default=50, ge=0, le=10_000)
    max_model_input_bytes: int = Field(default=2_000_000, ge=1024, le=100_000_000)
    max_model_output_bytes: int = Field(default=1_000_000, ge=1024, le=100_000_000)
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, gt=0)


class SandboxProfileConfig(BaseModel):
    backend: Literal["process", "bubblewrap"] = "process"
    require_os_enforcement: bool = False
    network: Literal["inherit", "deny"] = "inherit"
    allowed_executables: list[str] = Field(default_factory=lambda: ["*"])
    environment_allowlist: list[str] = Field(
        default_factory=lambda: ["PATH", "LANG", "LC_ALL", "TMPDIR"]
    )
    writable_roots: list[str] = Field(default_factory=list)
    max_seconds: int = Field(default=120, ge=1, le=3600)
    max_output_bytes: int = Field(default=1_000_000, ge=1024, le=100_000_000)

    @field_validator("environment_allowlist")
    @classmethod
    def _validate_environment_allowlist(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            name = value.strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
                raise ValueError(f"Invalid sandbox environment variable name: {value!r}")
            if name not in normalized:
                normalized.append(name)
        return normalized

    @field_validator("allowed_executables", "writable_roots")
    @classmethod
    def _normalize_nonempty_values(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _default_sandbox_profiles() -> dict[str, SandboxProfileConfig]:
    return {
        "controlled-shell": SandboxProfileConfig(),
        "git": SandboxProfileConfig(
            allowed_executables=["git"],
        ),
        "governed-data": SandboxProfileConfig(
            allowed_executables=["polaris"],
            environment_allowlist=["PATH", "LANG", "LC_ALL"],
            max_output_bytes=1_000_000,
        ),
        "mcp-stdio": SandboxProfileConfig(
            environment_allowlist=["PATH", "LANG", "LC_ALL"],
            max_output_bytes=1_000_000,
        ),
        "skill-script": SandboxProfileConfig(
            allowed_executables=["python*", "sh"],
            max_seconds=60,
            max_output_bytes=250_000,
        ),
    }


class SandboxConfig(BaseModel):
    enabled: bool = True
    shell_profile: str = "controlled-shell"
    git_profile: str = "git"
    governed_data_profile: str = "governed-data"
    mcp_stdio_profile: str = "mcp-stdio"
    skill_profile: str = "skill-script"
    profiles: dict[str, SandboxProfileConfig] = Field(default_factory=_default_sandbox_profiles)

    @model_validator(mode="before")
    @classmethod
    def _supply_selected_default_profiles(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "profiles" not in value:
            return value
        data = dict(value)
        profiles = dict(data.get("profiles") or {})
        defaults = _default_sandbox_profiles()
        for selected in (
            str(data.get("shell_profile", "controlled-shell")),
            str(data.get("git_profile", "git")),
            str(data.get("governed_data_profile", "governed-data")),
            str(data.get("mcp_stdio_profile", "mcp-stdio")),
            str(data.get("skill_profile", "skill-script")),
        ):
            if selected not in profiles and selected in defaults:
                profiles[selected] = defaults[selected]
        data["profiles"] = profiles
        return data

    @field_validator(
        "shell_profile",
        "git_profile",
        "governed_data_profile",
        "mcp_stdio_profile",
        "skill_profile",
    )
    @classmethod
    def _normalize_profile_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Sandbox profile name cannot be empty.")
        return normalized

    @model_validator(mode="after")
    def _validate_selected_profiles(self) -> "SandboxConfig":
        missing = {
            name
            for name in (
                self.shell_profile,
                self.git_profile,
                self.governed_data_profile,
                self.mcp_stdio_profile,
                self.skill_profile,
            )
            if name not in self.profiles
        }
        if missing:
            raise ValueError("Unknown sandbox profiles: " + ", ".join(sorted(missing)))
        return self


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
    skills: PermissionDecision = "ask"
    session_message: PermissionDecision = "ask"
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
    tenant_isolation: Literal["disabled", "identity"] = "disabled"
    write_policy: str = "explicit_user_dictation_only"
    read_policy: str = "semantic_retrieval_with_citations"
    retention_days: int | None = Field(default=None, ge=1, le=3650)
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
    credential_profile: str | None = None
    extensions: list[str] = Field(default_factory=list)

    @field_validator("command", "url", "cwd", "credential_profile")
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

    @field_validator("extensions")
    @classmethod
    def _normalize_extensions(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("protocol_mode")
    @classmethod
    def _validate_protocol_mode(cls, value: str) -> str:
        normalized = value.strip()
        if (
            normalized not in {"auto", "legacy"}
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized) is None
        ):
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


class MCPCredentialProfileConfig(BaseModel):
    type: Literal["bearer", "oauth_client_credentials", "oauth_authorization_code"]
    token_env: str | None = None
    client_id_env: str | None = None
    client_secret_env: str | None = None
    scopes: list[str] = Field(default_factory=list)
    redirect_uri: str = "http://127.0.0.1:8765/callback"
    client_metadata_url: str | None = None
    allow_dynamic_client_registration: bool = False

    @field_validator("token_env", "client_id_env", "client_secret_env")
    @classmethod
    def _validate_credential_environment_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = value.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise ValueError(f"Invalid credential environment variable name: {value!r}")
        return name

    @field_validator("scopes")
    @classmethod
    def _normalize_scopes(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def _validate_profile(self) -> "MCPCredentialProfileConfig":
        if self.type == "bearer" and not self.token_env:
            raise ValueError("MCP bearer credential profile requires token_env.")
        if self.type == "oauth_client_credentials" and (
            not self.client_id_env or not self.client_secret_env
        ):
            raise ValueError(
                "MCP OAuth client-credentials profile requires client_id_env and client_secret_env."
            )
        if self.type == "oauth_authorization_code":
            parsed_redirect = urlsplit(self.redirect_uri)
            if parsed_redirect.scheme not in {"http", "https"} or not parsed_redirect.hostname:
                raise ValueError("MCP OAuth redirect_uri must be an absolute HTTP(S) URL.")
            if not self.client_metadata_url and not self.allow_dynamic_client_registration:
                raise ValueError(
                    "MCP authorization-code profile requires client_metadata_url unless "
                    "allow_dynamic_client_registration is explicitly enabled."
                )
            if self.client_metadata_url:
                metadata = urlsplit(self.client_metadata_url)
                if (
                    metadata.scheme != "https"
                    or not metadata.hostname
                    or metadata.path in {"", "/"}
                ):
                    raise ValueError("MCP client_metadata_url must be HTTPS with a non-root path.")
        return self


class MCPExtensionConfig(BaseModel):
    enabled: bool = True
    version: str
    adapter: Literal["tasks"] | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    settings_schema: dict[str, Any] | None = None

    @field_validator("version")
    @classmethod
    def _normalize_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("MCP extension version cannot be empty.")
        return normalized


class MCPServerModeConfig(BaseModel):
    enabled: bool = False
    transport: Literal["stdio", "streamable_http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = Field(default=8766, ge=1, le=65535)
    export_tools: list[str] = Field(default_factory=list)
    export_resources: bool = True
    export_prompts: bool = True

    @field_validator("export_tools")
    @classmethod
    def _normalize_export_tools(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def _validate_listener(self) -> "MCPServerModeConfig":
        if self.transport == "streamable_http" and self.host not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError(
                "MCP server mode binds to loopback only; deploy behind an authenticated "
                "enterprise gateway for remote access."
            )
        return self


class MCPConfig(BaseModel):
    enabled: bool = False
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    credential_profiles: dict[str, MCPCredentialProfileConfig] = Field(default_factory=dict)
    extensions: dict[str, MCPExtensionConfig] = Field(default_factory=dict)
    allowed_extensions: list[str] = Field(default_factory=list)
    allowed_hosts: list[str] = Field(default_factory=list)
    require_https: bool = False
    allow_loopback_http: bool = True
    block_private_networks: bool = False
    follow_redirects: bool = False
    allowed_stdio_commands: list[str] = Field(default_factory=list)
    max_output_bytes: int = Field(default=1_000_000, ge=1024, le=100_000_000)
    max_pagination_pages: int = Field(default=20, ge=1, le=1000)
    allow_input_required: bool = False
    input_required_max_rounds: int = Field(default=3, ge=1, le=20)
    task_store_path: str = ".loro/mcp-tasks"
    subscription_max_events: int = Field(default=100, ge=1, le=10_000)
    subscription_max_seconds: float = Field(default=30, gt=0, le=3600)
    server: MCPServerModeConfig = Field(default_factory=MCPServerModeConfig)

    @field_validator("task_store_path")
    @classmethod
    def _validate_task_store_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("MCP task_store_path cannot be empty.")
        return normalized

    @field_validator("servers")
    @classmethod
    def _validate_server_ids(
        cls, servers: dict[str, MCPServerConfig]
    ) -> dict[str, MCPServerConfig]:
        for server_id in servers:
            if not server_id or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in server_id
            ):
                raise ValueError(
                    "MCP server ids must use lowercase letters, numbers, hyphens, or underscores."
                )
        return servers

    @field_validator("credential_profiles")
    @classmethod
    def _validate_profile_ids(
        cls, profiles: dict[str, MCPCredentialProfileConfig]
    ) -> dict[str, MCPCredentialProfileConfig]:
        for profile_id in profiles:
            if not profile_id or re.fullmatch(r"[a-z0-9_-]+", profile_id) is None:
                raise ValueError(
                    "MCP credential profile ids must use lowercase letters, numbers, "
                    "hyphens, or underscores."
                )
        return profiles

    @field_validator("extensions")
    @classmethod
    def _validate_extension_ids(
        cls, extensions: dict[str, MCPExtensionConfig]
    ) -> dict[str, MCPExtensionConfig]:
        for extension_id in extensions:
            if (
                not extension_id
                or "/" not in extension_id
                or re.fullmatch(r"[A-Za-z0-9._/-]+", extension_id) is None
            ):
                raise ValueError("MCP extension ids must be namespaced identifiers containing '/'.")
        return extensions

    @field_validator("allowed_extensions")
    @classmethod
    def _normalize_allowed_extensions(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def _validate_server_profiles(self) -> "MCPConfig":
        missing = sorted(
            {
                server.credential_profile
                for server in self.servers.values()
                if server.credential_profile
                and server.credential_profile not in self.credential_profiles
            }
        )
        if missing:
            raise ValueError("Unknown MCP credential profiles: " + ", ".join(missing))
        missing_extensions = sorted(
            {
                extension_id
                for server in self.servers.values()
                for extension_id in server.extensions
                if extension_id not in self.extensions
            }
        )
        if missing_extensions:
            raise ValueError(
                "Unknown MCP extension configurations: " + ", ".join(missing_extensions)
            )
        return self


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
    message_path: str = ".loro/session-messages"
    max_message_bytes: int = Field(default=100_000, ge=1, le=10_000_000)
    max_record_bytes: int = Field(default=10_000_000, ge=1024, le=100_000_000)


class CredentialsConfig(BaseModel):
    index_path: str = "~/.config/loro/credentials.json"


class GatewayIdentityConfig(BaseModel):
    subject: str
    display_name: str | None = None
    organization: str | None = None
    tenant: str
    groups: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class GatewayEndpointConfig(BaseModel):
    platform: Literal["slack", "discord", "telegram", "teams", "signal", "generic"]
    enabled: bool = True
    route: str
    credentials: dict[str, str] = Field(default_factory=dict)
    identities: dict[str, GatewayIdentityConfig] = Field(default_factory=dict)
    allowed_workspaces: list[str] = Field(default_factory=list)
    allowed_channels: list[str] = Field(default_factory=list)
    ephemeral_responses: bool = True

    @field_validator("route")
    @classmethod
    def _validate_route(cls, value: str) -> str:
        if re.fullmatch(r"/[A-Za-z0-9/_-]{1,128}", value) is None or ".." in value:
            raise ValueError("gateway route must be an absolute safe URL path")
        return value

    @field_validator("credentials")
    @classmethod
    def _validate_credential_references(cls, values: dict[str, str]) -> dict[str, str]:
        pattern = re.compile(
            r"vault://[a-z0-9][a-z0-9._-]{0,63}/"
            r"[a-z0-9][a-z0-9._-]{0,63}/[a-z0-9][a-z0-9._-]{0,63}"
        )
        for name, value in values.items():
            if not name.strip() or pattern.fullmatch(value) is None:
                raise ValueError(
                    "gateway credentials must map names to vault://namespace/profile/key references"
                )
        return values


class GatewayConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    max_body_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    max_pending_tasks: int = Field(default=32, ge=1, le=10_000)
    max_workers: int = Field(default=4, ge=1, le=128)
    state_path: str = ".loro/gateway-state.json"
    max_seen_messages: int = Field(default=10_000, ge=100, le=1_000_000)
    request_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    request_timeout_seconds: int = Field(default=15, ge=1, le=120)
    endpoints: dict[str, GatewayEndpointConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_routes(self) -> "GatewayConfig":
        invalid_ids = [
            endpoint_id
            for endpoint_id in self.endpoints
            if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", endpoint_id) is None
        ]
        if invalid_ids:
            raise ValueError("gateway endpoint ids must use lowercase safe names")
        routes = [endpoint.route for endpoint in self.endpoints.values()]
        if len(routes) != len(set(routes)):
            raise ValueError("gateway endpoint routes must be unique")
        return self


class AGraphConfig(BaseModel):
    enabled: bool = True
    conformance_level: int = Field(default=3, ge=0, le=3)
    state_path: str = ".loro/graph-runs"
    max_document_bytes: int = Field(default=5_000_000, ge=1024, le=100_000_000)
    max_record_bytes: int = Field(default=20_000_000, ge=1024, le=100_000_000)
    max_nodes: int = Field(default=100, ge=1, le=10_000)
    max_node_executions: int = Field(default=1000, ge=1, le=1_000_000)
    max_cost_usd: float | None = Field(default=None, gt=0)
    max_tier: Literal["minimal", "standard", "advanced", "frontier"] = "frontier"
    allow_command_criteria: bool = False
    allow_external_criteria: bool = False
    external_criteria: list[str] = Field(default_factory=list)
    allow_external_subgraph_refs: bool = False
    require_integrity_for_refs: bool = True
    require_gate_before: list[str] = Field(default_factory=list)
    forbidden_permissions: list[str] = Field(default_factory=list)
    required_criteria_kinds: list[str] = Field(default_factory=list)
    max_parallel_nodes: int = Field(default=8, ge=1, le=128)
    allow_generation: bool = True


class SkillsConfig(BaseModel):
    enabled: bool = True
    managed_paths: list[str] = Field(default_factory=lambda: ["/etc/loro/skills"])
    user_paths: list[str] = Field(default_factory=lambda: ["~/.config/loro/skills"])
    project_paths: list[str] = Field(default_factory=lambda: [".loro/skills"])
    allow_user: bool = True
    allow_project: bool = True
    allow_scripts: bool = False
    state_path: str = ".loro/skills-state.json"
    proposal_path: str = ".loro/skill-proposals"
    max_files: int = Field(default=100, ge=1, le=10_000)
    max_bytes: int = Field(default=1_000_000, ge=1024, le=100_000_000)
    max_instruction_bytes: int = Field(default=100_000, ge=256, le=10_000_000)
    max_reference_depth: int = Field(default=4, ge=0, le=20)
    max_active: int = Field(default=3, ge=1, le=20)


class SafetyConfig(BaseModel):
    enabled: bool = True
    block_on_findings: bool = True
    default_classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    redaction_text: str = "[redacted]"
    allow_sensitive_override: bool = True
    surfaces: dict[str, "DataProtectionSurfaceConfig"] = Field(
        default_factory=lambda: _default_data_protection_surfaces()
    )
    custom_patterns: list["DataProtectionPatternConfig"] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _supply_default_surfaces(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "surfaces" not in value:
            return value
        data = dict(value)
        configured = dict(data.get("surfaces") or {})
        surfaces = {
            name: policy.model_dump()
            for name, policy in _default_data_protection_surfaces().items()
        }
        surfaces.update(configured)
        data["surfaces"] = surfaces
        return data

    @model_validator(mode="after")
    def _validate_surface_names(self) -> "SafetyConfig":
        expected = set(_default_data_protection_surfaces())
        unknown = sorted(set(self.surfaces) - expected)
        if unknown:
            raise ValueError("Unknown data-protection surfaces: " + ", ".join(unknown))
        return self


class DataProtectionSurfaceConfig(BaseModel):
    action: Literal["allow", "redact", "block"] = "block"
    maximum_classification: Literal["public", "internal", "confidential", "restricted"] = (
        "confidential"
    )
    allowed_finding_kinds: list[str] = Field(default_factory=list)

    @field_validator("allowed_finding_kinds")
    @classmethod
    def _normalize_allowed_findings(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class DataProtectionPatternConfig(BaseModel):
    kind: str
    pattern: str
    classification: Literal["public", "internal", "confidential", "restricted"] = "restricted"

    @field_validator("kind", "pattern")
    @classmethod
    def _validate_nonempty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Data-protection pattern fields cannot be empty.")
        return normalized

    @field_validator("pattern")
    @classmethod
    def _validate_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"Invalid data-protection regular expression: {error}") from error
        return value


def _default_data_protection_surfaces() -> dict[str, DataProtectionSurfaceConfig]:
    persistence = DataProtectionSurfaceConfig(action="block")
    return {
        "model_input": DataProtectionSurfaceConfig(action="block"),
        "model_output": DataProtectionSurfaceConfig(action="redact"),
        "memory_local": persistence.model_copy(),
        "memory_shared": persistence.model_copy(),
        "artifact": persistence.model_copy(),
        "session": persistence.model_copy(),
        "session_message": persistence.model_copy(),
        "tool_output": DataProtectionSurfaceConfig(action="redact"),
        "audit": DataProtectionSurfaceConfig(action="redact", maximum_classification="internal"),
    }


class LoroConfig(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    approvals: ApprovalsConfig = Field(default_factory=ApprovalsConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    polaris: PolarisConfig = Field(default_factory=PolarisConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    sessions: SessionConfig = Field(default_factory=SessionConfig)
    credentials: CredentialsConfig = Field(default_factory=CredentialsConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    agraph: AGraphConfig = Field(default_factory=AGraphConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
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
            "max_retries": config.model.max_retries,
            "backoff_seconds": config.model.backoff_seconds,
            "verify_tls": config.model.verify_tls,
            "temperature": config.model.temperature,
        }
        if config.model.api_key_env:
            data["api_key_env"] = config.model.api_key_env
        if config.model.credential_ref:
            data["credential_ref"] = config.model.credential_ref
        if config.model.base_url:
            data["base_url"] = config.model.base_url
        if config.model.ca_bundle_env:
            data["ca_bundle_env"] = config.model.ca_bundle_env
        if config.model.proxy_env:
            data["proxy_env"] = config.model.proxy_env
        if config.model.max_tokens:
            data["max_tokens"] = config.model.max_tokens
        if config.model.input_cost_per_million:
            data["input_cost_per_million"] = config.model.input_cost_per_million
        if config.model.output_cost_per_million:
            data["output_cost_per_million"] = config.model.output_cost_per_million
        return {"model": data}
    if section == "identity":
        return {"identity": config.identity.model_dump(exclude_none=True)}
    if section == "approvals":
        return {"approvals": config.approvals.model_dump(exclude_none=True)}
    if section == "sandbox":
        return {"sandbox": config.sandbox.model_dump(exclude_none=True)}
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
    if section == "gateway":
        return {"gateway": config.gateway.model_dump(exclude_none=True)}
    if section == "skills":
        return {"skills": config.skills.model_dump(exclude_none=True)}
    if section == "safety":
        return {"safety": config.safety.model_dump(exclude_none=True)}
    raise ValueError(f"Unsupported config section: {section}")


def write_config_sections(path: Path, config: LoroConfig, sections: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_toml(path)
    for section in sections:
        data = _merge(data, _config_section_data(config, section))
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    return path


def replace_config_section(path: Path, config: LoroConfig, section: str) -> Path:
    """Replace a top-level section when removed nested keys must not survive a merge."""
    section_data = _config_section_data(config, section)
    if section not in section_data:
        raise ValueError(f"Section is not top-level and cannot be replaced: {section}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_toml(path)
    data[section] = section_data[section]
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
    managed_sources: list[tuple[str, bytes]] = []
    for path in managed_config_paths():
        if path.exists():
            managed_sources.append((str(path), path.read_bytes()))
    managed_env_config = os.environ.get("LORO_MANAGED_CONFIG")
    if managed_env_config:
        path = Path(managed_env_config).expanduser()
        if not path.exists():
            raise ManagedConfigIntegrityError(f"Managed config does not exist: {path}")
        managed_sources.append((str(path), path.read_bytes()))
    managed_env_content = os.environ.get("LORO_MANAGED_CONFIG_CONTENT")
    if managed_env_content:
        managed_sources.append(
            ("environment:LORO_MANAGED_CONFIG_CONTENT", managed_env_content.encode())
        )
    _verify_managed_sources(managed_sources)
    managed_data: dict[str, Any] = {}
    for label, content in managed_sources:
        try:
            parsed = tomllib.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ManagedConfigIntegrityError(f"Invalid managed config: {label}") from error
        managed_data = _merge(managed_data, parsed)
    data = _merge(data, managed_data)
    return LoroConfig.model_validate(data)


def managed_config_digest(sources: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for label, content in sources:
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _verify_managed_sources(sources: list[tuple[str, bytes]]) -> None:
    required = os.environ.get("LORO_MANAGED_CONFIG_REQUIRED", "").casefold() in {
        "1",
        "true",
        "yes",
    }
    if required and not sources:
        raise ManagedConfigIntegrityError("Managed policy is required but no source was found.")
    expected = os.environ.get("LORO_MANAGED_CONFIG_SHA256")
    if expected is None:
        return
    normalized = expected if expected.startswith("sha256:") else f"sha256:{expected}"
    actual = managed_config_digest(sources)
    if not sources or not secrets.compare_digest(actual, normalized):
        raise ManagedConfigIntegrityError(
            f"Managed policy digest mismatch (expected {normalized}, got {actual})."
        )
