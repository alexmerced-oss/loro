"""Filtered queries and compliance summaries over the JSONL audit chain.

The chain already records who did what under which policy; this module turns it into
evidence an auditor can read: filter by actor, tenant, event type, action and time range,
then verify the chain and summarize what it contains.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loro.audit.sinks import verify_jsonl_audit

__all__ = [
    "AuditQuery",
    "AuditReport",
    "audit_report",
    "query_audit_events",
]


@dataclass(frozen=True)
class AuditQuery:
    actor: str | None = None
    tenant: str | None = None
    event_type: str | None = None
    action: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 100

    def matches(self, event: dict[str, Any]) -> bool:
        if self.event_type and not _glob(str(event.get("event_type", "")), self.event_type):
            return False
        if self.actor and _field(event, "actor") != self.actor:
            return False
        if self.tenant and _field(event, "tenant_id") != self.tenant:
            return False
        if self.action and not _glob(_field(event, "action"), self.action):
            return False
        recorded = _timestamp(event)
        if self.since and (recorded is None or recorded < self.since):
            return False
        if self.until and (recorded is None or recorded > self.until):
            return False
        return True


@dataclass(frozen=True)
class AuditReport:
    path: str
    chain_ok: bool
    chain_issue: str | None
    events: int
    matched: int
    event_types: dict[str, int] = field(default_factory=dict)
    actors: dict[str, int] = field(default_factory=dict)
    tenants: dict[str, int] = field(default_factory=dict)
    decisions: dict[str, int] = field(default_factory=dict)
    first_event_at: str | None = None
    last_event_at: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "chain_ok": self.chain_ok,
            "chain_issue": self.chain_issue,
            "events": self.events,
            "matched": self.matched,
            "event_types": self.event_types,
            "actors": self.actors,
            "tenants": self.tenants,
            "decisions": self.decisions,
            "first_event_at": self.first_event_at,
            "last_event_at": self.last_event_at,
        }


def query_audit_events(path: str | Path, query: AuditQuery) -> list[dict[str, Any]]:
    """Newest-first events matching the query, capped at `query.limit`."""

    matched = [event for event in _iter_events(Path(path).expanduser()) if query.matches(event)]
    matched.reverse()
    return matched[: max(0, query.limit)]


def audit_report(path: str | Path, query: AuditQuery | None = None) -> AuditReport:
    """Verify the chain and summarize the matching events for compliance evidence."""

    audit_path = Path(path).expanduser()
    verification = verify_jsonl_audit(audit_path)
    active = query or AuditQuery(limit=0)
    total = 0
    event_types: Counter[str] = Counter()
    actors: Counter[str] = Counter()
    tenants: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    timestamps: list[str] = []
    for event in _iter_events(audit_path):
        total += 1
        if not active.matches(event):
            continue
        event_types[str(event.get("event_type", ""))] += 1
        actors[_field(event, "actor") or "unknown"] += 1
        tenants[_field(event, "tenant_id") or "unknown"] += 1
        decision = _field(event, "decision")
        if decision:
            decisions[decision] += 1
        recorded = event.get("timestamp") or event.get("created_at")
        if isinstance(recorded, str):
            timestamps.append(recorded)
    timestamps.sort()
    return AuditReport(
        path=str(audit_path),
        chain_ok=verification.ok,
        chain_issue=verification.issue,
        events=total,
        matched=sum(event_types.values()),
        event_types=dict(event_types.most_common()),
        actors=dict(actors.most_common()),
        tenants=dict(tenants.most_common()),
        decisions=dict(decisions.most_common()),
        first_event_at=timestamps[0] if timestamps else None,
        last_event_at=timestamps[-1] if timestamps else None,
    )


def _iter_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _field(event: dict[str, Any], name: str) -> str:
    """Read a field from the event envelope, falling back to its details payload."""

    value = event.get(name)
    if value in (None, ""):
        details = event.get("details")
        value = details.get(name) if isinstance(details, dict) else None
    return "" if value in (None, "") else str(value)


def _timestamp(event: dict[str, Any]) -> datetime | None:
    raw = event.get("timestamp") or event.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _glob(value: str, pattern: str) -> bool:
    from fnmatch import fnmatchcase

    return fnmatchcase(value.casefold(), pattern.casefold())
