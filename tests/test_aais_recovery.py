from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
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
            timeout=120,
        )
        assert ready.wait(60)
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
        assert run.result(timeout=60) == "once"
    assert receipt["resolution"]["outcome"] == "approved"
    assert events[-1] == receipt
    assert presenter.snapshot()["snapshot"]["pending"] == []


# Real separate processes: the store's guarantees are cross-process, so thread-only tests
# would not prove them.

WORKER = """
import sys, threading
from pathlib import Path
from loro.aais_bridge import AAISBridge
from loro.approvals import ApprovalRequest

root, mode, count = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
bridge = AAISBridge(root)

def make():
    return ApprovalRequest(
        action="file.read", target="t.txt", arguments={}, identity_subject="s",
        identity_tenant="t", identity_session_id="sess", policy_decision="ask",
        policy_version="1", policy_source="test", policy_reason="r", risk_reason="r",
    )

if mode == "add":
    for _ in range(count):
        request = make()
        bridge.store.add_request(
            action={"kind": "tool.call", "name": "file.read", "summary": "read",
                    "arguments": {}},
            origin={"harness": "loro", "session_id": "s"},
            risk={"level": "low", "reasons": ["test"]},
            choices=[{"decision": "approve", "scope": "once", "label": "Allow"},
                     {"decision": "deny", "scope": "once", "label": "Deny"}],
            request_id=request.request_id,
        )
elif mode == "wait":
    request = make()
    scope = bridge.request(
        request, origin={}, publish=lambda name, env: print(name, env["sequence"], flush=True)
        if name == "approval.requested" else print("resolved", flush=True),
        allow_session=False, cancelled=threading.Event(), timeout=30,
    )
    print("scope", scope, flush=True)
elif mode == "hold":
    request = make()
    bridge.request(
        request, origin={}, publish=lambda name, env: print(name, flush=True),
        allow_session=False, cancelled=threading.Event(), timeout=300,
    )
"""


def _env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COV_CORE_") and key != "COVERAGE_PROCESS_START"
    }


def _spawn(root: Path, mode: str, count: int = 1) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", WORKER, str(root), mode, str(count)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
    )


def _pending(root: Path) -> list[dict]:
    return AAISBridge(root).store.pending_requests()


def _wait_for_pending(root: Path, count: int = 1, timeout: float = 30) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = _pending(root)
        if len(pending) >= count:
            return pending
        time.sleep(0.05)
    raise AssertionError("pending request never appeared")


def test_concurrent_processes_never_lose_or_reuse_sequences(tmp_path: Path) -> None:
    workers = [_spawn(tmp_path, "add", 6) for _ in range(4)]
    for worker in workers:
        _out, err = worker.communicate(timeout=600)
        assert worker.returncode == 0, err

    pending = _pending(tmp_path)
    assert len(pending) == 24
    sequences = [int(item["sequence"]) for item in pending]
    assert sorted(sequences) == list(range(1, 25))


def test_decision_from_this_process_wakes_a_waiting_process(tmp_path: Path) -> None:
    worker = _spawn(tmp_path, "wait")
    try:
        [pending] = _wait_for_pending(tmp_path)
        AAISBridge(tmp_path).decide(
            pending["request"]["id"], decision="approve", scope="once", actor_id="reviewer"
        )
        out, err = worker.communicate(timeout=300)
    finally:
        worker.kill()
    assert worker.returncode == 0, err
    assert "scope once" in out


