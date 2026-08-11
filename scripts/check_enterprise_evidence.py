from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "docs" / "enterprise-evidence.md"
VALID_STATES = {"Existing", "Partial", "Planned", "External"}
EXPECTED_IDS = {
    *(f"E0-{index:02d}" for index in range(1, 7)),
    *(f"E1-{index:02d}" for index in range(1, 8)),
    *(f"E2-{index:02d}" for index in range(1, 9)),
    *(f"E3-{index:02d}" for index in range(1, 8)),
    *(f"E4-{index:02d}" for index in range(1, 11)),
    *(f"E5-{index:02d}" for index in range(1, 9)),
}


def main() -> int:
    rows: dict[str, str] = {}
    errors: list[str] = []
    for line_number, line in enumerate(REGISTER.read_text(encoding="utf-8").splitlines(), start=1):
        match = re.match(r"^\| (E\d-\d{2}) \|.*?\|.*?\| ([A-Za-z]+) \|", line)
        if match is None:
            continue
        evidence_id, state = match.groups()
        if evidence_id in rows:
            errors.append(f"duplicate evidence id {evidence_id} at line {line_number}")
        rows[evidence_id] = state
        if state not in VALID_STATES:
            errors.append(f"invalid state {state!r} for {evidence_id}")
    missing = sorted(EXPECTED_IDS - rows.keys())
    unexpected = sorted(rows.keys() - EXPECTED_IDS)
    if missing:
        errors.append("missing evidence ids: " + ", ".join(missing))
    if unexpected:
        errors.append("unexpected evidence ids: " + ", ".join(unexpected))
    if errors:
        print("Enterprise evidence register is invalid:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Enterprise evidence register OK: {len(rows)} unique items use valid states.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
