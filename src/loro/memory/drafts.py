from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loro.memory.base import SharedMemoryDraft


class SharedMemoryDraftStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "shared-memory-drafts.jsonl"

    def stage(self, draft: SharedMemoryDraft) -> SharedMemoryDraft:
        payload = {
            "draft_id": draft.draft_id,
            "tenant_id": draft.tenant_id,
            "scope_type": draft.scope_type,
            "scope_key": draft.scope_key,
            "memory_type": draft.memory_type,
            "content": draft.content,
            "summary": draft.summary,
            "classification": draft.classification,
            "created_by": draft.created_by,
            "status": draft.status,
            "created_at": draft.created_at.isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload) + "\n")
        return draft

    def list(self) -> list[SharedMemoryDraft]:
        if not self.path.exists():
            return []
        drafts: list[SharedMemoryDraft] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            drafts.append(
                SharedMemoryDraft(
                    draft_id=data["draft_id"],
                    tenant_id=data["tenant_id"],
                    scope_type=data["scope_type"],
                    scope_key=data["scope_key"],
                    memory_type=data["memory_type"],
                    content=data["content"],
                    summary=data["summary"],
                    classification=data["classification"],
                    created_by=data["created_by"],
                    status=data["status"],
                    created_at=datetime.fromisoformat(data["created_at"]),
                )
            )
        return drafts

    def get(self, draft_id: str) -> SharedMemoryDraft | None:
        for draft in self.list():
            if draft.draft_id == draft_id:
                return draft
        return None
