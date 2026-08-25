"""Reattaching to a chat run.

The chat stream was cursor-capable on the server and the client ignored it: it
never sent `after`, never reconnected, and could not find its run after a
reload. Reloading mid-reply therefore lost the live view of a turn that was
still going.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loro.webui.services import MAX_RETAINED_RUNS, RunHandle


def _manager():
    """A run manager with only its registry, so no real turn is started."""
    import threading

    from loro.webui.services import RunManager

    manager = RunManager.__new__(RunManager)
    manager.handles = {}
    manager.active_conversations = set()
    manager.lock = threading.Lock()
    return manager


def _handle(manager, run_id: str, conversation_id: str, *, finished: bool = False) -> RunHandle:
    handle = RunHandle(run_id, conversation_id)
    handle.finished = finished
    manager.handles[run_id] = handle
    return handle


# --- finding the run a reloading browser lost --------------------------------


def test_the_active_run_is_found_by_conversation() -> None:
    """A reconnecting tab knows its conversation, not the run id it lost."""
    manager = _manager()
    _handle(manager, "run-old", "conv-1", finished=True)
    live = _handle(manager, "run-live", "conv-1")
    _handle(manager, "run-other", "conv-2")

    assert manager.active_for("conv-1").run_id == live.run_id


def test_a_finished_run_is_not_offered_for_reattachment() -> None:
    """It already wrote its reply; replaying would show the answer twice."""
    manager = _manager()
    _handle(manager, "run-done", "conv-1", finished=True)

    assert manager.active_for("conv-1") is None


def test_an_unknown_conversation_has_no_active_run() -> None:
    assert _manager().active_for("conv-missing") is None


def test_the_newest_run_wins_when_several_are_live() -> None:
    manager = _manager()
    _handle(manager, "run-first", "conv-1")
    newest = _handle(manager, "run-second", "conv-1")

    assert manager.active_for("conv-1").run_id == newest.run_id


# --- the snapshot a browser reattaches from ----------------------------------


def test_a_snapshot_carries_the_cursor_and_state() -> None:
    handle = RunHandle("run-1", "conv-1")
    handle.publish("run.started", run_id="run-1")
    handle.publish("assistant.delta", content="hi")

    snapshot = handle.snapshot()
    assert snapshot["run_id"] == "run-1"
    assert snapshot["conversation_id"] == "conv-1"
    assert snapshot["cursor"] == 2
    assert snapshot["finished"] is False


def test_a_snapshot_reports_an_approval_still_waiting() -> None:
    """A reattaching browser must re-render it, or the run waits on a question
    nobody can see."""
    from loro.webui.services import ApprovalDecision

    handle = RunHandle("run-1", "conv-1")
    handle.approvals["ask-1"] = ApprovalDecision(scope=None)
    assert handle.snapshot()["awaiting_approval"] == ["ask-1"]

    handle.approvals["ask-1"].resolved = True
    assert handle.snapshot()["awaiting_approval"] == []


# --- retention ---------------------------------------------------------------


def test_finished_runs_are_evicted_once_the_cap_is_passed() -> None:
    """Handles are kept for reattachment, not as history, and nothing ever
    removed them: a long session accumulated every run and its whole log."""
    manager = _manager()
    for index in range(MAX_RETAINED_RUNS + 10):
        _handle(manager, f"run-{index}", f"conv-{index}", finished=True)
        manager._evict()

    assert len(manager.handles) <= MAX_RETAINED_RUNS


def test_a_running_turn_is_never_evicted() -> None:
    """However old it is, it still has a reader coming."""
    manager = _manager()
    _handle(manager, "run-live", "conv-live")
    for index in range(MAX_RETAINED_RUNS + 10):
        _handle(manager, f"run-{index}", f"conv-{index}", finished=True)
        manager._evict()

    assert "run-live" in manager.handles


@pytest.mark.parametrize("after", [-1, 0, 5])
def test_events_replay_from_a_cursor(after: int, tmp_path: Path) -> None:
    """Replay is what makes reattachment work rather than losing the reply."""
    handle = RunHandle("run-1", "conv-1")
    for index in range(8):
        handle.publish("assistant.delta", content=str(index))

    replayed = handle.events[after + 1 :]
    assert [item["data"]["content"] for item in replayed] == [
        str(index) for index in range(after + 1, 8)
    ]
