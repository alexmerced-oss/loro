from __future__ import annotations

import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from loro.audit import AuditLogger
from loro.audit.collector import (
    AuditCollector,
    AuditCollectorAuthError,
    AuditCollectorError,
    create_audit_collector_server,
    token_from_environment,
)
from loro.audit.metrics import OperationalMetrics
from loro.config import AuditConfig


def test_collector_authentication_deduplication_and_chain(tmp_path: Path) -> None:
    collector = AuditCollector(tmp_path / "collector.sqlite3", "test-token")
    if os.name == "posix":
        assert collector.path.stat().st_mode & 0o777 == 0o600
    first = _event("runtime.task_started")
    second = _event("memory.shared_committed")
    body = json.dumps({"events": [first, second]}).encode()

    with pytest.raises(AuditCollectorAuthError):
        collector.accept("Bearer wrong", body)

    accepted = collector.accept("Bearer test-token", body)
    duplicate = collector.accept("Bearer test-token", body)
    verification = collector.verify()

    assert accepted.accepted == 2
    assert duplicate.duplicates == 2
    assert duplicate.final_hash == accepted.final_hash
    assert verification.ok
    assert verification.events == 2
    assert "loro_audit_collector_events 2" in collector.prometheus()


def test_collector_conflict_rolls_back_entire_batch(tmp_path: Path) -> None:
    collector = AuditCollector(tmp_path / "collector.sqlite3", "test-token")
    original = _event("runtime.task_started")
    collector.accept("Bearer test-token", json.dumps(original).encode())
    conflicting = {**original, "event_type": "runtime.task_completed"}
    new_event = _event("provider.requested")

    with pytest.raises(AuditCollectorError, match="different content"):
        collector.accept(
            "Bearer test-token",
            json.dumps({"events": [new_event, conflicting]}).encode(),
        )

    assert collector.verify().events == 1


def test_collector_verification_detects_database_tampering(tmp_path: Path) -> None:
    path = tmp_path / "collector.sqlite3"
    collector = AuditCollector(path, "test-token")
    collector.accept("Bearer test-token", json.dumps(_event("audit.test")).encode())

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE audit_events SET payload_json = '{}' WHERE sequence = 1")
        connection.commit()

    result = collector.verify()
    assert not result.ok
    assert result.issue == "Event hash does not match."


