"""Agentic Graph support for the local Web UI.

Loro is an AGS level 3 harness whose graph runtime was unreachable from its own
UI. These cover discovery, planning, and the confinement rules, without running
a real model.
"""

from __future__ import annotations

import textwrap
import time
from importlib import import_module
from pathlib import Path

import pytest

from loro.webui.graphs import GraphService, confined_path, discover

GRAPH = textwrap.dedent(
    """
    ags_version: "1.0"
    kind: AgenticGraph
    id: demo/pipeline
    title: Demo pipeline
    objective: Prove the plan surface.

    entrypoints: [first]

    nodes:
      first:
        title: First card
        description: Do the first thing.
        intelligence:
          tier: standard
        requirements:
          tools: [file_read]
          permissions: ["fs:read:**"]
        success:
          summary: It happened.
          criteria:
            - id: done
              kind: file_exists
              description: A file exists.
              path: out.md

      gate:
        title: Human check
        type: gate
        description: A maintainer confirms before the run continues.
        depends_on: [first]
        gate:
          mode: approve
          prompt: Confirm before continuing.
          roles: [maintainer]
    """
).strip()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "release.agraph.yaml").write_text(GRAPH, encoding="utf-8")
    return tmp_path


def test_discovery_finds_graph_documents(workspace: Path) -> None:
    found = discover(workspace)
    assert [item["path"] for item in found] == ["release.agraph.yaml"]


def test_discovery_skips_dependency_and_vcs_directories(workspace: Path) -> None:
    for noisy in ("node_modules", ".git", "__pycache__", ".venv"):
        directory = workspace / noisy
        directory.mkdir()
        (directory / "buried.agraph.yaml").write_text(GRAPH, encoding="utf-8")

    assert [item["path"] for item in discover(workspace)] == ["release.agraph.yaml"]


def test_discovery_is_depth_bounded(workspace: Path) -> None:
    deep = workspace.joinpath(*[f"level{index}" for index in range(8)])
    deep.mkdir(parents=True)
    (deep / "deep.agraph.yaml").write_text(GRAPH, encoding="utf-8")

    assert [item["path"] for item in discover(workspace)] == ["release.agraph.yaml"]


def test_plan_reports_nodes_gates_and_digest(workspace: Path) -> None:
    summary = GraphService(workspace).plan("release.agraph.yaml")

    assert summary["ok"] is True
    assert summary["graph_id"] == "demo/pipeline"
    assert summary["title"] == "Demo pipeline"
    assert summary["node_count"] == 2
    assert summary["gates"] == ["gate"]
    assert summary["digest"].startswith("sha256-")

    ids = [node["id"] for node in summary["nodes"]]
    assert ids == ["first", "gate"]

    first, gate = summary["nodes"]
    assert first["type"] == "task"
    assert first["tier"] == "standard"
    assert first["depends_on"] == []
    assert gate["type"] == "gate"
    assert gate["depends_on"] == ["first"]
    # Every card starts in the To-do lane until a run moves it.
    assert {node["state"] for node in summary["nodes"]} == {"pending"}


def test_plan_reports_findings_for_an_invalid_graph(workspace: Path) -> None:
    (workspace / "broken.agraph.yaml").write_text(
        "ags_version: '1.0'\nkind: AgenticGraph\nid: broken\nnodes: {}\n", encoding="utf-8"
    )
    summary = GraphService(workspace).plan("broken.agraph.yaml")

    assert summary["ok"] is False
    assert any(finding.get("severity") == "error" for finding in summary["findings"])


def test_a_missing_graph_is_reported_rather_than_crashing(workspace: Path) -> None:
    with pytest.raises(FileNotFoundError):
        GraphService(workspace).plan("nope.agraph.yaml")


