"""The Postgres AAIS backend against a real Postgres 16, from several processes.

``TestPostgresBackendConformance`` runs the AAIS backend conformance kit; the rest checks Loro's
wiring (the bridge must really use Postgres, never a local file fallback).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from aais.testing import BackendConformance

pytestmark = pytest.mark.integration

ACTION = {"kind": "tool.call", "name": "shell.run", "summary": "Run", "arguments": {"x": 1}}
CHOICES = [
    {"decision": "approve", "scope": "once", "label": "Allow"},
    {"decision": "deny", "scope": "once", "label": "Deny"},
]


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    if os.environ.get("LORO_INTEGRATION_POSTGRES") != "1":
        pytest.skip("Set LORO_INTEGRATION_POSTGRES=1 to run Postgres container tests.")
    try:
        import psycopg  # noqa: F401
        from testcontainers.community.postgres import PostgresContainer
    except ModuleNotFoundError as error:
        pytest.skip(f"Missing integration dependency: {error.name}")
    container = PostgresContainer("postgres:16-alpine").with_tmpfs_mount("/var/lib/postgresql/data")
    container.start()
    try:
        yield _psycopg_dsn(container.get_connection_url())
    finally:
        container.stop()


def _store(dsn: str, stream: str):
    from loro.aais_postgres import postgres_authority

    return postgres_authority(dsn, stream=stream)


class TestPostgresBackendConformance(BackendConformance):
    """The AAIS backend conformance kit against ``PostgresBackend``.

    ``corrupt_backend`` is not implemented: a JSONB column cannot hold undecodable bytes.
    """

    @pytest.fixture(autouse=True)
    def _database(self, dsn: str) -> None:
        self.dsn = dsn

    def make_backend(self):
        from loro.aais_postgres import PostgresBackend

        return PostgresBackend(self.dsn, key=f"conformance-{uuid.uuid4().hex}", lock_timeout=5)

    def reopen_backend(self, backend):
        from loro.aais_postgres import PostgresBackend

        return PostgresBackend(self.dsn, key=backend.key, lock_timeout=5)

    def subprocess_backend_factory(self, backend):
        from loro.aais_postgres import PostgresBackend

        return PostgresBackend, (self.dsn, backend.key)


def test_request_decide_replay_and_snapshot(dsn: str) -> None:
    from aais import ConflictError

    store = _store(dsn, "t.basic")
    envelope = store.add_request(
        action=ACTION,
        origin={"harness": "loro", "session_id": "s"},
        risk={"level": "low", "reasons": ["r"]},
        choices=CHOICES,
        ttl=600,
    )
    request_id = envelope["request"]["id"]
    assert [item["id"] for item in store.snapshot()["snapshot"]["pending"]] == [request_id]
    actor = {"id": "alex", "type": "human", "authenticated_by": "oidc"}
    receipt = store.decide(request_id, decision="approve", scope="once", actor=actor)
    assert receipt["resolution"]["outcome"] == "approved"
    assert store.decide(request_id, decision="approve", scope="once", actor=actor) == receipt
    with pytest.raises(ConflictError):
        store.decide(request_id, decision="deny", scope="once", actor=actor)
    page = store.events_after(0)
    assert [event["sequence"] for event in page.events] == [1, 2] and not page.gap


WORKER = """
import sys
from loro.aais_postgres import postgres_authority
store = postgres_authority(sys.argv[1], stream=sys.argv[2])
for _ in range(int(sys.argv[3])):
    store.add_request(
        action={"kind": "tool.call", "name": "x", "summary": "x", "arguments": {}},
        origin={"harness": "loro", "session_id": "s"},
        risk={"level": "low", "reasons": ["r"]},
        choices=[{"decision": "approve", "scope": "once", "label": "A"},
                 {"decision": "deny", "scope": "once", "label": "D"}],
    )
