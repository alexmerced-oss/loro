from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from loro.fileio import atomic_write_text, file_lock


@dataclass(frozen=True)
class MemoryProposal:
    content: str
    target: str = "local"
    rationale: str = ""
    status: str = "proposed"
    proposal_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MemoryProposalStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "memory-proposals.jsonl"

    def propose(self, proposal: MemoryProposal) -> MemoryProposal:
        with file_lock(self.path), self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(_proposal_payload(proposal)) + "\n")
        return proposal

    def list(self) -> list[MemoryProposal]:
        with file_lock(self.path):
            return self._list_unlocked()

    def _list_unlocked(self) -> list[MemoryProposal]:
        if not self.path.exists():
            return []
        proposals: list[MemoryProposal] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            proposals.append(_proposal_from_payload(json.loads(line)))
        return proposals

    def get(self, proposal_id: str) -> MemoryProposal | None:
        for proposal in self.list():
            if proposal.proposal_id == proposal_id:
                return proposal
        return None

    def update_status(self, proposal_id: str, status: str) -> MemoryProposal | None:
        updated: MemoryProposal | None = None
        # Truncating in place lost every proposal if the process died mid-rewrite, and
        # the read-modify-write raced any concurrent propose().
        with file_lock(self.path):
            proposals = self._list_unlocked()
            lines: list[str] = []
            for proposal in proposals:
                if proposal.proposal_id == proposal_id:
                    proposal = MemoryProposal(
                        proposal_id=proposal.proposal_id,
                        content=proposal.content,
                        target=proposal.target,
                        rationale=proposal.rationale,
                        status=status,
                        created_at=proposal.created_at,
                    )
                    updated = proposal
                lines.append(json.dumps(_proposal_payload(proposal)))
            atomic_write_text(self.path, "".join(line + "\n" for line in lines))
        return updated


def _proposal_payload(proposal: MemoryProposal) -> dict[str, str]:
    return {
        "proposal_id": proposal.proposal_id,
        "content": proposal.content,
        "target": proposal.target,
        "rationale": proposal.rationale,
        "status": proposal.status,
        "created_at": proposal.created_at.isoformat(),
    }


def _proposal_from_payload(data: dict[str, str]) -> MemoryProposal:
    return MemoryProposal(
        proposal_id=data["proposal_id"],
        content=data["content"],
        target=data.get("target", "local"),
        rationale=data.get("rationale", ""),
        status=data.get("status", "proposed"),
        created_at=datetime.fromisoformat(data["created_at"]),
    )
