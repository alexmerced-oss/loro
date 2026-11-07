"""Gate diagnostics, gate timeouts and rejection handling (AGS 1.0 SPEC 7.3 and 15)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from loro.agraph.execute import GraphExecutor
from loro.agraph.record import validate_run_record
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


def _executor(config: LoroConfig, tmp_path: Path, gate_provider=None) -> GraphExecutor:
    return GraphExecutor(
        config,
        workspace=tmp_path,
        runtime_factory=lambda _config: _Runtime(),
        gate_provider=gate_provider,
    )


def _node(record: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(node for node in record["nodes"] if node["node_id"] == node_id)


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
