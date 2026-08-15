# ruff: noqa: E402
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from loro import __version__


def main() -> int:
    path = ROOT / "docs" / "oap-conformance.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("oap") != "1.0" or payload.get("implementation") != "loro":
            raise ValueError("identity must declare Loro OAP 1.0")
        if payload.get("version") != __version__:
            raise ValueError("conformance version does not match package")
        if payload.get("level") != 3 or payload.get("status") != "provisional":
            raise ValueError("the current release must declare provisional Level 3")
        if payload.get("source_revision") is not None:
            raise ValueError("source revision must remain null until canonical upstream is pinned")
        for evidence in payload.get("evidence", []):
            if not (ROOT / evidence).is_file():
                raise ValueError(f"missing evidence: {evidence}")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"OAP conformance statement invalid: {error}")
        return 1
    print("OAP conformance statement OK: provisional Level 3 evidence is explicit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
