import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


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
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(_proposal_payload(proposal)) + "\n")
        return proposal

    def list(self) -> list[MemoryProposal]:
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
        proposals = self.list()
        updated: MemoryProposal | None = None
        with self.path.open("w", encoding="utf-8") as file:
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
                file.write(json.dumps(_proposal_payload(proposal)) + "\n")
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
