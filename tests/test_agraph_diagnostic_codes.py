"""Runtime diagnostic codes keep their AGS 1.0 meaning (SPEC 18.2)."""

from __future__ import annotations

from pathlib import Path

import yaml

from loro.agraph.execute import GraphExecutor
from loro.config import LoroConfig

READ_ONLY = """\
ags_version: "1.0"
kind: AgenticGraph
id: loro.tests.read-only
title: Read only
objective: Read the README.
requires_conformance: 1
entrypoints: [read]
nodes:
  read:
    type: task
    title: Read
    description: Read the README.
    requirements:
      permissions: ["fs:read:**"]
"""


def _config(tmp_path: Path, **permissions: str) -> LoroConfig:
    return LoroConfig.model_validate(
        {
            "agraph": {"state_path": str(tmp_path / "runs")},
            "safety": {"enabled": False},
            "permissions": {"default": "allow", **permissions},
            "audit": {"enabled": False},
        }
    )


def test_denied_permission_uses_a_code_that_does_not_collide_with_rt021(tmp_path: Path) -> None:
    path = tmp_path / "read.agraph.yaml"
    path.write_text(READ_ONLY, encoding="utf-8")
    record = GraphExecutor(_config(tmp_path, edit="ask"), workspace=tmp_path).run(
        path, plan_approved=True
    )
    assert record["status"] == "failed"
    codes = [item["code"] for item in record["diagnostics"]]
    assert "RT021" not in codes
    [diagnostic] = [item for item in record["diagnostics"] if item["code"] == "LP012"]
    assert diagnostic["node_id"] == "read"
    assert diagnostic["pointer"] == "/nodes/read/requirements/permissions"
    # Names the policy that decided, so the reader knows what to change.
    assert "permissions.edit" in diagnostic["message"] and "'ask'" in diagnostic["message"]


def test_rt021_now_means_a_loop_hit_max_iterations(tmp_path: Path) -> None:
    body = {
        "entrypoints": ["step"],
        "nodes": {
            "step": {
                "type": "decision",
                "title": "Step",
                "description": "Step",
                "decision": {
                    "evaluator": "expression",
                    "branches": [
                        {"label": "yes", "description": "yes", "when": "true"},
                        {"label": "no", "description": "no", "when": "false"},
                    ],
                },
            }
        },
    }
    document = {
        "ags_version": "1.0",
        "kind": "AgenticGraph",
        "id": "loro.tests.loop",
        "title": "Loop",
        "objective": "Loop until a condition that never holds.",
        "requires_conformance": 1,
        "entrypoints": ["retry"],
        "nodes": {
            "retry": {
                "type": "loop",
                "title": "Retry",
                "description": "Retry until done.",
                "loop": {
                    "mode": "until",
                    "condition": "false",
                    "max_iterations": 2,
                    "body": body,
                },
            }
        },
    }
    path = tmp_path / "loop.agraph.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    record = GraphExecutor(_config(tmp_path), workspace=tmp_path).run(path, plan_approved=True)
    assert record["status"] == "failed"
    [diagnostic] = [item for item in record["diagnostics"] if item["code"] == "RT021"]
    assert diagnostic["node_id"] == "retry" and "max_iterations=2" in diagnostic["message"]