def test_killed_owner_is_orphaned_and_cannot_be_approved(tmp_path: Path) -> None:
    worker = _spawn(tmp_path, "hold")
    [pending] = _wait_for_pending(tmp_path)
    request_id = pending["request"]["id"]
    worker.send_signal(signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    worker.wait(timeout=30)

    bridge = AAISBridge(tmp_path)
    assert bridge.recovery()["orphaned"] == [request_id]
    assert bridge.snapshot()["snapshot"]["pending"] == []
    with pytest.raises(ConflictError, match="issuing process stopped"):
        bridge.decide(request_id, decision="approve", scope="once", actor_id="tester")
    [receipt] = bridge.cancel_orphaned()
    assert receipt["resolution"]["outcome"] in {"cancelled", "denied"}
    assert bridge.recovery()["orphaned"] == []


def test_corrupt_state_is_quarantined_and_needs_acknowledgement(tmp_path: Path) -> None:
    bridge = AAISBridge(tmp_path)
    bridge.path.parent.mkdir()
    bridge.path.write_text('{"pending":')
    with pytest.raises(ApprovalError, match="loro approvals recovery --acknowledge"):
        bridge.snapshot()
    [quarantined] = list(bridge.path.parent.glob(bridge.path.name + ".corrupt-*"))
    assert quarantined.read_text() == '{"pending":'
    # Other processes keep refusing until an operator acknowledges.
    with pytest.raises(ApprovalError, match="recovery"):
        AAISBridge(tmp_path).snapshot()
    assert AAISBridge(tmp_path).recovery_status() is not None
    bridge.acknowledge_recovery()
    assert bridge.snapshot()["snapshot"]["pending"] == []


def test_legacy_state_is_migrated_once(tmp_path: Path) -> None:
    from aais import create_request

    envelope = create_request(
        action={"kind": "tool.call", "name": "file.read", "summary": "Read file", "arguments": {}},
        origin={"harness": "loro", "session_id": "fixture"},
        risk={"level": "low", "reasons": ["Protected action"]},
        choices=[
            {"decision": "approve", "scope": "once", "label": "Allow once"},
            {"decision": "deny", "scope": "once", "label": "Deny"},
        ],
        sequence=7,
        stream="loro.approvals",
    )
    key = envelope["request"]["id"]
    legacy = tmp_path / ".loro" / "aais-pending.json"
    legacy.parent.mkdir()
    legacy.write_text(
        json.dumps(
            {
                "schema": "loro.aais-store.v1",
                "sequence": 7,
                "presenter_sequence": 0,
                "pending": {key: envelope},
                "decisions": {},
                "resolutions": {},
                "events": [envelope],
                "owners": {key: os.getpid()},
            }
        )
    )

    bridge = AAISBridge(tmp_path)

    assert not legacy.exists()
    assert list(legacy.parent.glob("aais-pending.json.migrated-*"))
    assert [item["request"]["id"] for item in bridge.store.pending_requests()] == [key]
    receipt = bridge.decide(key, decision="deny", scope="once", actor_id="tester")
    assert int(receipt["sequence"]) == 8
    AAISBridge(tmp_path)  # a second start does not re-import


def test_resolved_entries_are_bounded_by_retention(tmp_path: Path) -> None:
    from aais.store import RetentionPolicy

    bridge = AAISBridge(tmp_path, retention=RetentionPolicy(max_resolved=3, max_events=5))
    for _ in range(7):
        envelope = bridge.store.add_request(
            action={"kind": "tool.call", "name": "file.read", "summary": "r", "arguments": {}},
            origin={"harness": "loro", "session_id": "s"},
            risk={"level": "low", "reasons": ["t"]},
            choices=[
                {"decision": "approve", "scope": "once", "label": "Allow"},
                {"decision": "deny", "scope": "once", "label": "Deny"},
            ],
        )
        bridge.deny(envelope["request"]["id"])
    state = json.loads(bridge.path.read_text())
    assert len(state["resolutions"]) <= 3
    assert len(state["decisions"]) <= 3
    assert len(state["owners"]) <= 3
    page = bridge.events_after(0)
    assert page["gap"] is True


def test_recovery_cli_reports_holds_and_acknowledges(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from loro.cli import app

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    clean = runner.invoke(app, ["approvals", "recovery", "--json"])
    assert clean.exit_code == 0, clean.output
    assert json.loads(clean.output)["hold"] is None

    state = tmp_path / ".loro" / "aais-approvals.json"
    state.write_text("not json")
    held = runner.invoke(app, ["approvals", "recovery"])
    assert held.exit_code == 1
    assert "--acknowledge" in held.output
    assert "Traceback" not in held.output

    fixed = runner.invoke(app, ["approvals", "recovery", "--acknowledge"])
    assert fixed.exit_code == 0, fixed.output
    assert "acknowledged" in fixed.output


def test_postgres_authority_never_falls_back_to_a_local_file(tmp_path, monkeypatch) -> None:
    """Regression: the pre-A-5 subclass overrode hooks a newer AAIS no longer calls, so it
    silently kept approvals in a local file at postgres/<stream>."""

    pytest.importorskip("psycopg")
    from aais.backends import ApprovalStateBackend, FileBackend

    from loro.aais_postgres import PostgresBackend
    from loro.approvals import ApprovalError

    loro_dir = tmp_path / ".loro"
    loro_dir.mkdir()
    (loro_dir / "config.local.toml").write_text(
        'schema_version = "1.0"\n[approvals]\nauthority = "postgres"\n', encoding="utf-8"
    )
    # Port 1 refuses connections immediately; credentials must never reach messages.
    monkeypatch.setenv("LORO_APPROVALS_DSN", "postgresql://loro:hunter2@127.0.0.1:1/approvals")
    monkeypatch.chdir(tmp_path)
    bridge = AAISBridge(tmp_path)
    backend = bridge.store.backend
    assert isinstance(backend, PostgresBackend) and not isinstance(backend, FileBackend)
    assert isinstance(backend, ApprovalStateBackend)
    with pytest.raises(ApprovalError, match="Could not connect") as raised:
        bridge.snapshot()
    assert "hunter2" not in str(raised.value)
    assert sorted(path.name for path in tmp_path.iterdir()) == [".loro"]
    assert sorted(path.name for path in loro_dir.iterdir()) == ["config.local.toml"]
