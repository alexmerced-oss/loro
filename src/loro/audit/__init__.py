from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loro.audit.metrics import OperationalMetrics
from loro.audit.schema import AUDIT_SCHEMA_VERSION, AuditEvent
from loro.audit.sinks import (
    AuditBuffer,
    AuditBufferFullError,
    AuditSink,
    AuditSinkError,
    HttpAuditSink,
    JsonlAuditSink,
    verify_jsonl_audit,
)
from loro.config import AuditConfig, SafetyConfig
from loro.data_protection import DataProtectionEngine
from loro.identity import IdentityContext


class AuditDeliveryError(RuntimeError):
    """Raised when required audit delivery cannot be completed or buffered."""


@dataclass(frozen=True)
class AuditFlushResult:
    attempted: int
    delivered: int
    remaining: int


class AuditLogger:
    def __init__(
        self,
        config: AuditConfig,
        identity: IdentityContext | None = None,
        sink: AuditSink | None = None,
        safety_config: SafetyConfig | None = None,
    ) -> None:
        self.config = config
        self.identity = identity
        self.path = Path(config.path).expanduser()
        self.buffer = AuditBuffer(config.buffer_path, config.max_buffer_events)
        self.sink = sink or self._configured_sink()
        self.protection = DataProtectionEngine(safety_config) if safety_config else None
        self.metrics = (
            OperationalMetrics(config.metrics_path) if config.metrics_enabled else None
        )
        self.context: dict[str, Any] = {}

    def bind_context(self, **context: Any) -> None:
        self.context = {key: value for key, value in context.items() if value is not None}

    def write(self, event_type: str, **details: Any) -> AuditEvent:
        for key, value in self.context.items():
            details.setdefault(key, value)
        if self.identity is not None:
            details.setdefault("actor", self.identity.subject)
            details.setdefault("tenant_id", self.identity.tenant)
            details.setdefault("session_id", self.identity.session_id)
            details.setdefault("identity", self.identity.to_payload())
        if self.protection is not None:
            details, redacted_fields, classifications = _protect_details(details, self.protection)
            if redacted_fields:
                details["redaction_metadata"] = {
                    "applied": True,
                    "fields": redacted_fields,
                    "method": "managed-data-protection",
                    "classifications": sorted(classifications),
                }
        event = AuditEvent(
            event_type=event_type,
            details=details,
            schema_version=self.config.schema_version,
        )
        if not self.config.enabled:
            event.delivery_status = "disabled"
            self._observe_metrics(event_type, details, event.delivery_status)
            return event
        payload = event.to_payload()
        try:
            self.sink.deliver(payload)
            event.delivery_status = "delivered"
            self._observe_metrics(event_type, details, event.delivery_status)
            return event
        except AuditSinkError as error:
            if self.config.sink != "http":
                raise AuditDeliveryError(str(error)) from error
            try:
                evicted = self.buffer.append(payload)
                event.delivery_status = "buffered"
            except AuditBufferFullError as buffer_error:
                event.delivery_status = "failed"
                raise AuditDeliveryError(str(buffer_error)) from buffer_error
            if evicted:
                warnings.warn(
                    f"Audit buffer at {self.buffer.path} evicted {evicted} oldest event(s) "
                    f"to accept a new one (max_buffer_events={self.config.max_buffer_events}).",
                    RuntimeWarning,
                    stacklevel=2,
                )
            message = f"Audit delivery failed; event buffered at {self.buffer.path}: {error}"
            if self.config.failure_mode == "fail":
                self._observe_metrics(event_type, details, event.delivery_status)
                raise AuditDeliveryError(message) from error
            warnings.warn(message, RuntimeWarning, stacklevel=2)
            self._observe_metrics(event_type, details, event.delivery_status)
            return event

    def flush(self) -> AuditFlushResult:
        if self.config.sink != "http":
            return AuditFlushResult(attempted=0, delivered=0, remaining=0)
        # drain() holds the buffer lock across load-deliver-rewrite so a concurrent
        # append cannot land in the gap and be discarded by the rewrite.
        batch_deliver = getattr(self.sink, "deliver_batch", None)
        if batch_deliver is not None:
            result = self.buffer.drain(batch_deliver, batch_size=self.config.http_batch_size)
        else:
            # A sink without batch support keeps exact per-event delivery accounting.
            result = self.buffer.drain(lambda batch: self.sink.deliver(batch[0]), batch_size=1)
        return AuditFlushResult(
            attempted=result.attempted,
            delivered=result.delivered,
            remaining=result.remaining,
        )

    def doctor(self) -> dict[str, Any]:
        issues: list[str] = []
        if self.config.schema_version != AUDIT_SCHEMA_VERSION:
            issues.append(
                f"Unsupported audit schema version: {self.config.schema_version} "
                f"(supported: {AUDIT_SCHEMA_VERSION})"
            )
        if self.config.sink == "http" and not self.config.http_url:
            issues.append("HTTP audit sink requires http_url.")
        if self.config.sink == "http" and self.config.http_token_env:
            if not os.environ.get(self.config.http_token_env):
                issues.append(
                    "HTTP audit token environment variable is missing: "
                    f"{self.config.http_token_env}"
                )
        try:
            # load() parses every event, so a corrupt buffer surfaces here as a
            # diagnostic; count() is the fast path used on the write hot path.
            buffered_events = len(self.buffer.load()) if self.config.sink == "http" else 0
        except AuditSinkError as error:
            issues.append(str(error))
            buffered_events = -1
        evicted_events = self.buffer.evicted_events()
        if evicted_events:
            issues.append(
                f"Audit buffer evicted {evicted_events} oldest event(s) after reaching "
                f"max_buffer_events={self.config.max_buffer_events}."
            )
        return {
            "ok": not issues,
            "enabled": self.config.enabled,
            "schema_version": self.config.schema_version,
            "sink": self.config.sink,
            "failure_mode": self.config.failure_mode,
            "path": str(self.path),
            "buffer_path": str(self.buffer.path),
            "buffered_events": buffered_events,
            "evicted_events": evicted_events,
            "max_buffer_events": self.config.max_buffer_events,
            "metrics_enabled": self.config.metrics_enabled,
            "metrics_path": self.config.metrics_path,
            "issues": issues,
        }

    def _observe_metrics(
        self,
        event_type: str,
        details: dict[str, Any],
        delivery_status: str,
    ) -> None:
        if self.metrics is None:
            return
        try:
            self.metrics.observe(event_type, details, delivery_status=delivery_status)
        except RuntimeError as error:
            warnings.warn(
                f"Operational metrics update failed: {error}",
                RuntimeWarning,
                stacklevel=2,
            )

    def _configured_sink(self) -> AuditSink:
        if self.config.sink == "jsonl":
            return JsonlAuditSink(self.path)
        return HttpAuditSink(
            url=self.config.http_url or "",
            token_env=self.config.http_token_env,
            timeout_seconds=self.config.timeout_seconds,
            max_retries=self.config.max_retries,
            backoff_seconds=self.config.backoff_seconds,
        )


def prompt_preview(prompt: str, limit: int = 160) -> str:
    normalized = " ".join(prompt.split())
    return normalized[:limit]


def _protect_details(
    details: dict[str, Any],
    protection: DataProtectionEngine,
) -> tuple[dict[str, Any], list[str], set[str]]:
    redacted_fields: list[str] = []
    classifications: set[str] = set()

    def visit(value: Any, path: str) -> Any:
        if isinstance(value, str):
            decision = protection.evaluate(value, "audit")
            classifications.add(decision.classification)
            if decision.blocked:
                raise ValueError(f"Data protection blocked audit field: {path}")
            if decision.redacted:
                redacted_fields.append(path)
            return decision.content
        if isinstance(value, dict):
            return {key: visit(item, f"{path}.{key}") for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [visit(item, f"{path}[{index}]") for index, item in enumerate(value)]
        return value

    protected = {key: visit(value, f"details.{key}") for key, value in details.items()}
    return protected, redacted_fields, classifications


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "AuditDeliveryError",
    "AuditEvent",
    "AuditFlushResult",
    "AuditLogger",
    "prompt_preview",
    "verify_jsonl_audit",
]