def test_collector_http_endpoints(tmp_path: Path) -> None:
    collector = AuditCollector(tmp_path / "collector.sqlite3", "test-token")
    try:
        server = create_audit_collector_server(collector, port=0)
    except PermissionError:
        pytest.skip("Loopback sockets are unavailable in this sandbox.")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        event_request = urllib.request.Request(
            base_url + "/events",
            data=json.dumps(_event("runtime.task_started")).encode(),
            headers={
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(event_request, timeout=2) as response:
            assert response.status == 200
            assert json.loads(response.read())["accepted"] == 1

        with urllib.request.urlopen(base_url + "/health", timeout=2) as response:
            assert json.loads(response.read())["ok"] is True
        with urllib.request.urlopen(base_url + "/metrics", timeout=2) as response:
            assert b"loro_audit_collector_events 1" in response.read()

        unauthorized = urllib.request.Request(
            base_url + "/events",
            data=b"{}",
            headers={"Authorization": "Bearer wrong"},
        )
        with pytest.raises(urllib.error.HTTPError) as unauthorized_error:
            urllib.request.urlopen(unauthorized, timeout=2)
        assert unauthorized_error.value.code == 401
        unauthorized_error.value.close()

        malformed = urllib.request.Request(
            base_url + "/events",
            data=b"{",
            headers={"Authorization": "Bearer test-token"},
        )
        with pytest.raises(urllib.error.HTTPError) as malformed_error:
            urllib.request.urlopen(malformed, timeout=2)
        assert malformed_error.value.code == 400
        malformed_error.value.close()

        with pytest.raises(urllib.error.HTTPError) as missing_error:
            urllib.request.urlopen(base_url + "/missing", timeout=2)
        assert missing_error.value.code == 404
        missing_error.value.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_collector_validation_and_token_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = AuditCollector(tmp_path / "limited.sqlite3", "test-token", max_body_bytes=64)
    with pytest.raises(AuditCollectorError, match="max_body_bytes"):
        collector.accept("Bearer test-token", b"x" * 65)
    collector = AuditCollector(tmp_path / "collector.sqlite3", "test-token")
    with pytest.raises(AuditCollectorError, match="invalid events"):
        collector.accept("Bearer test-token", json.dumps({"events": []}).encode())

    for update, message in (
        ({"schema_version": "2.0"}, "schema version"),
        ({"event_id": "invalid"}, "UUID"),
        ({"event_type": "INVALID"}, "event_type"),
        ({"timestamp": None}, "timestamp"),
    ):
        with pytest.raises(AuditCollectorError, match=message):
            collector.accept(
                "Bearer test-token",
                json.dumps({**_event("audit.test"), **update}).encode(),
            )

    monkeypatch.delenv("LORO_TEST_COLLECTOR_TOKEN", raising=False)
    with pytest.raises(AuditCollectorError, match="environment variable is missing"):
        token_from_environment("LORO_TEST_COLLECTOR_TOKEN")
    monkeypatch.setenv("LORO_TEST_COLLECTOR_TOKEN", "configured")
    assert token_from_environment("LORO_TEST_COLLECTOR_TOKEN") == "configured"


def test_operational_metrics_never_persist_content(tmp_path: Path) -> None:
    metrics_path = tmp_path / "metrics.json"
    logger = AuditLogger(
        AuditConfig(
            path=str(tmp_path / "audit.jsonl"),
            metrics_enabled=True,
            metrics_path=str(metrics_path),
        )
    )

    logger.write(
        "runtime.task_completed",
        prompt="never persist this prompt",
        content="nor this memory content",
        duration_ms=125,
        input_tokens=30,
        output_tokens=10,
        stop_reason="complete",
    )

    raw = metrics_path.read_text(encoding="utf-8")
    snapshot = OperationalMetrics(metrics_path).snapshot()
    assert "never persist" not in raw
    assert "memory content" not in raw
    assert snapshot.counters["events_total"] == 1
    assert snapshot.counters["family.runtime"] == 1
    assert snapshot.sums["task_duration_ms"] == 125
    assert snapshot.sums["provider_input_tokens"] == 30


def test_operational_metrics_cover_governed_families_and_reject_bad_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metrics.json"
    metrics = OperationalMetrics(path)
    metrics.observe(
        "approval.used",
        {"status": "approved once", "cost_usd": 0.25, "queue_depth": 3, "ignored": True},
        delivery_status="buffered/retry",
    )
    metrics.observe("memory.shared_committed", {}, delivery_status="delivered")
    metrics.observe("gateway.rejected", {}, delivery_status="delivered")
    metrics.observe("unknown.event", {}, delivery_status="failed")

    snapshot = metrics.snapshot()
    assert snapshot.counters["approval.used"] == 1
    assert snapshot.counters["memory.shared_committed"] == 1
    assert snapshot.counters["gateway.rejected"] == 1
    assert snapshot.counters["family.unknown"] == 1
    assert snapshot.counters["result.approved_once"] == 1
    assert snapshot.sums["provider_cost_usd"] == 0.25
    assert snapshot.sums["gateway_queue_depth_observed"] == 3
    assert 'metric="delivery.buffered_retry"' in metrics.prometheus()

    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Invalid operational metrics state"):
        metrics.snapshot()
    path.write_text(
        json.dumps({"schema_version": "2.0", "counters": {}, "sums": {}}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Unsupported operational metrics schema"):
        metrics.snapshot()


def _event(event_type: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": str(uuid4()),
        "event_type": event_type,
        "timestamp": "2026-08-11T00:00:00+00:00",
        "details": {},
    }
