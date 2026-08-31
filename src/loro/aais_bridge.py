"""Durable AAIS authority/presenter bridge for Loro's web and stdio surfaces."""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aais import ApprovalStore, ConflictError, create_decision, create_request, validate

from loro.approvals import ApprovalRequest, ApprovalScope

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
        except (OSError, json.JSONDecodeError):
            return self._empty()
        return value if isinstance(value, dict) else self._empty()

    def _write(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
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
        with self._lock:
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
            state["events"].append(envelope)
            state["events"] = state["events"][-1000:]
            self._write(state)
            waiter = _Waiter(envelope, threading.Event())
            self._waiters[request.request_id] = waiter
            self._publishers[request.request_id] = publish
        publish("approval.requested", envelope)
        while not waiter.resolved.wait(timeout=0.25):
            if cancelled.is_set():
                self.cancel(request.request_id)
                break
            timeout -= 0.25
            if timeout <= 0:
                self.deny(request.request_id, actor_id="loro.timeout")
                break
        with self._lock:
            scope = waiter.scope
            self._waiters.pop(request.request_id, None)
            self._publishers.pop(request.request_id, None)
        return scope

    def decide(
        self,
        request_id: str,
        *,
        decision: str,
        scope: str,
        actor_id: str,
        decision_id: str | None = None,
    ) -> Envelope:
        publisher: Publisher | None
        waiter: _Waiter | None
        with self._lock:
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

    def snapshot(self) -> Envelope:
        with self._lock:
            state = self._read()
            machine = ApprovalStore(last_sequence=int(state.get("sequence", 0)))
            for envelope in state["pending"].values():
                machine.add(validate(envelope))
            return machine.snapshot(stream="loro.approvals")

    def events_after(self, sequence: int) -> list[Envelope]:
        with self._lock:
            return [
                copy.deepcopy(item)
                for item in self._read()["events"]
                if int(item.get("sequence", 0)) > sequence
            ]
