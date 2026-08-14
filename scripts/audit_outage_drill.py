from __future__ import annotations

import argparse
import json
import tempfile
import warnings
from pathlib import Path
from typing import Any

from loro.audit import AuditLogger
from loro.audit.collector import AuditCollector
from loro.audit.sinks import AuditSinkError
from loro.config import AuditConfig


class DrillSink:
    name = "drill"

    def __init__(self, collector: AuditCollector, token: str) -> None:
        self.collector = collector
        self.token = token
        self.available = False

    def deliver(self, payload: dict[str, Any]) -> None:
        self.deliver_batch([payload])

    def deliver_batch(self, payloads: list[dict[str, Any]]) -> None:
        if not self.available:
            raise AuditSinkError("injected collector outage")
        self.collector.accept(
            f"Bearer {self.token}",
            json.dumps({"events": payloads}).encode("utf-8"),
        )


def run_drill(root: Path, events: int) -> dict[str, Any]:
    token = "outage-drill-token"
    collector = AuditCollector(root / "collector.sqlite3", token)
    sink = DrillSink(collector, token)
    logger = AuditLogger(
        AuditConfig(
            sink="http",
            http_url="http://collector.invalid/events",
            buffer_path=str(root / "buffer.jsonl"),
            max_buffer_events=max(events, 1),
            http_batch_size=min(max(events, 1), 1000),
        ),
        sink=sink,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for index in range(events):
            logger.write("audit.outage_drill", sequence=index)
    buffered_before = logger.buffer.count()
    sink.available = True
    flushed = logger.flush()
    verification = collector.verify()
    ok = (
        buffered_before == events
        and flushed.delivered == events
        and flushed.remaining == 0
        and verification.ok
        and verification.events == events
    )
    return {
        "ok": ok,
        "generated": events,
        "buffered_before_recovery": buffered_before,
        "delivered_after_recovery": flushed.delivered,
        "remaining": flushed.remaining,
        "collector_events": verification.events,
        "collector_final_hash": verification.final_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Loro audit outage/recovery drill.")
    parser.add_argument("--events", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.events < 1 or args.events > 100_000:
        parser.error("--events must be between 1 and 100000")
    with tempfile.TemporaryDirectory(prefix="loro-audit-drill-") as directory:
        result = run_drill(Path(directory), args.events)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
