from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

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

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ApprovalRecord:
        try:
            scope = str(payload["scope"])
            method = str(payload["method"])
            status = str(payload.get("status", "active"))
            if scope not in {"once", "session"}:
                raise ValueError("invalid scope")
            if method not in {"interactive", "non_interactive"}:
                raise ValueError("invalid method")
            if status not in {"active", "used", "denied", "expired"}:
                raise ValueError("invalid status")
            approval_id = str(payload["approval_id"])
            UUID(approval_id)
            fingerprint = str(payload["request_fingerprint"])
            arguments_digest = str(payload["arguments_digest"])
            if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
                raise ValueError("invalid request fingerprint")
            if re.fullmatch(r"[0-9a-f]{64}", arguments_digest) is None:
                raise ValueError("invalid arguments digest")
            use_count = int(payload.get("use_count", 0))
            if use_count < 0:
                raise ValueError("invalid use count")
            granted_at = _parse_datetime(payload["granted_at"])
            expires_at = _parse_datetime(payload["expires_at"])
            if expires_at <= granted_at:
                raise ValueError("invalid approval lifetime")
            return cls(
                approval_id=approval_id,
                request_id=str(payload["request_id"]),
                request_fingerprint=fingerprint,
                arguments_digest=arguments_digest,
                identity_subject=str(payload["identity_subject"]),
                identity_tenant=str(payload["identity_tenant"]),
                identity_session_id=str(payload["identity_session_id"]),
                scope=scope,  # type: ignore[arg-type]
                method=method,  # type: ignore[arg-type]
                granted_at=granted_at,
                expires_at=expires_at,
                status=status,  # type: ignore[arg-type]
                use_count=use_count,
                last_used_at=(
                    _parse_datetime(payload["last_used_at"])
                    if payload.get("last_used_at")
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ApprovalError("Invalid persisted approval record.") from error


class ApprovalStore(Protocol):
    """Durable-store contract with atomic optimistic updates."""

    def get(self, approval_id: str) -> ApprovalRecord | None: ...

    def list(self) -> list[ApprovalRecord]: ...

    def create(self, record: ApprovalRecord) -> None: ...

    def update(
        self,
        record: ApprovalRecord,
        *,
        expected_status: ApprovalStatus,
        expected_use_count: int,
    ) -> bool: ...


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self.records: dict[str, ApprovalRecord] = {}
        self._lock = threading.RLock()

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            return self.records.get(approval_id)

    def list(self) -> list[ApprovalRecord]:
        with self._lock:
            return list(self.records.values())

    def create(self, record: ApprovalRecord) -> None:
        with self._lock:
            if record.approval_id in self.records:
                raise ApprovalError("Approval record already exists.")
            self.records[record.approval_id] = record

    def update(
        self,
        record: ApprovalRecord,
        *,
        expected_status: ApprovalStatus,
        expected_use_count: int,
    ) -> bool:
        with self._lock:
            current = self.records.get(record.approval_id)
            if current is None:
                return False
            if current is not record and (
                current.status != expected_status or current.use_count != expected_use_count
            ):
                return False
            self.records[record.approval_id] = record
            return True


class JsonApprovalStore:
    """Atomic local approval state suitable for single-host enterprise deployments."""

    SCHEMA_VERSION = "1.0"

    def __init__(self, path: str | Path, max_bytes: int = 10_000_000) -> None:
        self.path = Path(path).expanduser()
        self.max_bytes = max_bytes
        self._thread_lock = threading.RLock()

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._locked():
            return self._load_unlocked().get(approval_id)

    def list(self) -> list[ApprovalRecord]:
        with self._locked():
            return list(self._load_unlocked().values())

    def create(self, record: ApprovalRecord) -> None:
        with self._locked():
            records = self._load_unlocked()
            if record.approval_id in records:
                raise ApprovalError("Approval record already exists.")
            records[record.approval_id] = record
            self._write_unlocked(records)

    def update(
        self,
        record: ApprovalRecord,
        *,
        expected_status: ApprovalStatus,
        expected_use_count: int,
    ) -> bool:
        with self._locked():
            records = self._load_unlocked()
            current = records.get(record.approval_id)
            if current is None:
                return False
            if current.status != expected_status or current.use_count != expected_use_count:
                return False
            records[record.approval_id] = record
            self._write_unlocked(records)
            return True

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        if self.path.is_symlink() or lock_path.is_symlink():
            raise ApprovalError("Approval store paths cannot be symlinks.")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as error:
            raise ApprovalError(f"Unable to lock approval store: {lock_path}") from error
        with self._thread_lock, os.fdopen(descriptor, "a+b") as lock:
            _restrict_permissions(lock_path)
            if self.path.exists():
                _restrict_permissions(self.path)
            if os.name == "nt":  # pragma: no cover - Windows CI exercises this when available.
                import msvcrt

                lock.seek(0)
                if lock.read(1) == b"":
                    lock.seek(0)
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _load_unlocked(self) -> dict[str, ApprovalRecord]:
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > self.max_bytes:
            raise ApprovalError(f"Approval store exceeds max_store_bytes: {self.path}")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApprovalError(f"Invalid approval store: {self.path}") from error
        if not isinstance(payload, dict) or payload.get("schema_version") != self.SCHEMA_VERSION:
            raise ApprovalError("Unsupported approval store schema.")
        items = payload.get("records")
        if not isinstance(items, list):
            raise ApprovalError("Invalid approval store records.")
        records = [ApprovalRecord.from_payload(item) for item in items if isinstance(item, dict)]
        duplicate_ids = len({item.approval_id for item in records}) != len(records)
        if len(records) != len(items) or duplicate_ids:
            raise ApprovalError("Invalid or duplicate approval store records.")
        return {item.approval_id: item for item in records}

    def _write_unlocked(self, records: Mapping[str, ApprovalRecord]) -> None:
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "records": [records[key].to_payload() for key in sorted(records)],
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        if len(content.encode("utf-8")) > self.max_bytes:
            raise ApprovalError("Approval store update exceeds max_store_bytes.")
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            _restrict_permissions(temporary)
            os.replace(temporary, self.path)
            _restrict_permissions(self.path)
        finally:
            temporary.unlink(missing_ok=True)


class ApprovalManager:
    def __init__(
        self,
        config: ApprovalsConfig,
        identity: IdentityContext,
        event_handler: ApprovalEventHandler | None = None,
        store: ApprovalStore | None = None,
    ) -> None:
        self.config = config
        self.identity = identity
        self.event_handler = event_handler
        self.store = store or _approval_store(config)
        self.records = (
            self.store.records if isinstance(self.store, InMemoryApprovalStore) else {}
        )

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
        self.store.create(record)
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
        record = self.store.get(approval_id)
        if record is None:
            raise ApprovalReplayError("Unknown approval record.")
        used_at = now or datetime.now(UTC)
        if used_at >= record.expires_at:
            previous_status = record.status
            previous_use_count = record.use_count
            record.status = "expired"
            if not self.store.update(
                record,
                expected_status=previous_status,
                expected_use_count=previous_use_count,
            ):
                raise ApprovalReplayError("Approval record changed concurrently.")
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
        previous_status = record.status
        previous_use_count = record.use_count
        record.use_count += 1
        record.last_used_at = used_at
        record.status = "used" if record.scope == "once" else "active"
        if not self.store.update(
            record,
            expected_status=previous_status,
            expected_use_count=previous_use_count,
        ):
            raise ApprovalReplayError("Approval record changed concurrently.")
        self._emit("approval.used", _record_event_payload(request, record))
        return record

    def consume_matching_session(self, request: ApprovalRequest) -> ApprovalRecord | None:
        for record in self.store.list():
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


def _approval_store(config: ApprovalsConfig) -> ApprovalStore:
    if config.store == "json":
        return JsonApprovalStore(config.store_path, config.max_store_bytes)
    return InMemoryApprovalStore()


def _parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("Persisted approval timestamps must include a timezone.")
    return parsed.astimezone(UTC)


def _restrict_permissions(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)
