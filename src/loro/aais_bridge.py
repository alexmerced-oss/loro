"""Durable AAIS authority/presenter bridge for Loro's web and stdio surfaces."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aais import ApprovalStore, ConflictError, create_decision, create_request, validate

from loro.approvals import ApprovalError, ApprovalRequest, ApprovalScope, JsonApprovalStore

Envelope = dict[str, Any]
Publisher = Callable[[str, Mapping[str, Any]], None]


def _time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _identifier(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._:-]", ".", value.strip())[:200]
    return cleaned if cleaned and cleaned[0].isalnum() else fallback


@dataclass
class _Waiter:
    envelope: Envelope
    resolved: threading.Event
    scope: ApprovalScope | None = None
    resolution: Envelope | None = None


class AAISBridge:
    """One authority stream shared by chat, subagents, graphs, and tools."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.path = self.project_root / ".loro" / "aais-pending.json"
        self._lock = threading.RLock()
        self._disk = JsonApprovalStore(self.path)
        self._waiters: dict[str, _Waiter] = {}
        self._publishers: dict[str, Publisher] = {}

    @staticmethod
    def _empty() -> Envelope:
        return {
            "schema": "loro.aais-store.v1",
            "sequence": 0,
            "presenter_sequence": 0,
            "pending": {},
            "decisions": {},
            "resolutions": {},
            "events": [],
        }

    def _read(self) -> Envelope:
        if not self.path.exists():
            return self._empty()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ApprovalError(f"Approval state requires recovery: {self.path}") from error
        expected = self._empty()
        if (
            not isinstance(value, dict)
            or any(
                key not in value or not isinstance(value[key], type(default))
                for key, default in expected.items()
            )
            or value["schema"] != expected["schema"]
        ):
            raise ApprovalError(f"Invalid approval state; preserve and recover {self.path}")
        return value

    def _write(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            with suppress(OSError):
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _next(state: Envelope, key: str = "sequence") -> int:
        state[key] = int(state.get(key, 0)) + 1
        return int(state[key])

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
        choices: list[Envelope] = [{"decision": "approve", "scope": "once", "label": "Allow once"}]
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
        with self._lock, self._disk._locked():
            state = self._read()
            now = datetime.now(UTC)
            envelope = create_request(
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
                sequence=self._next(state),
                stream="loro.approvals",
                request_id=request.request_id,
                created_at=_time(now),
                expires_at=_time(now + timedelta(seconds=timeout)),
            )
            state["pending"][request.request_id] = envelope
            state.setdefault("owners", {})[request.request_id] = os.getpid()
            state["events"].append(envelope)
            state["events"] = state["events"][-1000:]
            self._write(state)
            waiter = _Waiter(envelope, threading.Event())
            self._waiters[request.request_id] = waiter
            self._publishers[request.request_id] = publish
        publish("approval.requested", envelope)
        deadline = time.monotonic() + timeout
        try:
            while not waiter.resolved.wait(timeout=0.1):
                with self._lock, self._disk._locked():
                    resolution = self._read()["resolutions"].get(request.request_id)
                if resolution:
                    waiter.resolution = resolution
                    body = resolution["resolution"]
                    waiter.scope = (
                        body.get("effective_scope") if body["outcome"] == "approved" else None
                    )
                    publish("approval.resolved", resolution)
                    break
                if cancelled.is_set() or time.monotonic() >= deadline:
                    with suppress(ConflictError):
                        if cancelled.is_set():
                            self.cancel(request.request_id)
                        else:
                            self.deny(request.request_id, actor_id="loro.timeout")
            return None if cancelled.is_set() else waiter.scope
        finally:
            with self._lock:
                self._waiters.pop(request.request_id, None)
                self._publishers.pop(request.request_id, None)

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
        publisher: Publisher | None
        waiter: _Waiter | None
        with self._lock, self._disk._locked():
            state = self._read()
            prior = state["resolutions"].get(request_id)
            previous = state["decisions"].get(request_id)
            if prior:
                if previous and (
                    previous["decision"]["decision"],
                    previous["decision"]["scope"],
                ) == (decision, scope):
                    return copy.deepcopy(prior)
                raise ConflictError(f"request {request_id} was already resolved")
            pending = state["pending"].get(request_id)
            if not pending:
                raise ValueError(f"unknown pending approval: {request_id}")
            if (
                reviewed_digest is not None
                and reviewed_digest != pending["request"]["action_digest"]
            ):
                raise ConflictError("Decision digest does not match the reviewed action")
            owner = state.get("owners", {}).get(request_id)
            if decision == "approve" and owner is not None and not self._owner_alive(owner):
                raise ConflictError(
                    "The issuing process stopped; inspect recovery before starting new work"
                )
            decided = create_decision(
                pending,
                decision=decision,
                scope=scope,
                actor={
                    "id": _identifier(actor_id, "local-user"),
                    "type": "human" if not actor_id.startswith("loro.") else "policy",
                    "authenticated_by": (
                        "loro-web-session" if not actor_id.startswith("loro.") else "authority"
                    ),
                },
                sequence=self._next(state, "presenter_sequence"),
                stream="loro.presenter",
                decision_id=decision_id,
            )
            machine = ApprovalStore()
            machine.add(pending)
            resolution = machine.decide(
                decided,
                current_action=pending["request"]["action"],
                sequence=self._next(state),
            )
            state["pending"].pop(request_id, None)
            state["decisions"][request_id] = decided
            state["resolutions"][request_id] = resolution
            state["events"].append(resolution)
            state["events"] = state["events"][-1000:]
            self._write(state)
            waiter = self._waiters.get(request_id)
            publisher = self._publishers.get(request_id)
            if waiter:
                waiter.resolution = resolution
                waiter.scope = scope if resolution["resolution"]["outcome"] == "approved" else None
        if publisher:
            publisher("approval.resolved", resolution)
        if waiter:
            waiter.resolved.set()
        return copy.deepcopy(resolution)

    def deny(self, request_id: str, *, actor_id: str = "loro.policy") -> Envelope:
        return self.decide(request_id, decision="deny", scope="once", actor_id=actor_id)

    def cancel(self, request_id: str) -> Envelope:
        return self.decide(request_id, decision="cancel", scope="once", actor_id="loro.cancel")

    def cancel_active(self) -> int:
        """Cancel only requests owned by this live bridge instance."""

        with self._lock:
            request_ids = list(self._waiters)
        for request_id in request_ids:
            try:
                self.cancel(request_id)
            except (ConflictError, ValueError):
                pass
        return len(request_ids)

    @staticmethod
    def _owner_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def recovery(self) -> Envelope:
        with self._lock, self._disk._locked():
            state = self._read()
            owners = state.get("owners", {})
            return {
                "orphaned": [
                    key
                    for key in state["pending"]
                    if key in owners and not self._owner_alive(owners[key])
                ],
                "unknown_owner": [key for key in state["pending"] if key not in owners],
                "receipts": list(state["resolutions"].values())[-100:],
                "guidance": (
                    "Stopped owners are not restarted. "
                    "Inspect completed effects before creating a new run."
                ),
            }

    def snapshot(self) -> Envelope:
        with self._lock, self._disk._locked():
            state = self._read()
            machine = ApprovalStore(last_sequence=int(state.get("sequence", 0)))
            for key, envelope in state["pending"].items():
                owner = state.get("owners", {}).get(key)
                if owner is None or self._owner_alive(owner):
                    machine.add(validate(envelope))
            return machine.snapshot(stream="loro.approvals")

    def events_after(self, sequence: int) -> list[Envelope]:
        with self._lock, self._disk._locked():
            return [
                copy.deepcopy(item)
                for item in self._read()["events"]
                if int(item.get("sequence", 0)) > sequence
            ]
