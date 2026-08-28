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
        if payload.get("schema") != "oap.conformance-result.v1":
            raise ValueError("schema must be oap.conformance-result.v1")
        if payload.get("oap") != "1.0" or payload.get("implementation") != "Loro":
            raise ValueError("identity must declare Loro OAP 1.0")
        if payload.get("implementation_version") != __version__:
            raise ValueError("conformance version does not match package")
        if payload.get("level") != 3 or payload.get("failed"):
            raise ValueError("the current release must pass Level 3")
        if payload.get("maintenance_release") != "1.0.1":
            raise ValueError("maintenance release must be 1.0.1")
        if len(str(payload.get("fixture_revision", ""))) != 40:
            raise ValueError("fixture revision must be an immutable commit")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"OAP conformance statement invalid: {error}")
        return 1
    print("OAP conformance statement OK: Level 3 result is explicit and immutable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
