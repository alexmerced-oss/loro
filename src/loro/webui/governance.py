"""Governance surfaces for the local Web UI.

Loro's reason to exist is evidence: who ran this, under whose identity, with
whose approval, against which policy, and can you prove the record has not been
edited. All of that was reachable only from the CLI, so a screenshot of
`loro web` looked like any other chat app.

Everything here is read-only. Verification recomputes the hash chain, policy
explanation evaluates a hypothetical request without performing it, and the
status view reports resolved identity, budgets, and sandbox posture. Nothing in
this module can grant authority or mutate state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loro.config import load_config

# A browser should never be handed an unbounded audit file.
MAX_EVENTS = 500


def _safe(value: Any) -> Any:
    """Make a dataclass or mapping JSON-friendly."""
    if hasattr(value, "__dict__"):
        return {key: _safe(item) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _tail_events(path: Path, query: Any, limit: int) -> list[dict[str, Any]]:
    """The most recent matching events, newest first, bounded.

    Read line by line rather than loading the file: an audit ledger grows
    without bound and the browser only ever shows a window of it.
    """
    from loro.audit.query import _iter_events

    matched = [event for event in _iter_events(path) if query.matches(event)]
    return list(reversed(matched[-limit:]))


class GovernanceService:
    """Read-only views over identity, policy, budgets, and the audit chain."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def _config(self) -> Any:
        return load_config(self.project_root)

    # -- identity, budgets, sandbox -----------------------------------------

    def status(self) -> dict[str, Any]:
        """Who am I, what may I spend, and is the sandbox actually on."""
        config = self._config()
        payload: dict[str, Any] = {"ok": True}

        try:
            from loro.identity import diagnose_identity, resolve_identity

            diagnostic = diagnose_identity(config.identity)
            payload["identity"] = {
                "ok": bool(diagnostic.ok),
                "issues": [str(issue) for issue in getattr(diagnostic, "issues", []) or []],
            }
            if diagnostic.ok:
                identity = resolve_identity(config.identity)
                payload["identity"].update(
                    {
                        "actor": getattr(identity, "actor", ""),
                        "tenant_id": getattr(identity, "tenant_id", ""),
                        "roles": list(getattr(identity, "roles", []) or []),
                    }
                )
        except Exception as error:  # noqa: BLE001 - reported, never raised
            payload["identity"] = {"ok": False, "issues": [str(error)]}

        runtime = getattr(config, "runtime", None)
        payload["budgets"] = {
            "max_model_bytes": getattr(runtime, "max_model_bytes", None),
            "max_tokens": getattr(runtime, "max_tokens", None),
            "max_cost_usd": getattr(runtime, "max_cost_usd", None),
            "max_tool_calls": getattr(runtime, "max_tool_calls", None),
        }

        sandbox = getattr(config, "sandbox", None)
        payload["sandbox"] = {
            "profile": getattr(sandbox, "profile", "") or "none",
            "enforced": bool(getattr(sandbox, "bubblewrap", False)),
            "max_runtime_seconds": getattr(sandbox, "max_runtime_seconds", None),
            "max_output_bytes": getattr(sandbox, "max_output_bytes", None),
        }

        approvals = getattr(config, "approvals", None)
        payload["approvals"] = {
            "mode": getattr(approvals, "mode", "") or "ask",
            "allow_session_scope": bool(getattr(approvals, "allow_session_scope", False)),
        }

        audit = getattr(config, "audit", None)
        payload["audit"] = {
            "sink": getattr(audit, "sink", "") or "none",
            "path": str(Path(str(getattr(audit, "path", "") or "")).expanduser()),
        }
        return _safe(payload)

    # -- audit ---------------------------------------------------------------

    def audit(self, *, limit: int = 100, event_type: str = "", actor: str = "") -> dict[str, Any]:
        """Recent audit events plus the chain verification result."""
        config = self._config()
        audit_config = getattr(config, "audit", None)
        sink = getattr(audit_config, "sink", "")
        path = Path(str(getattr(audit_config, "path", "") or "")).expanduser()

        if sink != "jsonl":
            return {
                "ok": False,
                "sink": sink or "none",
                "error": (
                    "Local chain verification requires the JSONL audit sink. "
                    "Run `loro setup audit` to enable it."
                ),
                "events": [],
            }
        if not path.is_file():
            return {
                "ok": True,
                "sink": sink,
                "path": str(path),
                "verification": None,
                "events": [],
                "note": "No audit events have been written yet.",
            }

        from loro.audit.query import AuditQuery, audit_report

        bounded = max(1, min(int(limit or 100), MAX_EVENTS))
        query = AuditQuery(
            limit=bounded,
            event_type=event_type or None,
            actor=actor or None,
        )
        # `audit_report` is a summary: counts plus the chain result. It carries
        # no per-event list, so the rows are read separately and bounded to the
        # tail; a browser should never be handed a 600 KB ledger.
        report = audit_report(path, query)
        summary = report.to_payload() if hasattr(report, "to_payload") else _safe(report)

        rows = _tail_events(path, query, bounded)

        return {
            "ok": True,
            "sink": sink,
            "path": str(path),
            "total": summary.get("events"),
            "matched": summary.get("matched"),
            "event_types": summary.get("event_types") or {},
            "actors": summary.get("actors") or {},
            "decisions": summary.get("decisions") or {},
            "first_event_at": summary.get("first_event_at"),
            "last_event_at": summary.get("last_event_at"),
            "verification": {
                "ok": bool(summary.get("chain_ok")),
                "issue": summary.get("chain_issue"),
            },
            "events": rows,
        }

    def verify(self, anchor: str = "") -> dict[str, Any]:
        """Recompute the hash chain, and check an external anchor when given."""
        config = self._config()
        audit_config = getattr(config, "audit", None)
        if getattr(audit_config, "sink", "") != "jsonl":
            return {
                "ok": False,
                "error": "Local hash verification requires the JSONL audit sink.",
            }

        from loro.audit.sinks import verify_jsonl_audit

        result = verify_jsonl_audit(
            Path(str(audit_config.path)).expanduser(), expected_final_hash=anchor or None
        )
        return _safe(result)

    # -- policy --------------------------------------------------------------

    def explain(self, request: dict[str, Any]) -> dict[str, Any]:
        """Explain how policy would decide a request, without performing it.

        This is `loro policy explain` in the browser: it evaluates the rules and
        reports the decision and the reason. It never runs the tool.
        """
        if not isinstance(request, dict) or not request.get("tool"):
            raise ValueError('A request needs at least a "tool", e.g. {"tool": "shell"}.')

        from loro.permissions import PermissionEngine, PermissionRequest
        from loro.resources import resource_from_payload

        tool = str(request.get("tool") or "").strip()
        action = str(request.get("action") or "").strip()
        if not action:
            raise ValueError('A request needs an "action", e.g. "run command".')

        # `resource` is a normalized structure, not a free dict, and the CLI
        # accepts either a flat object or one nested under "fields".
        resource = None
        payload = request.get("resource")
        if payload is not None:
            if not isinstance(payload, dict):
                raise ValueError("A policy request resource must be an object.")
            if isinstance(payload.get("fields"), dict):
                payload = {"kind": payload.get("kind"), **payload["fields"]}
            try:
                resource = resource_from_payload(payload)
            except PermissionError as error:
                raise ValueError(str(error)) from error

        target = request.get("target")
        if target is not None and not isinstance(target, str):
            raise ValueError("A policy request target must be a string.")

        config = self._config()
        engine = PermissionEngine(config.permissions)
        result = engine.evaluate(
            PermissionRequest(tool=tool, action=action, target=target, resource=resource)
        )
        payload = _safe(result)
        payload["request"] = request
        return payload
