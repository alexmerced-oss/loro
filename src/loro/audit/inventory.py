from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuditEventFamily:
    prefix: str
    purpose: str
    consequential: bool


AUDIT_EVENT_FAMILIES = (
    AuditEventFamily("agraph.", "Agentic Graph validation and execution", True),
    AuditEventFamily("approval.", "Approval request lifecycle", True),
    AuditEventFamily("artifact.", "Productivity artifact creation", True),
    AuditEventFamily("config.", "Configuration changes", True),
    AuditEventFamily("data.", "Governed-data inspection", True),
    AuditEventFamily("file.", "Workspace file access and mutation", True),
    AuditEventFamily("gateway.", "Remote gateway ingress and delivery", True),
    AuditEventFamily("git.", "Repository inspection and mutation", True),
    AuditEventFamily("mcp.", "MCP client and server operations", True),
    AuditEventFamily("memory.", "Local and shared-memory operations", True),
    AuditEventFamily("polaris.", "Polaris governed-data operations", True),
    AuditEventFamily("policy.", "Permission decisions", True),
    AuditEventFamily("provider.", "Direct provider diagnostics", False),
    AuditEventFamily("runtime.", "Agent task and model/tool lifecycle", True),
    AuditEventFamily("safety.", "Data-protection decisions", True),
    AuditEventFamily("session.", "Session and cross-session operations", True),
    AuditEventFamily("shell.", "Subprocess execution", True),
    AuditEventFamily("skill.", "Agent Skill lifecycle and execution", True),
)


def audit_event_family(event_type: str) -> AuditEventFamily | None:
    return next(
        (family for family in AUDIT_EVENT_FAMILIES if event_type.startswith(family.prefix)),
        None,
    )


__all__ = ["AUDIT_EVENT_FAMILIES", "AuditEventFamily", "audit_event_family"]
