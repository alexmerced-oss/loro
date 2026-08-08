import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from loro.config import AuditConfig
from loro.identity import IdentityContext


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    details: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AuditLogger:
    def __init__(self, config: AuditConfig, identity: IdentityContext | None = None) -> None:
        self.config = config
        self.identity = identity
        self.path = Path(config.path).expanduser()

    def write(self, event_type: str, **details: Any) -> AuditEvent:
        if self.identity is not None:
            details.setdefault("actor", self.identity.subject)
            details.setdefault("tenant_id", self.identity.tenant)
            details.setdefault("identity", self.identity.to_payload())
        event = AuditEvent(event_type=event_type, details=details)
        if not self.config.enabled:
            return event
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "created_at": event.created_at.isoformat(),
            "details": event.details,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, default=str) + "\n")
        return event


def prompt_preview(prompt: str, limit: int = 160) -> str:
    normalized = " ".join(prompt.split())
    return normalized[:limit]
