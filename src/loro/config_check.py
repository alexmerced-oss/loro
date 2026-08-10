"""Lint a resolved Loro configuration for risky-but-valid settings.

Schema validation only proves a config is well-formed. These checks surface combinations
that load cleanly and then weaken a boundary at run time — an unconfined workspace, a
redacting policy on a surface that persists, a sandbox profile that trusts a bare
executable name, a gateway endpoint with no replay defence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from loro.config import LoroConfig

Severity = Literal["error", "warning", "info"]

__all__ = ["ConfigFinding", "check_config"]


@dataclass(frozen=True)
class ConfigFinding:
    code: str
    severity: Severity
    message: str
    pointer: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "pointer": self.pointer,
        }


PERSISTENCE_SURFACES = (
    "memory_local",
    "memory_shared",
    "artifact",
    "session",
    "session_message",
)


def check_config(config: LoroConfig) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    findings.extend(_check_workspace(config))
    findings.extend(_check_surfaces(config))
    findings.extend(_check_sandbox(config))
    findings.extend(_check_gateway(config))
    findings.extend(_check_identity(config))
    findings.extend(_check_audit(config))
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(findings, key=lambda item: (order[item.severity], item.code))


def _check_workspace(config: LoroConfig) -> list[ConfigFinding]:
    if config.permissions.workspace_roots:
        return []
    findings = [
        ConfigFinding(
            "LC001",
            "warning",
            "permissions.workspace_roots is empty, so filesystem tools are not confined "
            "to any directory.",
            "/permissions/workspace_roots",
        )
    ]
    if config.mcp.server.enabled:
        findings.append(
            ConfigFinding(
                "LC002",
                "error",
                "MCP server mode is enabled with no workspace_roots; path-reading exports "
                "are refused until roots are configured.",
                "/mcp/server/enabled",
            )
        )
    return findings


def _check_surfaces(config: LoroConfig) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    for name in PERSISTENCE_SURFACES:
        surface = config.safety.surfaces.get(name)
        if surface is None:
            continue
        if surface.action == "redact":
            findings.append(
                ConfigFinding(
                    "LC010",
                    "warning",
                    f"Surface {name!r} persists content but is set to redact rather than "
                    "block; sensitive material is stored in redacted form instead of "
                    "being refused.",
                    f"/safety/surfaces/{name}/action",
                )
            )
        elif surface.action == "allow":
            findings.append(
                ConfigFinding(
                    "LC011",
                    "error",
                    f"Surface {name!r} persists content with no data-protection action.",
                    f"/safety/surfaces/{name}/action",
                )
            )
    if not config.safety.enabled:
        findings.append(
            ConfigFinding(
                "LC012",
                "error",
                "Data protection is disabled; no surface is scanned or redacted.",
                "/safety/enabled",
            )
        )
    if config.safety.allow_sensitive_override:
        findings.append(
            ConfigFinding(
                "LC013",
                "info",
                "allow_sensitive_override lets a caller bypass data protection with "
                "allow_sensitive=true.",
                "/safety/allow_sensitive_override",
            )
        )
    return findings


def _check_sandbox(config: LoroConfig) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    if not config.sandbox.enabled:
        return [
            ConfigFinding(
                "LC020",
                "error",
                "Sandboxing is disabled; subprocess tools run unconfined.",
                "/sandbox/enabled",
            )
        ]
    for name, profile in config.sandbox.profiles.items():
        pointer = f"/sandbox/profiles/{name}"
        if "*" in profile.allowed_executables:
            findings.append(
                ConfigFinding(
                    "LC021",
                    "warning",
                    f"Sandbox profile {name!r} allows any executable.",
                    f"{pointer}/allowed_executables",
                )
            )
        bare = [value for value in profile.allowed_executables if value != "*" and "/" not in value]
        if bare and not profile.trusted_executable_prefixes:
            findings.append(
                ConfigFinding(
                    "LC022",
                    "info",
                    f"Sandbox profile {name!r} allowlists executables by name "
                    f"({', '.join(sorted(bare))}) with no trusted_executable_prefixes; "
                    "set absolute paths or prefixes to pin them.",
                    f"{pointer}/allowed_executables",
                )
            )
        if profile.backend == "bubblewrap" and profile.filesystem == "host_readonly":
            findings.append(
                ConfigFinding(
                    "LC023",
                    "warning",
                    f"Sandbox profile {name!r} binds the whole host read-only; only "
                    "masked_paths are hidden.",
                    f"{pointer}/filesystem",
                )
            )
        if profile.backend == "process" and profile.network == "deny":
            findings.append(
                ConfigFinding(
                    "LC024",
                    "warning",
                    f"Sandbox profile {name!r} requests network denial but the process "
                    "backend cannot enforce it.",
                    f"{pointer}/network",
                )
            )
    return findings


def _check_gateway(config: LoroConfig) -> list[ConfigFinding]:
    if not config.gateway.enabled:
        return []
    findings: list[ConfigFinding] = []
    for endpoint_id, endpoint in config.gateway.endpoints.items():
        pointer = f"/gateway/endpoints/{endpoint_id}"
        if endpoint.platform in {"teams", "signal", "generic"} and (
            not endpoint.require_signed_timestamp
        ):
            findings.append(
                ConfigFinding(
                    "LC030",
                    "warning",
                    f"Gateway endpoint {endpoint_id!r} accepts bridge requests with no "
                    "signed timestamp; replay protection relies only on the bounded "
                    "seen-message ledger.",
                    f"{pointer}/require_signed_timestamp",
                )
            )
        if not endpoint.identities:
            findings.append(
                ConfigFinding(
                    "LC031",
                    "warning",
                    f"Gateway endpoint {endpoint_id!r} maps no identities, so every "
                    "request is rejected.",
                    f"{pointer}/identities",
                )
            )
        if not endpoint.allowed_channels and not endpoint.allowed_workspaces:
            findings.append(
                ConfigFinding(
                    "LC032",
                    "info",
                    f"Gateway endpoint {endpoint_id!r} restricts neither channels nor workspaces.",
                    pointer,
                )
            )
    return findings


def _check_identity(config: LoroConfig) -> list[ConfigFinding]:
    if config.identity.required_fields:
        return []
    return [
        ConfigFinding(
            "LC040",
            "info",
            "identity.required_fields is empty, so runs proceed with an unattested identity.",
            "/identity/required_fields",
        )
    ]


def _check_audit(config: LoroConfig) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    if not config.audit.enabled:
        findings.append(
            ConfigFinding(
                "LC050",
                "error",
                "Auditing is disabled; no tamper-evident record is written.",
                "/audit/enabled",
            )
        )
    if config.audit.sink == "http" and config.audit.failure_mode == "warn":
        findings.append(
            ConfigFinding(
                "LC051",
                "info",
                "HTTP audit delivery failures only warn; events are buffered and the "
                "oldest are evicted once max_buffer_events is reached.",
                "/audit/failure_mode",
            )
        )
    return findings
