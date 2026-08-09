from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from loro.config import ApprovalsConfig
from loro.identity import IdentityContext

ApprovalScope = Literal["once", "session"]
ApprovalMethod = Literal["interactive", "non_interactive"]
ApprovalStatus = Literal["active", "used", "denied", "expired"]
ApprovalEventHandler = Callable[[str, Mapping[str, Any]], None]


class ApprovalError(PermissionError):
    """Base error for approval validation failures."""


class ApprovalExpiredError(ApprovalError):
    """Raised when an approval is used after its expiry."""


class ApprovalReplayError(ApprovalError):
    """Raised when a consumed or mismatched approval is reused."""


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    target: str
    arguments: dict[str, Any]
    identity_subject: str
    identity_tenant: str
    identity_session_id: str
    policy_decision: str
    policy_version: str
    policy_source: str
    policy_reason: str
    risk_reason: str
    request_id: str = field(default_factory=lambda: str(uuid4()))
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def arguments_digest(self) -> str:
        return hashlib.sha256(_canonical_json(self.arguments).encode("utf-8")).hexdigest()

    @property
    def fingerprint(self) -> str:
        bound = {
            "action": self.action,
            "target": self.target,
            "arguments_digest": self.arguments_digest,
            "identity_subject": self.identity_subject,
            "identity_tenant": self.identity_tenant,
            "identity_session_id": self.identity_session_id,
            "policy_decision": self.policy_decision,
            "policy_version": self.policy_version,
            "policy_source": self.policy_source,
        }
        return hashlib.sha256(_canonical_json(bound).encode("utf-8")).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "requested_at": self.requested_at.isoformat(),
            "action": self.action,
            "target": self.target,
            "arguments_digest": self.arguments_digest,
            "identity_subject": self.identity_subject,
            "identity_tenant": self.identity_tenant,
            "identity_session_id": self.identity_session_id,
            "policy_decision": self.policy_decision,
            "policy_version": self.policy_version,
            "policy_source": self.policy_source,
            "policy_reason": self.policy_reason,
            "risk_reason": self.risk_reason,
        }

    def display_arguments(self, limit: int = 320) -> str:
        preview = _canonical_json(_redacted_preview(self.arguments))
        return preview if len(preview) <= limit else f"{preview[:limit]}..."


@dataclass
class ApprovalRecord:
    request_id: str
    request_fingerprint: str
    arguments_digest: str
    identity_subject: str
    identity_tenant: str
    identity_session_id: str
    scope: ApprovalScope
    method: ApprovalMethod
    granted_at: datetime
    expires_at: datetime
    approval_id: str = field(default_factory=lambda: str(uuid4()))
    status: ApprovalStatus = "active"
    use_count: int = 0
    last_used_at: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "request_id": self.request_id,
            "request_fingerprint": self.request_fingerprint,
            "arguments_digest": self.arguments_digest,
            "identity_subject": self.identity_subject,
            "identity_tenant": self.identity_tenant,
            "identity_session_id": self.identity_session_id,
            "scope": self.scope,
            "method": self.method,
            "granted_at": self.granted_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
            "use_count": self.use_count,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


