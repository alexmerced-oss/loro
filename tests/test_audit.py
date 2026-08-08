import json
from pathlib import Path

from loro.audit import AuditLogger
from loro.config import AuditConfig
from loro.identity import IdentityContext


def test_audit_logger_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(AuditConfig(path=str(path)))
    event = logger.write("test.event", answer=42)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_id"] == event.event_id
    assert payload["event_type"] == "test.event"
    assert payload["details"]["answer"] == 42


def test_audit_logger_adds_identity_context(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    identity = IdentityContext(
        subject="user-123",
        display_name="Alex",
        organization="acme",
        tenant="platform",
        groups=("engineering",),
        roles=("developer",),
        auth_method="oidc",
        session_id="session-456",
        source="environment",
    )
    AuditLogger(AuditConfig(path=str(path)), identity).write("tool.executed", ok=True)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["details"]["actor"] == "user-123"
    assert payload["details"]["tenant_id"] == "platform"
    assert payload["details"]["identity"]["session_id"] == "session-456"
    assert payload["details"]["identity"]["roles"] == ["developer"]