def test_starting_an_invalid_graph_is_refused_before_a_worker_is_taken(workspace: Path) -> None:
    (workspace / "broken.agraph.yaml").write_text(
        "ags_version: '1.0'\nkind: AgenticGraph\nid: broken\nnodes: {}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        GraphService(workspace).start("broken.agraph.yaml")


# --- confinement -------------------------------------------------------------


def test_paths_outside_the_workspace_are_refused(workspace: Path) -> None:
    for escape in ("../outside.agraph.yaml", "../../etc/passwd", "/etc/passwd"):
        with pytest.raises(ValueError):
            confined_path(workspace, escape)


def test_a_nested_path_inside_the_workspace_is_allowed(workspace: Path) -> None:
    nested = workspace / "flows"
    nested.mkdir()
    (nested / "inner.agraph.yaml").write_text(GRAPH, encoding="utf-8")

    resolved = confined_path(workspace, "flows/inner.agraph.yaml")
    assert resolved.is_file()
    assert workspace.resolve() in resolved.parents


def test_service_refuses_an_escaping_path(workspace: Path) -> None:
    with pytest.raises(ValueError):
        GraphService(workspace).plan("../escape.agraph.yaml")


# --- gates -------------------------------------------------------------------


def test_a_gate_decision_reaches_the_waiting_run(workspace: Path) -> None:
    from loro.webui.graphs import GateRequest, GraphRunHandle

    handle = GraphRunHandle("run-1", "release.agraph.yaml")
    handle.gate = GateRequest(request_id="gate-1", prompt="Confirm?", roles=["maintainer"])

    assert handle.decide_gate("gate-1", approved=True) is True
    assert handle.gate.decided.is_set()
    assert handle.gate.approved is True

    # A stale or duplicate decision must not resolve anything twice.
    assert handle.decide_gate("gate-1", approved=False) is False
    assert handle.decide_gate("other", approved=True) is False


def test_events_replay_from_a_cursor(workspace: Path) -> None:
    """A reconnecting browser must not lose events it already missed."""
    from loro.webui.graphs import GraphRunHandle

    handle = GraphRunHandle("run-2", "release.agraph.yaml")
    handle.publish("run.started", run_id="run-2")
    handle.publish("node.started", node_id="first")
    handle.publish("node.finished", node_id="first", status="succeeded")

    assert [event["seq"] for event in handle.since(-1)] == [0, 1, 2]
    assert [event["type"] for event in handle.since(0)] == ["node.started", "node.finished"]
    assert handle.since(2) == []


# --- authoring ---------------------------------------------------------------


def test_blank_document_is_valid_and_saveable(tmp_path: Path) -> None:
    service = GraphService(tmp_path)
    document = service.blank("Promo audit")

    assert document["kind"] == "AgenticGraph"
    assert list(document["nodes"]) == ["card_1"]

    saved = service.save("flows/promo.agraph.yaml", document)
    assert saved["ok"] is True
    assert saved["node_count"] == 1


def test_added_cards_chain_into_a_connected_graph(tmp_path: Path) -> None:
    service = GraphService(tmp_path)
    document = service.blank("Chained")
    for _ in range(3):
        document = service.add_card(document)["document"]

    assert list(document["nodes"]) == ["card_1", "card_2", "card_3", "card_4"]
    assert document["nodes"]["card_4"]["depends_on"] == ["card_3"]

    # A chained board must still validate, or "add card" would produce junk.
    assert service.save("chained.agraph.yaml", document)["ok"] is True


def test_save_refuses_an_invalid_document(tmp_path: Path) -> None:
    service = GraphService(tmp_path)
    with pytest.raises(ValueError):
        service.save("broken.agraph.yaml", {"kind": "AgenticGraph", "nodes": {}})
    assert not (tmp_path / "broken.agraph.yaml").exists()


def test_save_refuses_a_non_graph_suffix(tmp_path: Path) -> None:
    service = GraphService(tmp_path)
    with pytest.raises(ValueError):
        service.save("notes.txt", service.blank())


def test_a_rejected_save_leaves_no_staging_file(tmp_path: Path) -> None:
    """The staged file must never survive a failed validation."""
    service = GraphService(tmp_path)
    with pytest.raises(ValueError):
        service.save("broken.agraph.yaml", {"kind": "AgenticGraph", "nodes": {}})
    assert list(tmp_path.glob(".*staged*")) == []


def test_document_round_trips_through_save(tmp_path: Path) -> None:
    service = GraphService(tmp_path)
    document = service.add_card(service.blank("Round trip"))["document"]
    service.save("round.agraph.yaml", document)

    reloaded = service.document("round.agraph.yaml")
    assert list(reloaded["nodes"]) == list(document["nodes"])
    assert reloaded["title"] == "Round trip"


def test_serialise_emits_both_encodings(tmp_path: Path) -> None:
    from loro.webui.graphs import serialise

    document = GraphService(tmp_path).blank()
    assert serialise(document, ".yaml").startswith("ags_version:")
    assert serialise(document, ".json").lstrip().startswith("{")


def test_deterministic_generation_needs_no_provider(tmp_path: Path) -> None:
    document = GraphService(tmp_path).generate("Ship the thing", use_ai=False)
    assert document["kind"] == "AgenticGraph"
    assert document["nodes"]
    # Nothing is written until the user saves the draft.
    assert list(tmp_path.glob("*.agraph.*")) == []


def test_generation_rejects_an_empty_goal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        GraphService(tmp_path).generate("   ")


def test_background_generation_reports_lifecycle_and_document(tmp_path: Path) -> None:
    service = GraphService(tmp_path)
    started = service.start_draft("Ship the thing", use_ai=False)
    status = service.draft_status(started["job_id"])
    for _ in range(100):
        if status["status"] != "running":
            break
        time.sleep(0.01)
        status = service.draft_status(started["job_id"])
    assert status["status"] == "completed"
    assert status["stage"] == "validated"
    assert status["document"]["kind"] == "AgenticGraph"


def test_active_lists_only_runs_still_in_memory(tmp_path: Path) -> None:
    """`history` reads persisted records, which is the wrong question here.

    A browser that reloaded mid-run needs the live handle it can still reattach
    to, and a finished record is not one.
    """
    from loro.webui.graphs import GraphRunHandle, GraphService

    service = GraphService(tmp_path)
    running = GraphRunHandle("run_live", "graphs/build.yaml")
    running.publish("node.started", node_id="plan")
    finished = GraphRunHandle("run_done", "graphs/old.yaml")
    finished.status = "succeeded"
    service.handles["run_live"] = running
    service.handles["run_done"] = finished

    active = service.active()
    assert [item["run_id"] for item in active] == ["run_live"]
    assert active[0]["path"] == "graphs/build.yaml"
    # The cursor lets a client decide whether it has already seen everything.
    assert active[0]["cursor"] == 1
    assert active[0]["awaiting_gate"] is False


def test_active_reports_a_run_waiting_on_a_gate(tmp_path: Path) -> None:
    """A reattaching browser has to re-render the approval, or the run hangs."""
    from loro.webui.graphs import GateRequest, GraphRunHandle, GraphService

    service = GraphService(tmp_path)
    handle = GraphRunHandle("run_gate", "graphs/build.yaml")
    handle.gate = GateRequest(request_id="gate-1", prompt="Run shell?", roles=["operator"])
    service.handles["run_gate"] = handle

    assert service.active()[0]["awaiting_gate"] is True


def test_graph_tool_approval_is_answered_through_browser_gate(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A graph tool must never fall back to a hidden terminal prompt."""
    from loro.approvals import ApprovalRequest

    graph_execute = import_module("loro.agraph.execute")

    observed: dict[str, object] = {}

    class FakeExecutor:
        def __init__(self, _config: object, **kwargs: object) -> None:
            observed["provider"] = kwargs["approval_provider"]

        def run(self, _path: Path, **_kwargs: object) -> dict[str, object]:
            provider = observed["provider"]
            request = ApprovalRequest(
                action="shell.run",
                target="workspace",
                arguments={"command": "npm test"},
                identity_subject="operator",
                identity_tenant="local",
                identity_session_id="graph-run",
                policy_decision="ask",
                policy_version="1",
                policy_source="test",
                policy_reason="Shell execution needs confirmation.",
                risk_reason="Runs a local process.",
            )
            observed["decision"] = provider(request)  # type: ignore[operator]
            return {"status": "succeeded"}

    monkeypatch.setattr(graph_execute, "GraphExecutor", FakeExecutor)
    service = GraphService(workspace)
    handle = service.start("release.agraph.yaml")
    deadline = time.monotonic() + 2
    pending: list[dict[str, object]] = []
    while not pending and time.monotonic() < deadline:
        pending = service.aais.snapshot()["snapshot"]["pending"]
        time.sleep(0.01)

    assert pending
    assert pending[0]["action"]["name"] == "shell.run"  # type: ignore[index]
    assert pending[0]["action"]["arguments"]["command"] == "npm test"  # type: ignore[index]
    service.aais.decide(
        str(pending[0]["id"]),
        decision="approve",
        scope="once",
        actor_id="test-operator",
    )

    deadline = time.monotonic() + 2
    while handle.status == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert handle.status == "succeeded"
    assert observed["decision"] == "once"


def test_active_is_empty_when_nothing_is_running(tmp_path: Path) -> None:
    from loro.webui.graphs import GraphService

    assert GraphService(tmp_path).active() == []
