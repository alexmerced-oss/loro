from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from loro.agent_profiles.models import AgentStateDelta
from loro.config import AgentProfilesConfig, SafetyConfig
from loro.data_protection import DataProtectionEngine
from loro.fileio import atomic_write_text, file_lock


@dataclass(frozen=True)
class ProfileProposal:
    proposal_id: str
    kind: str
    status: str
    profile: str
    spec_digest: str
    delta: AgentStateDelta | None
    capability: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "status": self.status,
            "profile": self.profile,
            "spec_digest": self.spec_digest,
            "delta": self.delta.model_dump(mode="json") if self.delta else None,
            "capability": self.capability,
        }


class ProfileProposalStore:
    def __init__(self, config: AgentProfilesConfig, safety: SafetyConfig) -> None:
        self.root = Path(config.proposal_path).expanduser()
        self.protection = DataProtectionEngine(safety)

    def create_state(self, delta: AgentStateDelta) -> ProfileProposal:
        payload = delta.model_dump(mode="json")
        for operation in payload["operations"]:
            value = operation.get("value")
            if isinstance(value, dict) and "content" in value:
                value["content"] = self.protection.enforce(
                    str(value["content"]), "agent_profile"
                ).content
        proposal = ProfileProposal(
            proposal_id=str(uuid4()),
            kind="state-delta",
            status="pending",
            profile=delta.profile,
            spec_digest=delta.spec_digest,
            delta=AgentStateDelta.model_validate(payload),
        )
        self._write(proposal)
        return proposal

    def list(self) -> list[ProfileProposal]:
        if not self.root.exists():
            return []
        return [self.get(path.stem) for path in sorted(self.root.glob("*.json"))]

    def get(self, proposal_id: str) -> ProfileProposal:
        try:
            proposal_id = str(UUID(proposal_id))
        except ValueError as error:
            raise FileNotFoundError(f"Invalid proposal ID: {proposal_id}") from error
        path = self.root / f"{proposal_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProfileProposal(
            proposal_id=str(payload["proposal_id"]),
            kind=str(payload["kind"]),
            status=str(payload["status"]),
            profile=str(payload["profile"]),
            spec_digest=str(payload["spec_digest"]),
            delta=(
                AgentStateDelta.model_validate(payload["delta"]) if payload.get("delta") else None
            ),
            capability=payload.get("capability"),
        )

    def set_status(self, proposal: ProfileProposal, status: str) -> ProfileProposal:
        updated = ProfileProposal(**{**proposal.__dict__, "status": status})
        self._write(updated)
        return updated

    def _write(self, proposal: ProfileProposal) -> None:
        path = self.root / f"{proposal.proposal_id}.json"
        with file_lock(path):
            atomic_write_text(
                path, json.dumps(proposal.to_payload(), indent=2, sort_keys=True) + "\n"
            )
