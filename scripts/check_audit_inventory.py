from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from loro.audit.inventory import audit_event_family

ROOT = Path(__file__).resolve().parents[1]
EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


def literal_audit_events(source_root: Path) -> dict[str, list[str]]:
    events: dict[str, list[str]] = {}
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr not in {"write", "_emit"}:
                continue
            value = node.args[0]
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            event_type = value.value
            if not EVENT_PATTERN.fullmatch(event_type):
                continue
            location = f"{path.relative_to(ROOT)}:{node.lineno}"
            events.setdefault(event_type, []).append(location)
    return events


def main() -> int:
    events = literal_audit_events(ROOT / "src" / "loro")
    unknown = {
        event: locations
        for event, locations in events.items()
        if audit_event_family(event) is None
    }
    if unknown:
        for event, locations in unknown.items():
            print(f"Unregistered audit event family for {event}: {', '.join(locations)}")
        return 1
    print(
        f"Audit inventory OK: {len(events)} literal event types are assigned "
        "to a governed family."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
