"""Forward audit events to a SIEM as OCSF JSON or ArcSight CEF, over syslog or to a file.

The mapping is deliberately small and documented in ``docs/audit.md``: every Loro audit event
becomes one OCSF "API Activity" event (class 6003) or one CEF record, with the full Loro
payload kept under ``unmapped`` (OCSF) so nothing is lost. Forwarding is best effort: a SIEM
outage never blocks an agent run; the local hash-chained log stays authoritative.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from loro import __version__
from loro.config import AuditForwardConfig

OCSF_VERSION = "1.3.0"
OCSF_CLASS_UID = 6003  # API Activity
OCSF_CATEGORY_UID = 6  # Application Activity
OCSF_ACTIVITY_OTHER = 99
VENDOR = "Alex Merced"
PRODUCT = "Loro"
_FAILURE_WORDS = ("denied", "failed", "blocked", "rejected", "error", "expired", "exceeded")


def _timestamp_ms(event: Mapping[str, Any]) -> int:
    raw = str(event.get("timestamp") or event.get("created_at") or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC)
    return int(parsed.timestamp() * 1000)


def _failed(event: Mapping[str, Any]) -> bool | None:
    result = event.get("result")
    if isinstance(result, Mapping) and isinstance(result.get("ok"), bool):
        return not result["ok"]
    event_type = str(event.get("event_type", ""))
    if any(word in event_type for word in _FAILURE_WORDS):
        return True
    return None


def to_ocsf(event: Mapping[str, Any]) -> dict[str, Any]:
    """Map one Loro audit payload onto an OCSF 1.3 API Activity event."""

    failed = _failed(event)
    status_id = 0 if failed is None else (2 if failed else 1)
    raw_details = event.get("details")
    details: Mapping[str, Any] = raw_details if isinstance(raw_details, Mapping) else {}
    raw_integrity = event.get("integrity")
    integrity: Mapping[str, Any] = raw_integrity if isinstance(raw_integrity, Mapping) else {}
    ocsf: dict[str, Any] = {
        "class_uid": OCSF_CLASS_UID,
        "class_name": "API Activity",
        "category_uid": OCSF_CATEGORY_UID,
        "category_name": "Application Activity",
        "activity_id": OCSF_ACTIVITY_OTHER,
        "activity_name": str(event.get("event_type", "unknown")),
        "type_uid": OCSF_CLASS_UID * 100 + OCSF_ACTIVITY_OTHER,
        "severity_id": 3 if failed else 1,
        "severity": "Medium" if failed else "Informational",
        "status_id": status_id,
        "status": {0: "Unknown", 1: "Success", 2: "Failure"}[status_id],
        "time": _timestamp_ms(event),
        "metadata": {
            "version": OCSF_VERSION,
            "uid": event.get("event_id"),
            "correlation_uid": event.get("trace_id"),
            "log_name": "loro.audit",
            "product": {"name": PRODUCT, "vendor_name": VENDOR, "version": __version__},
        },
        "actor": {
            "user": {"uid": event.get("actor"), "name": event.get("actor")},
            "session": {"uid": event.get("session_id")},
        },
        "api": {"operation": event.get("action") or event.get("event_type")},
        "unmapped": {
            "tenant_id": event.get("tenant_id"),
            "loro_schema_version": event.get("schema_version"),
            "policy": event.get("policy"),
            "approval": event.get("approval"),
            "result": event.get("result"),
            "details": details,
            "event_hash": integrity.get("event_hash"),
        },
    }
    identity = details.get("identity") if isinstance(details, Mapping) else None
    if isinstance(identity, Mapping):
        ocsf["actor"]["user"]["name"] = identity.get("display_name") or event.get("actor")
        ocsf["actor"]["user"]["groups"] = [{"name": g} for g in identity.get("groups") or []]
        ocsf["unmapped"]["identity_verified"] = bool(identity.get("verified", False))
    if event.get("target"):
        ocsf["resources"] = [{"name": str(event["target"])}]
    return ocsf


def _cef_header(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _cef_extension(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def to_cef(event: Mapping[str, Any]) -> str:
    """Render one Loro audit payload as an ArcSight CEF:0 record."""

    failed = _failed(event)
    event_type = str(event.get("event_type", "unknown"))
    extensions: list[tuple[str, object]] = [
        ("rt", _timestamp_ms(event)),
        ("suser", event.get("actor")),
        ("act", event.get("action") or event_type),
        ("outcome", None if failed is None else ("failure" if failed else "success")),
        ("request", event.get("target")),
        ("externalId", event.get("event_id")),
        ("cs1Label", "tenant"),
        ("cs1", event.get("tenant_id")),
        ("cs2Label", "traceId"),
        ("cs2", event.get("trace_id")),
        ("cs3Label", "sessionId"),
        ("cs3", event.get("session_id")),
    ]
    integrity = event.get("integrity")
    if isinstance(integrity, Mapping) and integrity.get("event_hash"):
        extensions += [("cs4Label", "eventHash"), ("cs4", integrity["event_hash"])]
    body = " ".join(
        f"{key}={_cef_extension(value)}" for key, value in extensions if value not in (None, "")
    )
    header = "|".join(
        _cef_header(part)
        for part in (
            "CEF:0",
            VENDOR,
            PRODUCT,
            __version__,
            event_type,
            event_type.replace(".", " "),
            7 if failed else 3,
        )
    )
    return f"{header}|{body}"


def render(event: Mapping[str, Any], fmt: str) -> str:
    if fmt == "ocsf":
        return json.dumps(to_ocsf(event), sort_keys=True, default=str, separators=(",", ":"))
    if fmt == "cef":
        return to_cef(event)
    raise ValueError(f"Unknown audit export format: {fmt}")


def export_lines(events: Iterable[Mapping[str, Any]], fmt: str) -> Iterable[str]:
    for event in events:
        yield render(event, fmt)


class SyslogForwarder:
    """RFC 5424 syslog over UDP, or TCP with RFC 6587 octet-counting framing."""

    def __init__(self, config: AuditForwardConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self.hostname = socket.gethostname()[:255] or "-"

    def _frame(self, event: Mapping[str, Any]) -> bytes:
        failed = _failed(event)
        severity = 4 if failed else 6  # warning / informational
        priority = self.config.facility * 8 + severity
        stamp = datetime.fromtimestamp(_timestamp_ms(event) / 1000, UTC).isoformat()
        message = render(event, self.config.format)
        msgid = str(event.get("event_type", "-"))[:32].replace(" ", "_") or "-"
        line = f"<{priority}>1 {stamp} {self.hostname} {self.config.app_name} - {msgid} - {message}"
        return line.encode("utf-8")

    def send(self, event: Mapping[str, Any]) -> None:
        frame = self._frame(event)
        address = (self.config.host, self.config.port)
        with self._lock:
            if self.config.protocol == "udp":
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                    udp.sendto(frame[:65000], address)
                return
            try:
                if self._socket is None:
                    self._socket = socket.create_connection(
                        address, timeout=self.config.timeout_seconds
                    )
                self._socket.sendall(f"{len(frame)} ".encode("ascii") + frame)
            except OSError:
                self.close()
                raise

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None
