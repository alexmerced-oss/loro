from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loro.audit.inventory import audit_event_family
from loro.audit.sinks import _file_lock

METRICS_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class MetricsSnapshot:
    counters: dict[str, int]
    sums: dict[str, float]
    updated_at: str | None


class OperationalMetrics:
    """Content-free, bounded-cardinality operational metrics derived from audit events."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def observe(
        self,
        event_type: str,
        details: dict[str, Any],
        *,
        delivery_status: str,
    ) -> None:
        family = audit_event_family(event_type)
        family_name = family.prefix.removesuffix(".") if family else "unknown"
        counters = {
            "events_total": 1,
            f"family.{family_name}": 1,
            f"delivery.{_bounded(delivery_status)}": 1,
        }
        result = details.get("stop_reason") or details.get("status")
        if isinstance(result, str):
            counters[f"result.{_bounded(result)}"] = 1
        if event_type.startswith("approval."):
            counters[f"approval.{_bounded(event_type.rsplit('.', 1)[-1])}"] = 1
        if event_type.startswith("memory."):
            counters[f"memory.{_bounded(event_type.rsplit('.', 1)[-1])}"] = 1
        if event_type.startswith("gateway."):
            counters[f"gateway.{_bounded(event_type.rsplit('.', 1)[-1])}"] = 1
        sums: dict[str, float] = {}
        for source, metric in (
            ("duration_ms", "task_duration_ms"),
            ("input_tokens", "provider_input_tokens"),
            ("output_tokens", "provider_output_tokens"),
            ("cost_usd", "provider_cost_usd"),
            ("queue_depth", "gateway_queue_depth_observed"),
        ):
            value = details.get(source)
            if isinstance(value, int | float) and not isinstance(value, bool):
                sums[metric] = float(value)
        self._merge(counters, sums)

    def snapshot(self) -> MetricsSnapshot:
        with _file_lock(self.path):
            payload = self._load_unlocked()
        return MetricsSnapshot(
            counters={key: int(value) for key, value in payload["counters"].items()},
            sums={key: float(value) for key, value in payload["sums"].items()},
            updated_at=payload.get("updated_at"),
        )

    def prometheus(self) -> str:
        snapshot = self.snapshot()
        lines = [
            "# HELP loro_operational_events_total Content-free Loro operational counters.",
            "# TYPE loro_operational_events_total counter",
        ]
        for key, value in sorted(snapshot.counters.items()):
            lines.append(f'loro_operational_events_total{{metric="{key}"}} {value}')
        lines.extend(
            [
                "# HELP loro_operational_value_sum Content-free Loro operational value sums.",
                "# TYPE loro_operational_value_sum counter",
            ]
        )
        for key, value in sorted(snapshot.sums.items()):
            lines.append(f'loro_operational_value_sum{{metric="{key}"}} {value}')
        return "\n".join(lines) + "\n"

    def _merge(self, counters: dict[str, int], sums: dict[str, float]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(self.path):
            payload = self._load_unlocked()
            for key, value in counters.items():
                payload["counters"][key] = int(payload["counters"].get(key, 0)) + value
            for key, value in sums.items():
                payload["sums"][key] = float(payload["sums"].get(key, 0)) + value
            payload["updated_at"] = datetime.now(UTC).isoformat()
            temporary = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": METRICS_SCHEMA_VERSION,
                "counters": {},
                "sums": {},
                "updated_at": None,
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Invalid operational metrics state: {self.path}") from error
        if not isinstance(payload, dict) or payload.get("schema_version") != METRICS_SCHEMA_VERSION:
            raise RuntimeError("Unsupported operational metrics schema.")
        if not isinstance(payload.get("counters"), dict) or not isinstance(
            payload.get("sums"), dict
        ):
            raise RuntimeError("Invalid operational metrics state.")
        return payload


def _bounded(value: str) -> str:
    normalized = "".join(character if character.isalnum() else "_" for character in value.lower())
    return (normalized.strip("_") or "unknown")[:64]