class ApprovalManager:
    def __init__(
        self,
        config: ApprovalsConfig,
        identity: IdentityContext,
        event_handler: ApprovalEventHandler | None = None,
    ) -> None:
        self.config = config
        self.identity = identity
        self.event_handler = event_handler
        self.records: dict[str, ApprovalRecord] = {}

    def request(
        self,
        *,
        action: str,
        target: str,
        arguments: Mapping[str, Any],
        policy_decision: str,
        policy_version: str = "local-v1",
        policy_source: str = "unknown",
        policy_reason: str,
        risk_reason: str,
    ) -> ApprovalRequest:
        normalized_action = action.strip()
        normalized_target = target.strip()
        if not normalized_action or not normalized_target:
            raise ApprovalError("Approval action and target must be non-empty.")
        request = ApprovalRequest(
            action=normalized_action,
            target=normalized_target,
            arguments=_approval_arguments(arguments),
            identity_subject=self.identity.subject,
            identity_tenant=self.identity.tenant,
            identity_session_id=self.identity.session_id,
            policy_decision=policy_decision,
            policy_version=policy_version,
            policy_source=policy_source,
            policy_reason=policy_reason,
            risk_reason=risk_reason,
        )
        self._emit("approval.requested", request.to_payload())
        return request

    def grant(
        self,
        request: ApprovalRequest,
        *,
        scope: ApprovalScope = "once",
        method: ApprovalMethod = "interactive",
        now: datetime | None = None,
    ) -> ApprovalRecord:
        self._assert_request_identity(request)
        if scope == "session" and not self.config.allow_session_scope:
            raise ApprovalError("Session-scoped approvals are disabled by policy.")
        if method == "non_interactive" and not self.config.allow_non_interactive:
            raise ApprovalError("Non-interactive approvals are disabled by managed policy.")
        granted_at = now or datetime.now(UTC)
        ttl = (
            self.config.session_ttl_seconds if scope == "session" else self.config.once_ttl_seconds
        )
        record = ApprovalRecord(
            request_id=request.request_id,
            request_fingerprint=request.fingerprint,
            arguments_digest=request.arguments_digest,
            identity_subject=request.identity_subject,
            identity_tenant=request.identity_tenant,
            identity_session_id=request.identity_session_id,
            scope=scope,
            method=method,
            granted_at=granted_at,
            expires_at=granted_at + timedelta(seconds=ttl),
        )
        self.records[record.approval_id] = record
        self._emit("approval.granted", _record_event_payload(request, record))
        return record

    def deny(self, request: ApprovalRequest) -> None:
        self._emit("approval.denied", request.to_payload())

    def consume(
        self,
        request: ApprovalRequest,
        approval_id: str,
        *,
        now: datetime | None = None,
    ) -> ApprovalRecord:
        self._assert_request_identity(request)
        record = self.records.get(approval_id)
        if record is None:
            raise ApprovalReplayError("Unknown approval record.")
        used_at = now or datetime.now(UTC)
        if used_at >= record.expires_at:
            record.status = "expired"
            self._emit("approval.expired", _record_event_payload(request, record))
            raise ApprovalExpiredError("Approval has expired.")
        if record.identity_subject != request.identity_subject:
            raise ApprovalReplayError("Approval actor does not match the current request.")
        if record.identity_session_id != request.identity_session_id:
            raise ApprovalReplayError("Approval session does not match the current request.")
        if record.request_fingerprint != request.fingerprint:
            raise ApprovalReplayError("Approval arguments do not match the current request.")
        if record.scope == "once" and record.status != "active":
            raise ApprovalReplayError("One-time approval has already been used.")
        if record.status not in {"active", "used"}:
            raise ApprovalReplayError(f"Approval is not active: {record.status}.")
        record.use_count += 1
        record.last_used_at = used_at
        record.status = "used" if record.scope == "once" else "active"
        self._emit("approval.used", _record_event_payload(request, record))
        return record

    def consume_matching_session(self, request: ApprovalRequest) -> ApprovalRecord | None:
        for record in self.records.values():
            if record.scope != "session" or record.request_fingerprint != request.fingerprint:
                continue
            try:
                return self.consume(request, record.approval_id)
            except ApprovalExpiredError:
                continue
        return None

    def _emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self.event_handler is not None:
            self.event_handler(event_type, payload)

    def _assert_request_identity(self, request: ApprovalRequest) -> None:
        if request.identity_subject != self.identity.subject:
            raise ApprovalReplayError("Approval request actor does not match the manager identity.")
        if request.identity_tenant != self.identity.tenant:
            raise ApprovalReplayError(
                "Approval request tenant does not match the manager identity."
            )
        if request.identity_session_id != self.identity.session_id:
            raise ApprovalReplayError(
                "Approval request session does not match the manager identity."
            )


def _approval_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _json_value(value)
        for key, value in arguments.items()
        if key not in {"approved", "approval_id"}
    }


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _redacted_preview(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _redacted_value(str(key), value) for key, value in arguments.items()}


def _redacted_value(key: str, value: Any) -> Any:
    normalized_key = key.casefold().replace("-", "_")
    if any(
        marker in normalized_key
        for marker in ("password", "secret", "token", "api_key", "credential")
    ):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _redacted_value(str(item_key), item) for item_key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redacted_value(key, item) for item in value]
    if normalized_key in {"content", "new", "old", "prompt"} and isinstance(value, str):
        return value[:120]
    return value


def _record_event_payload(
    request: ApprovalRequest,
    record: ApprovalRecord,
) -> dict[str, Any]:
    return {**request.to_payload(), **record.to_payload()}
