"""Loro's AAIS authority/presenter bridge for its web and stdio surfaces.

Persistence, locking, owner liveness, retention and corruption handling come from
``aais.store.FileApprovalStore`` (agent-approval-interchange 0.2). This module maps Loro's
``ApprovalRequest`` onto AAIS envelopes, wakes the waiting run, and publishes events to the
presenter that asked.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aais import ConflictError
from aais.store import (
    FileApprovalStore,
    OwnerStoppedError,
    RecoveryRequired,
    RetentionPolicy,
    StoreError,
    UnknownRequestError,
)

from loro.approvals import ApprovalError, ApprovalRequest, ApprovalScope

Envelope = dict[str, Any]
Publisher = Callable[[str, Mapping[str, Any]], None]

STATE_FILE = "aais-approvals.json"
LEGACY_STATE_FILE = "aais-pending.json"
LOCK_TIMEOUT_SECONDS = 60.0


def _identifier(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._:-]", ".", value.strip())[:200]
    return cleaned if cleaned and cleaned[0].isalnum() else fallback


@contextmanager
def _store_errors() -> Iterator[None]:
    """Report store failures as Loro approval errors that say what to do next."""

    try:
        yield
    except RecoveryRequired as error:
        where = (
            f" The damaged file was moved to {error.quarantined_to}."
            if error.quarantined_to
            else ""
        )
        raise ApprovalError(
            f"Approval state requires recovery: {error.path}.{where} Inspect it, then run "
            "`loro approvals recovery --acknowledge`."
        ) from error
    except StoreError as error:
        raise ApprovalError(f"Approval store error: {error}") from error


class AAISBridge:
    """One authority stream shared by chat, subagents, graphs, and tools."""

    def __init__(self, project_root: Path, *, retention: RetentionPolicy | None = None) -> None:
        self.project_root = project_root.resolve()
        self.path = self.project_root / ".loro" / STATE_FILE
        self.legacy_path = self.project_root / ".loro" / LEGACY_STATE_FILE
        self.store = FileApprovalStore(
            self.path,
            stream="loro.approvals",
            presenter_stream="loro.presenter",
            retention=retention,
            # Every transaction fsyncs; on a busy disk a 10-second default can expire while
            # several processes queue for the lock.
            lock_timeout=LOCK_TIMEOUT_SECONDS,
        )
        self._lock = threading.RLock()
        self._active: dict[str, threading.Event] = {}
        self._migrate_legacy_state()

    def _migrate_legacy_state(self) -> None:
        """Import a pre-0.22 ``aais-pending.json`` once, then set it aside."""

        if not self.legacy_path.exists() or self.path.exists():
            return
        try:
            legacy = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApprovalError(
                f"Legacy approval state requires recovery: {self.legacy_path}. It was left in "
                "place; repair or remove it, then retry."
            ) from error
        if not isinstance(legacy, dict) or legacy.get("schema") != "loro.aais-store.v1":
            raise ApprovalError(f"Unrecognized legacy approval state: {self.legacy_path}")
        try:
            self.store.import_legacy_state(legacy)
        except StoreError:
            if not self.path.exists():  # another process migrated first; anything else is real
                raise
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        try:
            self.legacy_path.rename(
                self.legacy_path.with_name(f"{LEGACY_STATE_FILE}.migrated-{stamp}")
            )
        except FileNotFoundError:
            pass

    def request(
        self,
        request: ApprovalRequest,
        *,
        origin: Mapping[str, Any],
        publish: Publisher,
        allow_session: bool,
        cancelled: threading.Event,
        timeout: float = 1800,
    ) -> ApprovalScope | None:
        action = {
            "kind": "tool.call",
            "name": _identifier(request.action, "loro.protected_action"),
            "summary": f"{request.action} on {request.target}",
            "arguments": copy.deepcopy(request.arguments),
            "resource": request.target,
            "working_directory": str(self.project_root),
            "effects": [request.risk_reason or "Performs an action guarded by Loro policy."],
        }
        choices: list[Mapping[str, Any]] = [
            {"decision": "approve", "scope": "once", "label": "Allow once"}
        ]
        if allow_session:
            choices.append(
                {
                    "decision": "approve",
                    "scope": "session",
                    "label": "Allow this exact action for this session",
                    "scope_constraints": {"request_fingerprint": request.fingerprint},
                }
            )
        choices.append({"decision": "deny", "scope": "once", "label": "Deny"})
        with _store_errors():
            envelope = self.store.add_request(
                action=action,
                origin={
                    "harness": "loro",
                    "project": str(self.project_root),
                    "session_id": _identifier(request.identity_session_id, "loro-session"),
                    **dict(origin),
                },
                risk={
                    "level": "high" if request.policy_decision == "deny" else "medium",
                    "reasons": [
                        request.risk_reason or request.policy_reason or "Protected action."
                    ],
                },
                choices=choices,
                request_id=request.request_id,
                ttl=timeout,
            )
        request_id = request.request_id
        stop = threading.Event()
        with self._lock:
            self._active[request_id] = stop
        publish("approval.requested", envelope)
        deadline = time.monotonic() + timeout
        resolution: Envelope | None = None
        try:
            while resolution is None:
                remaining = deadline - time.monotonic()
                with _store_errors():
                    resolution = self.store.wait_for_resolution(
                        request_id, timeout=max(0.0, min(remaining, 0.5)), cancelled=stop
                    )
                if resolution is not None:
                    break
                if cancelled.is_set() or stop.is_set():
                    resolution = self._withdraw(request_id, self.store.cancel, "loro.cancel")
                elif time.monotonic() >= deadline:
                    resolution = self._withdraw(request_id, self.store.deny, "loro.timeout")
        finally:
            with self._lock:
                self._active.pop(request_id, None)
        publish("approval.resolved", resolution)
        if cancelled.is_set():
            return None
        body = resolution["resolution"]
        return body.get("effective_scope") if body["outcome"] == "approved" else None

    def _withdraw(
        self, request_id: str, action: Callable[..., Envelope], actor_id: str
    ) -> Envelope:
        """Cancel or time out a request; if a decision landed first, keep that decision."""

        with _store_errors():
            try:
                return action(request_id, actor_id=actor_id)
            except ConflictError:
                resolved = self.store.get_resolution(request_id)
                if resolved is None:
                    raise
                return resolved

    def decide(
        self,
        request_id: str,
        *,
        decision: str,
        scope: str,
        actor_id: str,
        decision_id: str | None = None,
        reviewed_digest: str | None = None,
    ) -> Envelope:
        actor = {
            "id": _identifier(actor_id, "local-user"),
            "type": "human" if not actor_id.startswith("loro.") else "policy",
            "authenticated_by": (
                "loro-web-session" if not actor_id.startswith("loro.") else "authority"
            ),
        }
        with _store_errors():
            try:
                return self.store.decide(
                    request_id,
                    decision=decision,
                    scope=scope,
                    actor=actor,
                    decision_id=decision_id,
                    reviewed_digest=reviewed_digest,
                )
            except OwnerStoppedError as error:
                raise ConflictError(
                    "The issuing process stopped; inspect recovery before starting new work"
                ) from error
            except UnknownRequestError as error:
                raise ValueError(str(error)) from error

    def deny(self, request_id: str, *, actor_id: str = "loro.policy") -> Envelope:
        return self.decide(request_id, decision="deny", scope="once", actor_id=actor_id)

    def cancel(self, request_id: str) -> Envelope:
        with _store_errors():
            return self.store.cancel(request_id, actor_id="loro.cancel")

    def cancel_active(self) -> int:
        """Withdraw only the requests this bridge instance is waiting on."""

        with self._lock:
            waiting = list(self._active.items())
        for _request_id, stop in waiting:
            stop.set()
        return len(waiting)

    def recovery(self) -> Envelope:
        with _store_errors():
            report = self.store.recovery().to_dict()
        report["guidance"] = (
            "Stopped owners are not restarted. Inspect completed effects before creating a new "
            "run; `loro approvals recovery --cancel-orphaned` withdraws orphaned requests."
        )
        return report

    def recovery_status(self) -> dict[str, Any] | None:
        problem = self.store.recovery_status()
        return problem.to_dict() if problem is not None else None

    def acknowledge_recovery(self) -> None:
        self.store.acknowledge_recovery()

    def cancel_orphaned(self) -> list[Envelope]:
        with _store_errors():
            return self.store.cancel_orphaned(actor_id="loro.recovery")

    def snapshot(self) -> Envelope:
        with _store_errors():
            return self.store.snapshot()

    def events_after(self, sequence: int) -> dict[str, Any]:
        """Events after ``sequence`` plus a ``gap`` flag; on a gap, resync from a snapshot."""

        with _store_errors():
            return self.store.events_after(sequence).to_dict()

    def receipts(self, request_ids: Iterable[str]) -> dict[str, Envelope]:
        """Read-only AAIS envelopes for the given request ids, for evidence export.

        Each entry holds whichever of the request, decision and resolution envelopes the
        store still has. Requests are found in the pending map or the retained event log.
        """

        wanted = sorted({str(item) for item in request_ids})
        if not wanted or not self.path.exists():
            return {}
        with _store_errors():
            requests = {
                str(item["request"].get("id")): item
                for item in self.store.events_after(0).events
                if isinstance(item.get("request"), dict)
            }
            found: dict[str, Envelope] = {}
            for request_id in wanted:
                entry: Envelope = {}
                request = self.store.get_pending(request_id) or requests.get(request_id)
                if request is not None:
                    entry["request"] = copy.deepcopy(request)
                decision = self.store.get_decision(request_id)
                if decision is not None:
                    entry["decision"] = decision
                resolution = self.store.get_resolution(request_id)
                if resolution is not None:
                    entry["resolution"] = resolution
                if entry:
                    found[request_id] = entry
        return found
