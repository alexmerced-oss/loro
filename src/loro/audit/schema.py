from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

AUDIT_SCHEMA_VERSION = "1.0"


@dataclass
class AuditEvent:
    event_type: str
    details: dict[str, Any] = field(default_factory=dict)
    schema_version: str = AUDIT_SCHEMA_VERSION
    event_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    delivery_status: str = "pending"

    def to_payload(self) -> dict[str, Any]:
        identity = self.details.get("identity")
        identity_payload = identity if isinstance(identity, dict) else {}
        actor = self.details.get("actor") or identity_payload.get("subject")
        tenant = self.details.get("tenant_id") or identity_payload.get("tenant")
        session_id = self.details.get("session_id") or identity_payload.get("session_id")
        timestamp = self.created_at.isoformat()
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": timestamp,
            "created_at": timestamp,
            "actor": actor,
            "tenant_id": tenant,
            "session_id": session_id,
            "trace_id": self.details.get("trace_id") or self.event_id,
            "action": self.details.get("action") or self.event_type,
            "target": self.details.get("target"),
            "policy": _policy_payload(self.details),
            "approval": _approval_payload(self.details),
            "result": _result_payload(self.details),
            "redaction": _redaction_payload(self.details),
            "details": self.details,
        }


def _policy_payload(details: dict[str, Any]) -> dict[str, Any] | None:
    payload = _present(
        details,
        {
            "decision": ("policy_decision", "decision"),
            "version": ("policy_version",),
            "source": ("policy_source",),
            "reason": ("policy_reason",),
        },
    )
    return payload or None


def _approval_payload(details: dict[str, Any]) -> dict[str, Any] | None:
    payload = _present(
        details,
        {
            "approval_id": ("approval_id",),
            "request_id": ("request_id",),
            "scope": ("scope",),
            "method": ("method",),
            "status": ("status",),
        },
    )
    return payload or None


def _result_payload(details: dict[str, Any]) -> dict[str, Any] | None:
    payload = _present(
        details,
        {
            "ok": ("ok", "success"),
            "returncode": ("returncode",),
            "stop_reason": ("stop_reason",),
            "error": ("error",),
        },
    )
    return payload or None


def _redaction_payload(details: dict[str, Any]) -> dict[str, Any]:
    explicit = details.get("redaction") or details.get("redaction_metadata")
    if isinstance(explicit, dict):
        return explicit
    if details.get("prompt_preview") is not None:
        return {
            "applied": True,
            "fields": ["details.prompt_preview"],
            "method": "truncated-preview",
        }
    return {"applied": False, "fields": []}


def _present(
    details: dict[str, Any],
    mapping: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for output_key, candidates in mapping.items():
        for candidate in candidates:
            if candidate in details and details[candidate] is not None:
                payload[output_key] = details[candidate]
                break
    return payload
