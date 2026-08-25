"""Memory surfaces for the local Web UI.

Loro keeps three related things: local memories it has written for this
workspace, a queue of proposals the agent has raised for a human to decide,
and governed shared memory behind a Postgres or Iceberg backend. None of it was
reachable from the browser, so the memory shaping every reply was invisible and
the proposal queue could only be reviewed from a terminal.

Reads are unrestricted. The one mutation is deciding a proposal, which is the
whole point of a queue, and every decision is written to the audit record the
same way `loro memory accept-proposal` writes it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loro.config import load_config

# A browser is shown a window, never a whole store.
MAX_MEMORIES = 200
MAX_EXCERPT = 400

# Same defaults `loro memory accept-proposal` uses, so accepting here and
# accepting there put a shared draft in the same place.
DEFAULT_SCOPE_TYPE = "org"
DEFAULT_SCOPE_KEY = "default"


class MemoryService:
    """Local memories, the proposal queue, and governed shared memory."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def _config(self) -> Any:
        return load_config(self.project_root)

    def _local_store(self, config: Any) -> Any:
        from loro.memory.local import LocalMemoryStore

        return LocalMemoryStore.from_config(config.memory.local, config.safety)

    def _proposal_store(self, config: Any) -> Any:
        from loro.memory.proposals import MemoryProposalStore

        return MemoryProposalStore(Path(config.memory.local.path))

    # -- overview -------------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        """What exists, what is waiting on a decision, and where shared lives."""
        config = self._config()
        try:
            memories = self._local_store(config).list()
        except Exception as error:  # noqa: BLE001 - reported, never raised
            return {"ok": False, "error": str(error)}

        proposals = self._proposal_store(config).list()
        pending = [item for item in proposals if item.status == "proposed"]

        return {
            "ok": True,
            "local": {
                "path": str(Path(config.memory.local.path).expanduser()),
                "count": len(memories),
                "scopes": sorted({record.scope for record in memories}),
            },
            "proposals": {
                "total": len(proposals),
                "pending": len(pending),
                # Every status the queue has ever held, so a filter can be built
                # from what is there rather than from a guessed list.
                "statuses": sorted({item.status for item in proposals}),
            },
            "shared": self.shared_status(),
        }

    # -- local memories -------------------------------------------------------

    def memories(self, query: str = "", limit: int = 50) -> dict[str, Any]:
        """Local memories, newest first, optionally filtered."""
        config = self._config()
        store = self._local_store(config)
        query = (query or "").strip()

        try:
            records = store.search(query) if query else store.list()
        except Exception as error:  # noqa: BLE001 - reported, never raised
            return {"ok": False, "error": str(error), "memories": []}

        bounded = max(1, min(int(limit or 50), MAX_MEMORIES))
        ordered = sorted(records, key=lambda record: record.created_at, reverse=True)
        return {
            "ok": True,
            "query": query,
            "total": len(records),
            "truncated": len(records) > bounded,
            "memories": [
                {
                    "memory_id": record.memory_id,
                    "content": record.content,
                    "scope": record.scope,
                    "created_at": record.created_at.isoformat(),
                }
                for record in ordered[:bounded]
            ],
        }

    # -- proposals ------------------------------------------------------------

    def proposals(self, status: str = "") -> dict[str, Any]:
        """The review queue: what the agent wants remembered, and why."""
        config = self._config()
        items = self._proposal_store(config).list()
        status = (status or "").strip()
        if status:
            items = [item for item in items if item.status == status]

        return {
            "ok": True,
            "status": status,
            "proposals": [
                {
                    "proposal_id": item.proposal_id,
                    "content": item.content,
                    "target": item.target,
                    "rationale": item.rationale,
                    "status": item.status,
                    "created_at": item.created_at.isoformat(),
                    # A decided proposal is history; only a pending one is
                    # actionable, and the UI should not offer buttons that
                    # would be refused.
                    "decidable": item.status == "proposed",
                }
                for item in sorted(items, key=lambda item: item.created_at, reverse=True)
            ],
        }

    def accept(self, proposal_id: str) -> dict[str, Any]:
        """Accept a proposal, exactly as `loro memory accept-proposal` does.

        A local proposal becomes a local memory. A shared one becomes a staged
        shared draft rather than a committed memory, because committing to a
        governed backend is a separate, deliberate step.
        """
        config = self._config()
        store = self._proposal_store(config)
        proposal = self._pending(store, proposal_id)

        if proposal.target == "shared":
            from loro.memory.operations import create_shared_memory_draft

            identity = self._identity(config)
            draft = create_shared_memory_draft(
                content=proposal.content,
                tenant_id=self._tenant(config, identity),
                scope_type=DEFAULT_SCOPE_TYPE,
                scope_key=DEFAULT_SCOPE_KEY,
                memory_type="fact",
                classification="public-internal",
                created_by=identity,
                retention_days=config.memory.shared.retention_days,
            )
            self._draft_store(config).stage(draft)
            store.update_status(proposal_id, "accepted_as_shared_draft")
            self._audit(
                config,
                "memory.proposal_accepted",
                proposal_id=proposal_id,
                target="shared",
                draft_id=draft.draft_id,
            )
            return {
                "ok": True,
                "status": "accepted_as_shared_draft",
                "draft_id": draft.draft_id,
                "note": (
                    "Staged as a shared-memory draft. Commit it with "
                    "`loro memory commit-shared-draft`."
                ),
            }

        memory = self._local_store(config).remember(proposal.content)
        store.update_status(proposal_id, "accepted")
        self._audit(
            config,
            "memory.proposal_accepted",
            proposal_id=proposal_id,
            target="local",
            memory_id=memory.memory_id,
        )
        return {"ok": True, "status": "accepted", "memory_id": memory.memory_id}

    def reject(self, proposal_id: str, reason: str = "") -> dict[str, Any]:
        """Decline a proposal.

        There was no way to decline one anywhere: the CLI only accepts, so the
        queue could only grow and a proposal you did not want stayed pending
        forever. Nothing is written to memory.
        """
        config = self._config()
        store = self._proposal_store(config)
        self._pending(store, proposal_id)

        store.update_status(proposal_id, "rejected")
        self._audit(
            config,
            "memory.proposal_rejected",
            proposal_id=proposal_id,
            reason=str(reason)[:500],
        )
        return {"ok": True, "status": "rejected"}

    def _pending(self, store: Any, proposal_id: str) -> Any:
        proposal_id = (proposal_id or "").strip()
        if not proposal_id:
            raise ValueError("A proposal id is required.")
        proposal = store.get(proposal_id)
        if proposal is None:
            raise ValueError(f"Unknown memory proposal: {proposal_id}")
        if proposal.status != "proposed":
            # Deciding twice is a race, not a fault; say which so the UI can
            # just refresh rather than show an error.
            raise ValueError(f"That proposal was already {proposal.status}.")
        return proposal

    # -- shared memory --------------------------------------------------------

    def shared_status(self) -> dict[str, Any]:
        """Which backend shared memory uses, and whether it answers."""
        config = self._config()
        try:
            from loro.memory.operations import check_shared_memory_backend

            check = check_shared_memory_backend(config.memory.shared)
            return {
                "backend": check.backend,
                "ok": bool(check.ok),
                "messages": [str(message) for message in getattr(check, "messages", [])][:10],
            }
        except Exception as error:  # noqa: BLE001 - advisory only
            return {"backend": config.memory.shared.backend, "ok": False, "messages": [str(error)]}

    def shared_search(self, query: str, *, tenant_id: str = "", limit: int = 20) -> dict[str, Any]:
        """Search governed shared memory, with the citation each record carries."""
        query = (query or "").strip()
        if not query:
            return {"ok": True, "query": "", "records": []}

        config = self._config()
        try:
            from loro.memory.operations import search_shared_memories

            result = search_shared_memories(
                config,
                query=query,
                tenant_id=tenant_id or self._tenant(config, self._identity(config)),
                limit=max(1, min(int(limit or 20), 100)),
            )
        except Exception as error:  # noqa: BLE001 - reported, never raised
            return {"ok": False, "error": str(error), "query": query, "records": []}

        return {
            "ok": True,
            "query": query,
            "backend": result.backend,
            "tenant_id": result.tenant_id,
            "executed": bool(result.executed),
            "messages": [str(message) for message in result.messages][:10],
            "records": [
                {
                    "memory_id": record.memory_id,
                    "content": record.content[:MAX_EXCERPT],
                    "summary": record.summary,
                    "classification": record.classification,
                    "created_by": record.created_by,
                    "created_at": record.created_at,
                    "status": record.status,
                    # The citation is how a shared memory is referred to
                    # elsewhere; showing it is most of the point.
                    "citation": record.citation,
                }
                for record in result.records
            ],
        }

    # -- shared helpers -------------------------------------------------------

    def _identity(self, config: Any) -> str:
        try:
            from loro.identity import resolve_identity

            resolved = resolve_identity(config.identity)
            return str(getattr(resolved, "subject", "") or getattr(resolved, "actor", "") or "")
        except Exception:  # noqa: BLE001 - a missing identity is not fatal here
            return ""

    def _tenant(self, config: Any, identity: str) -> str:
        configured = str(getattr(config.memory.shared, "tenant_id", "") or "")
        return configured or identity or "default"

    def _draft_store(self, config: Any) -> Any:
        from loro.memory.drafts import SharedMemoryDraftStore

        return SharedMemoryDraftStore(Path(config.memory.local.path))

    def _audit(self, config: Any, event: str, **payload: Any) -> None:
        """Record the decision. A memory changing with no trace is the thing
        Loro exists to prevent."""
        try:
            from loro.audit import AuditLogger
            from loro.identity import resolve_identity

            AuditLogger(
                config.audit, resolve_identity(config.identity), safety_config=config.safety
            ).write(event, **payload)
        except Exception:  # noqa: BLE001 - never fail a decision on audit wiring
            pass
