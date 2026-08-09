#!/usr/bin/env python3
"""Compare detect-secrets findings without its volatile generation timestamp."""

import json
import sys
from pathlib import Path


def _results(path: str) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    results = payload.get("results")
    if not isinstance(results, dict):
        raise ValueError(f"Invalid detect-secrets baseline: {path}")
    return results


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_secret_baseline.py EXPECTED CURRENT")
        return 2
    expected = _results(sys.argv[1])
    current = _results(sys.argv[2])
    if expected != current:
        print("Secret candidate baseline drifted; review every changed finding explicitly.")
        return 1
    print(f"Secret candidate baseline unchanged ({sum(map(len, current.values()))} findings).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
