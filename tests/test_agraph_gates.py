"""Gate diagnostics, gate timeouts and rejection handling (AGS 1.0 SPEC 7.3 and 15)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

import pytest
import yaml

from loro.agraph.execute import GateTimeoutError, GraphExecutionError, GraphExecutor
from loro.agraph.policy import evaluate_policy
from loro.agraph.record import validate_run_record
from loro.agraph.validate import validate_graph
from loro.config import LoroConfig

# The gate from the Appendix B walkthrough ("One approval gate").
WALKTHROUGH_GATE = """\
ags_version: "1.0"
kind: AgenticGraph
id: gewaa/gate-only
title: One approval gate
requires_conformance: 1
objective: Ask a maintainer to approve a release before anything else happens.
constraints:
  max_cost_usd: 0.1
entrypoints: [approve_release]
nodes:
  approve_release:
    type: gate
    title: Maintainer approves the release
    description: A maintainer approves or rejects the release.
    gate:
      mode: approve
      roles: [maintainer]
      prompt: "Approve the release?"
      timeout_seconds: 3600
      on_timeout: hold
      on_reject: fail
"""


class _Result:
    summary = "done"
    session_id = "fake"
    stop_reason = "completed"
    usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "cost_usd": 0.0}
    emitted_outputs: dict = {}


class _Runtime:
    def run(self, _prompt: str, mode: str) -> _Result:
        return _Result()


@pytest.fixture(autouse=True)
def _no_identity_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LORO_IDENTITY_ROLES", "LORO_IDENTITY_GROUPS", "LORO_IDENTITY_SUBJECT"):
        monkeypatch.delenv(name, raising=False)


def _config(tmp_path: Path, *, roles: list[str] | None = None, **agraph: Any) -> LoroConfig:
    return LoroConfig.model_validate(
        {
            "agraph": {"state_path": str(tmp_path / "runs"), **agraph},
            "safety": {"enabled": False},
            "identity": {"roles": roles if roles is not None else ["maintainer"]},
            "permissions": {"default": "allow", "edit": "allow"},
            "audit": {"path": str(tmp_path / "audit.jsonl")},
        }
    )


def _gate_graph(tmp_path: Path, gate: dict[str, Any], *, policy: dict | None = None) -> Path:
    document: dict[str, Any] = {
        "ags_version": "1.0",
        "kind": "AgenticGraph",
        "id": "loro.tests.gate",
        "title": "Gate",
        "objective": "Approve, then publish.",
        "requires_conformance": 1,
        "entrypoints": ["approve"],
        "nodes": {
            "approve": {
                "type": "gate",
                "title": "Approve",
                "description": "A maintainer approves the release.",
                "gate": {"mode": "approve", "prompt": "Approve?", **gate},
            },
            "publish": {
                "type": "task",
                "title": "Publish",
                "description": "Publish the release.",
                "depends_on": ["approve"],
            },
        },
    }
    if policy:
        document["policy"] = policy
    path = tmp_path / "gate.agraph.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _executor(config: LoroConfig, tmp_path: Path, gate_provider=None) -> GraphExecutor:
    return GraphExecutor(
        config,
        workspace=tmp_path,
        runtime_factory=lambda _config: _Runtime(),
        gate_provider=gate_provider,
    )


def _node(record: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(node for node in record["nodes"] if node["node_id"] == node_id)


def _backdate_gate(config: LoroConfig, tmp_path: Path, run_id: str, seconds: float) -> None:
    path = tmp_path / "runs" / f"{run_id}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    for node in record["nodes"]:
        if node["node_id"] == "approve":
            started = datetime.now(UTC) - timedelta(seconds=seconds)
            node["started_at"] = started.isoformat()
    _executor(config, tmp_path).store.save(record)


# --- L1: a blocked gate explains itself -------------------------------------------------------


def test_role_blocked_gate_records_required_and_held_roles(tmp_path: Path) -> None:
    path = tmp_path / "gate-only.agraph.yaml"
    path.write_text(WALKTHROUGH_GATE, encoding="utf-8")
    config = _config(tmp_path, roles=["developer"])
    record = _executor(config, tmp_path, gate_provider=lambda _p, _r: True).run(
        path, plan_approved=True
    )
    assert record["status"] == "failed"
    assert _node(record, "approve_release")["status"] == "blocked"
    [diagnostic] = [item for item in record["diagnostics"] if item["code"] == "LP013"]
    assert diagnostic["severity"] == "error"
    assert diagnostic["node_id"] == "approve_release"
    assert diagnostic["pointer"] == "/nodes/approve_release/gate/roles"
    assert "maintainer" in diagnostic["message"]
    assert "holds roles: developer" in diagnostic["message"]
    assert "LORO_IDENTITY_ROLES" in diagnostic["message"]
    validate_run_record(record)


def test_role_blocked_gate_with_no_roles_says_none(tmp_path: Path) -> None:
    path = tmp_path / "gate-only.agraph.yaml"
    path.write_text(WALKTHROUGH_GATE, encoding="utf-8")
    record = _executor(_config(tmp_path, roles=[]), tmp_path).run(path, plan_approved=True)
    [diagnostic] = [item for item in record["diagnostics"] if item["code"] == "LP013"]
    assert "holds roles: none" in diagnostic["message"]


# --- L2: gate timeouts are enforced ------------------------------------------------------------


def test_gate_timeout_fields_are_no_longer_reported_as_unenforced(tmp_path: Path) -> None:
    path = tmp_path / "gate-only.agraph.yaml"
    path.write_text(WALKTHROUGH_GATE, encoding="utf-8")
    from loro.agraph.document import load_graph

    report = validate_graph(load_graph(path))
    assert not [item for item in report.findings if item.code == "AG901"]


def test_paused_gate_fails_on_resume_after_its_timeout(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = _gate_graph(tmp_path, {"timeout_seconds": 60, "on_timeout": "fail"})
    paused = _executor(config, tmp_path).run(path, plan_approved=True)
    assert paused["status"] == "awaiting_human"
    gate = _node(paused, "approve")
    assert gate["started_at"]
    # Not yet expired: resuming without an approver pauses again with the same start time.
    again = _executor(config, tmp_path).resume(paused["run_id"])
    assert again["status"] == "awaiting_human"
    assert _node(again, "approve")["started_at"] == gate["started_at"]
    _backdate_gate(config, tmp_path, paused["run_id"], 120)
    # An approver arriving after the deadline is too late: on_timeout: fail applies.
    record = _executor(config, tmp_path, gate_provider=lambda _p, _r: True).resume(paused["run_id"])
    assert record["status"] == "failed"
    gate = _node(record, "approve")
    assert gate["status"] == "failed"
    assert gate["human_events"][0]["outcome"] == "timed_out"
    assert _node(record, "publish")["status"] == "skipped"
    [diagnostic] = [item for item in record["diagnostics"] if item["code"] == "LP014"]
    assert diagnostic["severity"] == "error" and "on_timeout: fail" in diagnostic["message"]
    validate_run_record(record)


def test_unanswered_live_gate_times_out_instead_of_hanging(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = _gate_graph(tmp_path, {"timeout_seconds": 0.3, "on_timeout": "fail"})
    release = Event()

    def never_answers(_prompt: str, _roles: tuple[str, ...]) -> bool:
        release.wait(30)
        return True

    started = time.monotonic()
    try:
        record = _executor(config, tmp_path, gate_provider=never_answers).run(
            path, plan_approved=True
        )
    finally:
        release.set()
    assert time.monotonic() - started < 10
    assert record["status"] == "failed"
    assert _node(record, "approve")["human_events"][0]["outcome"] == "timed_out"


def test_timeout_aware_provider_gets_remaining_time_and_hold_pauses(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = _gate_graph(tmp_path, {"timeout_seconds": 600, "on_timeout": "hold"})
    seen: list[float] = []

    def provider(_prompt: str, _roles: tuple[str, ...], timeout_seconds: float | None = None):
        seen.append(float(timeout_seconds or 0))
        raise GateTimeoutError("nobody answered")

    record = _executor(config, tmp_path, gate_provider=provider).run(path, plan_approved=True)
    assert 0 < seen[0] <= 600
    assert record["status"] == "awaiting_human"
    assert _node(record, "approve")["status"] == "awaiting_human"
    [diagnostic] = [item for item in record["diagnostics"] if item["code"] == "LP014"]
    assert diagnostic["severity"] == "info" and "hold" in diagnostic["message"]


def test_hold_still_accepts_a_late_approval(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = _gate_graph(tmp_path, {"timeout_seconds": 60, "on_timeout": "hold"})
    paused = _executor(config, tmp_path).run(path, plan_approved=True)
    _backdate_gate(config, tmp_path, paused["run_id"], 120)
    record = _executor(config, tmp_path, gate_provider=lambda _p, _r: True).resume(paused["run_id"])
    assert record["status"] == "succeeded"


def test_policy_default_human_timeout_applies_to_gates(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = _gate_graph(
        tmp_path,
        {},
        policy={"default_human_timeout_seconds": 60, "on_human_timeout": "fail"},
    )
    paused = _executor(config, tmp_path).run(path, plan_approved=True)
    _backdate_gate(config, tmp_path, paused["run_id"], 120)
    record = _executor(config, tmp_path).resume(paused["run_id"])
    assert record["status"] == "failed"
    assert any(item["code"] == "LP014" for item in record["diagnostics"])


def test_approve_on_timeout_is_refused_unless_policy_allows_it(tmp_path: Path) -> None:
    path = _gate_graph(tmp_path, {"timeout_seconds": 60, "on_timeout": "approve"})
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    [finding] = [
        f for f in evaluate_policy(document, _config(tmp_path).agraph) if f.code == "LP015"
    ]
    assert finding.pointer == "/nodes/approve/gate/on_timeout"
    assert "allow_approve_on_timeout = true" in finding.message
    with pytest.raises(GraphExecutionError, match="LP015"):
        _executor(_config(tmp_path), tmp_path).run(path, plan_approved=True)

    config = _config(tmp_path, allow_approve_on_timeout=True)
    paused = _executor(config, tmp_path).run(path, plan_approved=True)
    _backdate_gate(config, tmp_path, paused["run_id"], 120)
    record = _executor(config, tmp_path).resume(paused["run_id"])
    assert record["status"] == "succeeded"
    assert _node(record, "approve")["human_events"][0]["outcome"] == "timed_out"
    [diagnostic] = [item for item in record["diagnostics"] if item["code"] == "LP014"]
    assert diagnostic["severity"] == "warning"


def test_escalate_on_timeout_fails_closed_and_is_reported(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = _gate_graph(tmp_path, {"timeout_seconds": 60, "on_timeout": "escalate"})
    from loro.agraph.document import load_graph

    findings = validate_graph(load_graph(path)).findings
    assert [item.pointer for item in findings if item.code == "AG901"] == [
        "/nodes/approve/gate/on_timeout"
    ]
    paused = _executor(config, tmp_path).run(path, plan_approved=True)
    _backdate_gate(config, tmp_path, paused["run_id"], 120)
    record = _executor(config, tmp_path).resume(paused["run_id"])
    assert record["status"] == "failed"
    [diagnostic] = [item for item in record["diagnostics"] if item["code"] == "LP014"]
    assert "escalate" in diagnostic["message"]


# --- L2: on_reject ----------------------------------------------------------------------------


def test_on_reject_skip_dependents_skips_the_gate_and_its_dependents(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = _gate_graph(tmp_path, {"on_reject": "skip_dependents"})
    record = _executor(config, tmp_path, gate_provider=lambda _p, _r: False).run(
        path, plan_approved=True
    )
    gate = _node(record, "approve")
    assert gate["status"] == "skipped" and gate["decision"] == "rejected"
    assert _node(record, "publish")["status"] == "skipped"
    validate_run_record(record)


def test_on_reject_route_succeeds_with_rejected_decision(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = _gate_graph(tmp_path, {"on_reject": "route"})
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["nodes"]["publish"].pop("depends_on")
    document["nodes"]["notify_rejected"] = {
        "type": "task",
        "title": "Tell the author",
        "description": "Explain why the release was rejected.",
    }
    document["edges"] = [
        {
            "from": "approve",
            "to": "publish",
            "kind": "conditional",
            "when": "nodes.approve.outputs.decision == 'approved'",
        },
        {
            "from": "approve",
            "kind": "conditional",
            "to": "notify_rejected",
            "when": "nodes.approve.outputs.decision == 'rejected'",
        },
    ]
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    record = _executor(config, tmp_path, gate_provider=lambda _p, _r: False).run(
        path, plan_approved=True
    )
    assert _node(record, "approve")["status"] == "succeeded"
    assert _node(record, "approve")["decision"] == "rejected"
    assert _node(record, "publish")["status"] == "skipped"
    assert _node(record, "notify_rejected")["status"] == "succeeded"


def test_on_reject_fail_is_still_the_default(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = _gate_graph(tmp_path, {})
    record = _executor(config, tmp_path, gate_provider=lambda _p, _r: False).run(
        path, plan_approved=True
    )
    assert record["status"] == "failed"
    assert _node(record, "approve")["status"] == "failed"
