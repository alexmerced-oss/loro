from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loro.config import MCPConfig, MCPExtensionConfig, MCPServerConfig

TASKS_EXTENSION_ID = "io.modelcontextprotocol/tasks"


class MCPExtensionError(ValueError):
    """Raised when an MCP extension is unavailable or violates managed policy."""


@dataclass(frozen=True)
class ExtensionStatus:
    identifier: str
    version: str
    adapter: str | None
    enabled: bool
    allowed: bool
    implemented: bool
    version_supported: bool
    active: bool
    reason: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "version": self.version,
            "adapter": self.adapter,
            "enabled": self.enabled,
            "allowed": self.allowed,
            "implemented": self.implemented,
            "version_supported": self.version_supported,
            "active": self.active,
            "reason": self.reason,
        }


class MCPExtensionRegistry:
    _IMPLEMENTED_ADAPTERS = {"tasks"}
    _IMPLEMENTED_VERSIONS = {TASKS_EXTENSION_ID: {"draft"}}

    def __init__(self, config: MCPConfig) -> None:
        self.config = config

    def statuses(self, server: MCPServerConfig | None = None) -> list[ExtensionStatus]:
        requested = set(server.extensions) if server is not None else set(self.config.extensions)
        return [
            self._status(extension_id, extension)
            for extension_id, extension in sorted(self.config.extensions.items())
            if extension_id in requested
        ]

    def active(self, server: MCPServerConfig) -> list[tuple[str, MCPExtensionConfig]]:
        active: list[tuple[str, MCPExtensionConfig]] = []
        for extension_id in server.extensions:
            extension = self.config.extensions[extension_id]
            if self._status(extension_id, extension).active:
                active.append((extension_id, extension))
        return active

    def require_active(self, server: MCPServerConfig, extension_id: str) -> None:
        if extension_id not in server.extensions:
            raise MCPExtensionError(f"MCP extension is not enabled for this server: {extension_id}")
        extension = self.config.extensions[extension_id]
        status = self._status(extension_id, extension)
        if not status.active:
            raise MCPExtensionError(status.reason or f"MCP extension is inactive: {extension_id}")

    def sdk_extensions(self, server: MCPServerConfig, on_task: Any) -> list[Any]:
        result: list[Any] = []
        for extension_id, extension in self.active(server):
            if extension.adapter == "tasks" and extension_id == TASKS_EXTENSION_ID:
                from loro.mcp.tasks import create_sdk_tasks_extension

                result.append(create_sdk_tasks_extension(on_task, extension.settings))
        return result

    def _status(self, extension_id: str, extension: MCPExtensionConfig) -> ExtensionStatus:
        allowed = (
            not self.config.allowed_extensions or extension_id in self.config.allowed_extensions
        )
        implemented = (
            extension.adapter in self._IMPLEMENTED_ADAPTERS and extension_id == TASKS_EXTENSION_ID
        )
        version_supported = implemented and extension.version in self._IMPLEMENTED_VERSIONS.get(
            extension_id, set()
        )
        schema_issue = _settings_schema_issue(extension)
        active = (
            extension.enabled
            and allowed
            and implemented
            and version_supported
            and schema_issue is None
        )
        reason = None
        if not extension.enabled:
            reason = "disabled by configuration"
        elif not allowed:
            reason = "blocked by managed extension allowlist"
        elif not implemented:
            reason = "no trusted Loro adapter is installed; extension remains inert"
        elif not version_supported:
            reason = f"unsupported extension version for trusted adapter: {extension.version}"
        elif schema_issue is not None:
            reason = schema_issue
        return ExtensionStatus(
            identifier=extension_id,
            version=extension.version,
            adapter=extension.adapter,
            enabled=extension.enabled,
            allowed=allowed,
            implemented=implemented,
            version_supported=version_supported,
            active=active,
            reason=reason,
        )


def _settings_schema_issue(extension: MCPExtensionConfig) -> str | None:
    if extension.settings_schema is None:
        return None
    try:
        from jsonschema import SchemaError, ValidationError, validate
    except ImportError:
        return "jsonschema is required to validate extension settings"
    try:
        validate(extension.settings, extension.settings_schema)
    except SchemaError as error:
        return f"invalid extension settings schema: {error.message}"
    except ValidationError as error:
        return f"extension settings failed schema validation: {error.message}"
    return None
