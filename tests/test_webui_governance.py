"""Governance surfaces for the Web UI.

Loro's reason to exist is evidence, and all of it lived in the CLI. These cover
the read-only adapters: identity and posture, policy explanation, and the audit
chain.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from loro.webui.governance import GovernanceService


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _chained_audit(path: Path, count: int = 5) -> None:
    """Write a small valid hash-chained JSONL ledger."""
    previous = ""
    lines = []
    for index in range(count):
        event: dict[str, Any] = {
            "event_type": "runtime.task_started" if index % 2 else "policy.evaluated",
            "timestamp": f"2026-08-25T10:0{index}:00+00:00",
            "identity": {"actor": "alexmerced", "tenant_id": "default"},
            "previous_hash": previous,
        }
        digest = hashlib.sha256(
            json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        event["hash"] = f"sha256:{digest}"
        previous = event["hash"]
        lines.append(json.dumps(event))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- status ------------------------------------------------------------------


def test_status_reports_every_posture_section(workspace: Path) -> None:
    status = GovernanceService(workspace).status()

    assert status["ok"] is True
    for section in ("identity", "budgets", "sandbox", "approvals", "audit"):
        assert section in status, section


def test_status_resolves_the_audit_path(workspace: Path) -> None:
    """A configured path may carry a literal ~; unexpanded it always looks absent."""
    status = GovernanceService(workspace).status()
    assert not str(status["audit"]["path"]).startswith("~")


def test_status_never_raises_when_identity_is_unconfigured(workspace: Path) -> None:
    status = GovernanceService(workspace).status()
    assert "ok" in status["identity"]


# --- policy ------------------------------------------------------------------


def test_explain_returns_a_decision_and_a_reason(workspace: Path) -> None:
    result = GovernanceService(workspace).explain(
        {
            "tool": "shell",
            "action": "run command",
            "resource": {"kind": "shell", "executable_name": "python"},
        }
    )

    assert result["decision"]
    assert result["reason"]
    # The request is echoed so the UI can show what was asked.
    assert result["request"]["tool"] == "shell"


def test_explain_accepts_a_resource_nested_under_fields(workspace: Path) -> None:
    """The CLI accepts both shapes; the browser should not be stricter."""
    result = GovernanceService(workspace).explain(
        {
            "tool": "shell",
            "action": "run",
            "resource": {"kind": "shell", "fields": {"executable_name": "python"}},
        }
    )
    assert result["decision"]


@pytest.mark.parametrize(
    ("request_payload", "expected"),
    [
        ({}, "tool"),
        ({"tool": "shell"}, "action"),
        ({"tool": "shell", "action": "run", "resource": "not an object"}, "object"),
        ({"tool": "shell", "action": "run", "target": 7}, "target"),
    ],
)
def test_a_malformed_request_is_refused_with_a_reason(
    workspace: Path, request_payload: dict[str, Any], expected: str
) -> None:
    with pytest.raises(ValueError, match=expected):
        GovernanceService(workspace).explain(request_payload)


# --- audit -------------------------------------------------------------------


def test_audit_reports_no_events_before_anything_is_written(workspace: Path, monkeypatch) -> None:
    service = GovernanceService(workspace)
    monkeypatch.setattr(
        service, "_config", lambda: _config_with_audit(workspace / "missing.jsonl")
    )
    payload = service.audit()

    assert payload["ok"] is True
    assert payload["events"] == []
    assert "No audit events" in payload["note"]


def test_audit_lists_events_newest_first_with_the_chain_result(
    workspace: Path, monkeypatch
) -> None:
    ledger = workspace / "audit.jsonl"
    _chained_audit(ledger, count=5)
    service = GovernanceService(workspace)
    monkeypatch.setattr(service, "_config", lambda: _config_with_audit(ledger))

    payload = service.audit(limit=3)

    assert payload["total"] == 5
    assert len(payload["events"]) == 3
    # Newest first, so the most recent event leads.
    assert payload["events"][0]["timestamp"] > payload["events"][-1]["timestamp"]
    assert "ok" in payload["verification"]
    assert payload["event_types"]


def test_audit_filters_by_event_type(workspace: Path, monkeypatch) -> None:
    ledger = workspace / "audit.jsonl"
    _chained_audit(ledger, count=6)
    service = GovernanceService(workspace)
    monkeypatch.setattr(service, "_config", lambda: _config_with_audit(ledger))

    filtered = service.audit(limit=10, event_type="policy.evaluated")
    assert filtered["matched"] < filtered["total"]
    assert all(item["event_type"] == "policy.evaluated" for item in filtered["events"])


def test_audit_never_returns_an_unbounded_window(workspace: Path, monkeypatch) -> None:
    """A ledger grows without bound; the browser gets a window of it."""
    from loro.webui.governance import MAX_EVENTS

    ledger = workspace / "audit.jsonl"
    _chained_audit(ledger, count=40)
    service = GovernanceService(workspace)
    monkeypatch.setattr(service, "_config", lambda: _config_with_audit(ledger))

    assert len(service.audit(limit=10_000)["events"]) <= MAX_EVENTS
    assert len(service.audit(limit=5)["events"]) == 5


def test_a_non_jsonl_sink_explains_itself(workspace: Path, monkeypatch) -> None:
    service = GovernanceService(workspace)
    monkeypatch.setattr(
        service, "_config", lambda: _config_with_audit(workspace / "a.jsonl", sink="http")
    )
    payload = service.audit()

    assert payload["ok"] is False
    assert "JSONL" in payload["error"]
    assert service.verify()["ok"] is False


def _config_with_audit(path: Path, sink: str = "jsonl") -> Any:
    from types import SimpleNamespace

    from loro.config import load_config

    config = load_config(path.parent)
    return SimpleNamespace(
        identity=config.identity,
        runtime=config.runtime,
        sandbox=getattr(config, "sandbox", None),
        approvals=config.approvals,
        permissions=config.permissions,
        audit=SimpleNamespace(sink=sink, path=path),
    )