"""


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("COV_CORE_")}


def test_concurrent_processes_share_one_sequence(dsn: str) -> None:
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, dsn, "t.concurrent", "10"],
            env=_env(),
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    for worker in workers:
        _out, err = worker.communicate(timeout=300)
        assert worker.returncode == 0, err
    pending = _store(dsn, "t.concurrent").pending_requests()
    assert sorted(int(item["sequence"]) for item in pending) == list(range(1, 41))


def test_decision_from_another_process_wakes_the_waiter(dsn: str) -> None:
    store = _store(dsn, "t.wait")
    envelope = store.add_request(
        action=ACTION,
        origin={"harness": "loro", "session_id": "s"},
        risk={"level": "low", "reasons": ["r"]},
        choices=CHOICES,
    )
    request_id = envelope["request"]["id"]
    decider = (
        "import sys\nfrom loro.aais_postgres import postgres_authority\n"
        "postgres_authority(sys.argv[1], stream='t.wait').decide(sys.argv[2], "
        "decision='deny', scope='once', actor={'id':'bob','type':'human',"
        "'authenticated_by':'oidc'})\n"
    )
    threading.Timer(
        0.5,
        lambda: subprocess.run([sys.executable, "-c", decider, dsn, request_id], env=_env()),
    ).start()
    started = time.monotonic()
    resolution = store.wait_for_resolution(request_id, timeout=60)
    assert resolution is not None and resolution["resolution"]["outcome"] == "denied"
    assert time.monotonic() - started < 60


def test_foreign_host_owner_is_never_treated_as_stopped(dsn: str) -> None:
    from aais.liveness import OwnerIdentity

    store = _store(dsn, "t.hosts")
    envelope = store.add_request(
        action=ACTION,
        origin={"harness": "loro", "session_id": "s"},
        risk={"level": "low", "reasons": ["r"]},
        choices=CHOICES,
        owner=OwnerIdentity(pid=999_999, process_start_time=1.0, host_id="other-host"),
    )
    report = store.recovery()
    assert envelope["request"]["id"] in report.unverified_owner
    assert report.orphaned == []


def test_bridge_uses_postgres_when_configured(dsn: str, tmp_path: Path, monkeypatch) -> None:
    from loro.aais_bridge import AAISBridge

    loro_dir = tmp_path / ".loro"
    loro_dir.mkdir()
    (loro_dir / "config.local.toml").write_text(
        'schema_version = "1.0"\n[approvals]\nauthority = "postgres"\n', encoding="utf-8"
    )
    monkeypatch.setenv("LORO_APPROVALS_DSN", dsn)
    monkeypatch.chdir(tmp_path)
    bridge = AAISBridge(tmp_path)
    from aais.backends import FileBackend

    from loro.aais_postgres import PostgresBackend

    assert isinstance(bridge.store.backend, PostgresBackend)
    assert not isinstance(bridge.store.backend, FileBackend)
    assert bridge.snapshot()["type"] == "approval.snapshot"
    bridge.store.add_request(
        action=ACTION,
        origin={"harness": "loro", "session_id": "s"},
        risk={"level": "low", "reasons": ["r"]},
        choices=CHOICES,
    )
    # The request is in the database row, and nothing was written to local files: the old
    # subclass silently fell back to a file at postgres/<stream> under a newer AAIS.
    import psycopg

    with psycopg.connect(dsn) as connection:
        row = connection.execute(
            "SELECT state->'pending' FROM loro_aais_state WHERE stream = 'loro.approvals'"
        ).fetchone()
    assert row is not None and len(row[0]) >= 1
    assert not (loro_dir / "aais-approvals.json").exists()
    assert not (tmp_path / "postgres").exists()
    monkeypatch.delenv("LORO_APPROVALS_DSN")
    from loro.approvals import ApprovalError

    with pytest.raises(ApprovalError, match="LORO_APPROVALS_DSN"):
        AAISBridge(tmp_path)


def test_quarantine_and_recovery_through_the_bridge(dsn: str, tmp_path: Path, monkeypatch) -> None:
    import psycopg

    from loro.aais_bridge import AAISBridge
    from loro.approvals import ApprovalError

    stream = f"t.recovery.{uuid.uuid4().hex}"
    store = _store(dsn, stream)
    store.add_request(
        action=ACTION,
        origin={"harness": "loro", "session_id": "s"},
        risk={"level": "low", "reasons": ["r"]},
        choices=CHOICES,
    )
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE loro_aais_state SET state = jsonb_set(state, '{events}', '{}'::jsonb) "
            "WHERE stream = %s",
            (stream,),
        )
    bridge = AAISBridge(tmp_path, store=store)
    with pytest.raises(ApprovalError, match="loro_aais_quarantine id"):
        bridge.snapshot()
    status = bridge.recovery_status()
    assert status is not None and "events" in status["reason"]
    assert "password" not in str(status) and "@" not in str(status["path"])
    bridge.acknowledge_recovery()
    assert bridge.recovery_status() is None
    assert bridge.snapshot()["type"] == "approval.snapshot"
