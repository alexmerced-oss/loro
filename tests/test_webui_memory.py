"""Memory surfaces for the Web UI.

Loro keeps local memories, a queue of proposals awaiting a human decision, and
governed shared memory. None of it was reachable from the browser, so the memory
shaping every reply was invisible and the queue could only be reviewed from a
terminal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from loro.config import load_config
from loro.memory.proposals import MemoryProposal, MemoryProposalStore
from loro.webui.memory import MemoryService


@pytest.fixture
def service(tmp_path: Path) -> MemoryService:
    """A service pointed at an empty workspace, never the real one."""
    memory_root = tmp_path / "memory"
    config = load_config(tmp_path)
    config.memory.local.path = str(memory_root)
    built = MemoryService(tmp_path)
    built._config = lambda: config  # type: ignore[method-assign]
    return built


def _propose(service: MemoryService, content: str, target: str = "local") -> Any:
    config = service._config()
    store = MemoryProposalStore(Path(config.memory.local.path))
    return store.propose(MemoryProposal(content=content, target=target, rationale="stated twice"))


# --- overview ----------------------------------------------------------------


def test_overview_reports_each_kind_of_memory(service: MemoryService) -> None:
    payload = service.overview()

    assert payload["ok"] is True
    for section in ("local", "proposals", "shared"):
        assert section in payload, section


def test_overview_counts_what_is_waiting_on_a_decision(service: MemoryService) -> None:
    _propose(service, "The user prefers tabs.")
    _propose(service, "Deploys happen on Fridays.")

    assert service.overview()["proposals"]["pending"] == 2


def test_overview_reports_the_shared_backend_even_when_it_is_down(
    service: MemoryService,
) -> None:
    """An unreachable backend must be named, not hidden behind an empty list."""
    shared = service.overview()["shared"]

    assert shared["backend"]
    assert "ok" in shared


# --- local memories ----------------------------------------------------------


def test_memories_are_returned_newest_first(service: MemoryService) -> None:
    store = service._local_store(service._config())
    store.remember("first")
    store.remember("second")

    contents = [item["content"] for item in service.memories()["memories"]]
    assert contents == ["second", "first"]


def test_memories_can_be_searched(service: MemoryService) -> None:
    store = service._local_store(service._config())
    store.remember("the user prefers tabs")
    store.remember("deploys happen on Fridays")

    found = service.memories("tabs")
    assert [item["content"] for item in found["memories"]] == ["the user prefers tabs"]


def test_local_memories_can_be_created_updated_and_deleted(service: MemoryService) -> None:
    created = service.create_memory("Prefer concise answers", "preference")
    memory_id = created["memory_id"]

    updated = service.update_memory(memory_id, "Prefer concise, sourced answers", "preference")
    assert updated["content"] == "Prefer concise, sourced answers"
    assert service.memories()["memories"][0]["scope"] == "preference"

    assert service.delete_memory(memory_id)["removed"] is True
    assert service.memories()["memories"] == []


def test_local_memory_mutations_reject_missing_records(service: MemoryService) -> None:
    with pytest.raises(ValueError, match="was not found"):
        service.update_memory("missing", "value")
    with pytest.raises(ValueError, match="was not found"):
        service.delete_memory("missing")


def test_memories_are_bounded_and_say_when_they_were_cut(service: MemoryService) -> None:
    """A store grows without limit; a browser is shown a window of it."""
    store = service._local_store(service._config())
    for index in range(12):
        store.remember(f"memory {index}")

    payload = service.memories(limit=5)
    assert len(payload["memories"]) == 5
    assert payload["total"] == 12
    assert payload["truncated"] is True


def test_an_absurd_limit_is_clamped(service: MemoryService) -> None:
    from loro.webui.memory import MAX_MEMORIES

    store = service._local_store(service._config())
    store.remember("one")
    assert service.memories(limit=10_000)["ok"] is True
    assert MAX_MEMORIES < 10_000


# --- the proposal queue ------------------------------------------------------


def test_proposals_report_whether_they_can_still_be_decided(service: MemoryService) -> None:
    """The UI should not offer buttons that would be refused."""
    proposal = _propose(service, "remember this")
    assert service.proposals()["proposals"][0]["decidable"] is True

    service.reject(proposal.proposal_id)
    assert service.proposals()["proposals"][0]["decidable"] is False


def test_accepting_a_local_proposal_writes_a_memory(service: MemoryService) -> None:
    proposal = _propose(service, "The user prefers tabs.")

    result = service.accept(proposal.proposal_id)
    assert result["status"] == "accepted"
    assert result["memory_id"]
    assert [item["content"] for item in service.memories()["memories"]] == [
        "The user prefers tabs."
    ]


def test_rejecting_writes_nothing_to_memory(service: MemoryService) -> None:
    """There was no way to decline a proposal anywhere: the CLI only accepts,
    so the queue could only grow."""
    proposal = _propose(service, "something not worth keeping")

    assert service.reject(proposal.proposal_id, reason="not durable")["status"] == "rejected"
    assert service.memories()["memories"] == []
    assert service.overview()["proposals"]["pending"] == 0


def test_deciding_twice_is_refused_by_name(service: MemoryService) -> None:
    """Deciding twice is a race, not a fault; the message says which."""
    proposal = _propose(service, "remember this")
    service.accept(proposal.proposal_id)

    with pytest.raises(ValueError, match="already accepted"):
        service.accept(proposal.proposal_id)
    with pytest.raises(ValueError, match="already accepted"):
        service.reject(proposal.proposal_id)


def test_an_unknown_proposal_is_refused(service: MemoryService) -> None:
    with pytest.raises(ValueError, match="Unknown memory proposal"):
        service.accept("nonesuch")


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_missing_proposal_id_is_refused(service: MemoryService, blank: str) -> None:
    with pytest.raises(ValueError, match="proposal id is required"):
        service.reject(blank)


def test_a_shared_proposal_is_staged_as_a_draft_not_committed(service: MemoryService) -> None:
    """Committing to a governed backend is a separate, deliberate step."""
    proposal = _propose(service, "The org standard is UTC.", target="shared")

    result = service.accept(proposal.proposal_id)
    assert result["status"] == "accepted_as_shared_draft"
    assert result["draft_id"]
    assert "commit" in result["note"]
    # It must not have become a local memory by accident.
    assert service.memories()["memories"] == []


def test_proposals_can_be_filtered_by_status(service: MemoryService) -> None:
    kept = _propose(service, "keep me")
    dropped = _propose(service, "drop me")
    service.accept(kept.proposal_id)
    service.reject(dropped.proposal_id)

    assert len(service.proposals("accepted")["proposals"]) == 1
    assert len(service.proposals("rejected")["proposals"]) == 1
    assert len(service.proposals()["proposals"]) == 2


# --- shared memory -----------------------------------------------------------


def test_an_empty_shared_query_searches_nothing(service: MemoryService) -> None:
    payload = service.shared_search("   ")
    assert payload["records"] == []


def test_a_shared_search_against_a_missing_backend_reports_instead_of_raising(
    service: MemoryService,
) -> None:
    payload = service.shared_search("anything")
    assert "ok" in payload
    assert payload["query"] == "anything"
