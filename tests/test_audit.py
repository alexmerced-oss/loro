import json
from pathlib import Path

import httpx
import pytest

from loro.audit import (
    AUDIT_SCHEMA_VERSION,
    AuditDeliveryError,
    AuditLogger,
    verify_jsonl_audit,
)
from loro.audit.sinks import AuditBuffer, AuditBufferFullError, AuditSinkError, HttpAuditSink
from loro.config import AuditConfig
from loro.identity import IdentityContext


def test_audit_logger_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(AuditConfig(path=str(path)))
    event = logger.write("test.event", answer=42)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_id"] == event.event_id
    assert payload["event_type"] == "test.event"
    assert payload["schema_version"] == AUDIT_SCHEMA_VERSION
    assert payload["timestamp"] == payload["created_at"]
    assert payload["trace_id"] == event.event_id
    assert payload["action"] == "test.event"
    assert payload["redaction"] == {"applied": False, "fields": []}
    assert payload["details"]["answer"] == 42
    assert payload["integrity"]["algorithm"] == "sha256"
    assert payload["integrity"]["previous_hash"] is None
    assert event.delivery_status == "delivered"


def test_audit_hash_chain_detects_mutation_and_external_anchor_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(AuditConfig(path=str(path)))
    logger.write("first.event", answer=1)
    logger.write("second.event", answer=2)

    valid = verify_jsonl_audit(path)
    assert valid.ok is True
    assert valid.events == 2
    assert valid.final_hash is not None
    assert verify_jsonl_audit(path, expected_final_hash=valid.final_hash).ok is True
    anchored = verify_jsonl_audit(path, expected_final_hash="sha256:wrong")
    assert anchored.ok is False
    assert "external anchor" in str(anchored.issue)

    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["details"]["answer"] = 99
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    invalid = verify_jsonl_audit(path)
    assert invalid.ok is False
    assert invalid.line == 1
    assert "Event hash" in str(invalid.issue)


