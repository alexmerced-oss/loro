from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from aais import ConflictError

from loro.aais_bridge import AAISBridge
from loro.approvals import ApprovalError, ApprovalRequest


def make_request() -> ApprovalRequest:
    return ApprovalRequest(
        action="file.read",
        target="test.txt",
        arguments={},
        identity_subject="tester",
        identity_tenant="test",
        identity_session_id="test-session",
        policy_decision="ask",
        policy_version="1",
        policy_source="test",
        policy_reason="Test",
        risk_reason="Test",
    )


def test_external_decision_wakes_owner_and_rejects_changed_digest(tmp_path: Path) -> None:
    owner, presenter = AAISBridge(tmp_path), AAISBridge(tmp_path)
    ready = threading.Event()
    request = make_request()
    events = []

    def publish(name, envelope):
        events.append(envelope)
        ready.set()

    with ThreadPoolExecutor() as pool:
        run = pool.submit(
            owner.request,
            request,
            origin={},
            publish=publish,
            allow_session=False,
            cancelled=threading.Event(),
            timeout=5,
        )
        assert ready.wait(5)
        with pytest.raises(ConflictError):
            presenter.decide(
                request.request_id,
                decision="approve",
                scope="once",
                actor_id="tester",
                reviewed_digest="sha256:" + "0" * 64,
            )
        receipt = presenter.decide(
            request.request_id, decision="approve", scope="once", actor_id="tester"
        )
        assert run.result(timeout=3) == "once"
    assert receipt["resolution"]["outcome"] == "approved"
    assert events[-1] == receipt
    assert presenter.snapshot()["snapshot"]["pending"] == []


def test_concurrent_bridge_transactions_preserve_all_requests(tmp_path: Path) -> None:
    ready = [threading.Event() for _ in range(4)]
    requests = [make_request() for _ in ready]
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = [
            pool.submit(
                AAISBridge(tmp_path).request,
                request,
                origin={},
                publish=lambda *args, event=event: event.set(),
                allow_session=False,
                cancelled=threading.Event(),
                timeout=10,
            )
            for request, event in zip(requests, ready, strict=True)
        ]
        assert all(event.wait(5) for event in ready)
        presenter = AAISBridge(tmp_path)
        assert len(presenter.snapshot()["snapshot"]["pending"]) == 4
        for request in requests:
            presenter.deny(request.request_id)
        assert [run.result(timeout=3) for run in runs] == [None] * 4
    state = json.loads(presenter.path.read_text())
    assert len({event["sequence"] for event in state["events"]}) == 8


def test_corrupt_approval_state_is_preserved(tmp_path: Path) -> None:
    bridge = AAISBridge(tmp_path)
    bridge.path.parent.mkdir()
    bridge.path.write_text('{"pending":')
    with pytest.raises(ApprovalError, match="recovery"):
        bridge.snapshot()
    assert bridge.path.read_text() == '{"pending":'


def test_dead_owner_is_visible_as_recovery_not_approvable(tmp_path, monkeypatch):
    bridge = AAISBridge(tmp_path)
    from aais import create_request

    envelope = create_request(
        action={"kind": "tool.call", "name": "file.read", "summary": "Read file", "arguments": {}},
        origin={"harness": "loro", "session_id": "fixture"},
        risk={"level": "low", "reasons": ["Protected action"]},
        choices=[
            {"decision": "approve", "scope": "once", "label": "Allow once"},
            {"decision": "deny", "scope": "once", "label": "Deny"},
        ],
        sequence=1,
        stream="test",
    )
    state = bridge._empty()
    key = envelope["request"]["id"]
    state["pending"][key] = envelope
    state["owners"] = {key: 12345}
    bridge._write(state)
    monkeypatch.setattr(bridge, "_owner_alive", lambda pid: False)
    assert bridge.recovery()["orphaned"] == [key]
    assert bridge.snapshot()["snapshot"]["pending"] == []
    with pytest.raises(ConflictError, match="issuing process stopped"):
        bridge.decide(key, decision="approve", scope="once", actor_id="tester")
