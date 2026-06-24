import json
from pathlib import Path

from loro.audit import AuditLogger
from loro.config import AuditConfig


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