def test_audit_logger_adds_identity_context(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    identity = IdentityContext(
        subject="user-123",
        display_name="Alex",
        organization="acme",
        tenant="platform",
        groups=("engineering",),
        roles=("developer",),
        auth_method="oidc",
        session_id="session-456",
        source="environment",
    )
    AuditLogger(AuditConfig(path=str(path)), identity).write("tool.executed", ok=True)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["actor"] == "user-123"
    assert payload["tenant_id"] == "platform"
    assert payload["session_id"] == "session-456"
    assert payload["details"]["actor"] == "user-123"
    assert payload["details"]["tenant_id"] == "platform"
    assert payload["details"]["identity"]["session_id"] == "session-456"
    assert payload["details"]["identity"]["roles"] == ["developer"]


def test_audit_schema_promotes_policy_approval_result_and_redaction(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLogger(AuditConfig(path=str(path))).write(
        "approval.used",
        action="edit.write",
        target="/workspace/note.txt",
        policy_decision="ask",
        policy_version="enterprise-42",
        policy_source="permissions.rules[0]",
        approval_id="approval-1",
        request_id="request-1",
        status="used",
        ok=True,
        prompt_preview="short preview",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["policy"] == {
        "decision": "ask",
        "version": "enterprise-42",
        "source": "permissions.rules[0]",
    }
    assert payload["approval"]["approval_id"] == "approval-1"
    assert payload["result"] == {"ok": True}
    assert payload["redaction"]["method"] == "truncated-preview"


def test_failed_http_event_is_buffered_and_warned(tmp_path: Path) -> None:
    buffer_path = tmp_path / "buffer.jsonl"
    logger = AuditLogger(
        AuditConfig(
            sink="http",
            http_url="https://audit.example/events",
            buffer_path=str(buffer_path),
            failure_mode="warn",
        ),
        sink=FailingSink(),
    )

    with pytest.warns(RuntimeWarning, match="event buffered"):
        event = logger.write("tool.executed", action="shell.run", ok=False)

    buffered = [json.loads(line) for line in buffer_path.read_text().splitlines()]
    assert event.delivery_status == "buffered"
    assert len(buffered) == 1
    assert buffered[0]["event_id"] == event.event_id


def test_fail_closed_mode_buffers_then_raises(tmp_path: Path) -> None:
    buffer_path = tmp_path / "buffer.jsonl"
    logger = AuditLogger(
        AuditConfig(
            sink="http",
            http_url="https://audit.example/events",
            buffer_path=str(buffer_path),
            failure_mode="fail",
        ),
        sink=FailingSink(),
    )

    with pytest.raises(AuditDeliveryError, match="event buffered"):
        logger.write("memory.committed", action="shared_memory.commit")

    assert logger.buffer.count() == 1


def test_full_buffer_evicts_oldest_instead_of_dropping_newest(tmp_path: Path) -> None:
    logger = AuditLogger(
        AuditConfig(
            sink="http",
            http_url="https://audit.example/events",
            buffer_path=str(tmp_path / "buffer.jsonl"),
            max_buffer_events=1,
            failure_mode="warn",
        ),
        sink=FailingSink(),
    )
    with pytest.warns(RuntimeWarning):
        logger.write("first.event")

    with pytest.warns(RuntimeWarning, match="evicted 1 oldest event"):
        event = logger.write("second.event")

    assert event.delivery_status == "buffered"
    buffered = logger.buffer.load()
    assert [item["event_type"] for item in buffered] == ["second.event"]
    assert logger.buffer.evicted_events() == 1

    diagnostic = logger.doctor()
    assert diagnostic["evicted_events"] == 1
    assert any("evicted 1 oldest event" in issue for issue in diagnostic["issues"])


def test_zero_capacity_buffer_refuses_to_buffer(tmp_path: Path) -> None:
    buffer = AuditBuffer(tmp_path / "buffer.jsonl", max_events=0)

    with pytest.raises(AuditBufferFullError, match="capacity is zero"):
        buffer.append({"event_type": "first.event"})


def test_flush_delivers_buffered_events(tmp_path: Path) -> None:
    logger = AuditLogger(
        AuditConfig(
            sink="http",
            http_url="https://audit.example/events",
            buffer_path=str(tmp_path / "buffer.jsonl"),
        ),
        sink=FailingSink(),
    )
    with pytest.warns(RuntimeWarning):
        logger.write("first.event")
    with pytest.warns(RuntimeWarning):
        logger.write("second.event")
    accepting = RecordingSink()
    logger.sink = accepting

    result = logger.flush()

    assert result.attempted == 2
    assert result.delivered == 2
    assert result.remaining == 0
    assert [event["event_type"] for event in accepting.events] == [
        "first.event",
        "second.event",
    ]
    assert logger.buffer.count() == 0


def test_http_audit_batching_is_explicitly_opt_in(tmp_path: Path) -> None:
    assert AuditConfig().http_batch_size == 1
    logger = AuditLogger(
        AuditConfig(
            sink="http",
            http_url="https://audit.example/events",
            buffer_path=str(tmp_path / "buffer.jsonl"),
            http_batch_size=2,
        ),
        sink=FailingSink(),
    )
    for event_type in ("first.event", "second.event"):
        with pytest.warns(RuntimeWarning):
            logger.write(event_type)
    batching = RecordingBatchSink()
    logger.sink = batching

    result = logger.flush()

    assert result.delivered == 2
    assert [[event["event_type"] for event in batch] for batch in batching.batches] == [
        ["first.event", "second.event"]
    ]


def test_partial_flush_preserves_failed_and_later_events(tmp_path: Path) -> None:
    logger = AuditLogger(
        AuditConfig(
            sink="http",
            http_url="https://audit.example/events",
            buffer_path=str(tmp_path / "buffer.jsonl"),
        ),
        sink=FailingSink(),
    )
    for event_type in ("first.event", "second.event", "third.event"):
        with pytest.warns(RuntimeWarning):
            logger.write(event_type)
    logger.sink = FailAfterOneSink()

    result = logger.flush()

    assert result.attempted == 2
    assert result.delivered == 1
    assert result.remaining == 2
    assert [event["event_type"] for event in logger.buffer.load()] == [
        "second.event",
        "third.event",
    ]


def test_http_sink_retries_with_exponential_backoff(monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_TOKEN", "secret-token")
    client = FlakyHttpClient(failures=2)
    delays: list[float] = []
    sink = HttpAuditSink(
        url="https://audit.example/events",
        token_env="AUDIT_TOKEN",
        max_retries=2,
        backoff_seconds=0.5,
        client=client,
        sleep_fn=delays.append,
    )

    sink.deliver({"event_id": "event-1"})

    assert client.attempts == 3
    assert delays == [0.5, 1.0]
    assert client.headers["Authorization"] == "Bearer secret-token"


def test_audit_doctor_reports_missing_http_configuration(tmp_path: Path) -> None:
    logger = AuditLogger(
        AuditConfig(
            sink="http",
            http_token_env="MISSING_AUDIT_TOKEN",
            buffer_path=str(tmp_path / "buffer.jsonl"),
        )
    )

    diagnostic = logger.doctor()

    assert diagnostic["ok"] is False
    assert len(diagnostic["issues"]) == 2


def test_audit_doctor_reports_corrupt_buffer(tmp_path: Path) -> None:
    buffer_path = tmp_path / "buffer.jsonl"
    buffer_path.write_text("not-json\n", encoding="utf-8")
    logger = AuditLogger(
        AuditConfig(
            sink="http",
            http_url="https://audit.example/events",
            buffer_path=str(buffer_path),
        )
    )

    diagnostic = logger.doctor()

    assert diagnostic["ok"] is False
    assert diagnostic["buffered_events"] == -1
    assert "Invalid audit buffer JSON" in diagnostic["issues"][0]


def test_disabled_audit_returns_visible_status_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    event = AuditLogger(AuditConfig(enabled=False, path=str(path))).write("disabled.event")

    assert event.delivery_status == "disabled"
    assert not path.exists()


class FailingSink:
    name = "http"

    def deliver(self, payload) -> None:
        raise AuditSinkError("collector unavailable")


class RecordingSink:
    name = "http"

    def __init__(self) -> None:
        self.events: list[dict] = []

    def deliver(self, payload) -> None:
        self.events.append(payload)


class RecordingBatchSink(RecordingSink):
    def __init__(self) -> None:
        super().__init__()
        self.batches: list[list[dict]] = []

    def deliver_batch(self, payloads) -> None:
        self.batches.append(payloads)


class FailAfterOneSink:
    name = "http"

    def __init__(self) -> None:
        self.deliveries = 0

    def deliver(self, payload) -> None:
        self.deliveries += 1
        if self.deliveries > 1:
            raise AuditSinkError("collector unavailable")


class FlakyHttpClient:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0
        self.headers: dict[str, str] = {}

    def post(self, url, *, json, headers, timeout):
        self.attempts += 1
        self.headers = headers
        if self.attempts <= self.failures:
            raise httpx.ConnectError("collector unavailable")
        return httpx.Response(202, request=httpx.Request("POST", url))
