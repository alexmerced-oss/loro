#!/usr/bin/env python3
"""Enforce branch-aware coverage floors for security-critical modules."""

import json
import sys
from pathlib import Path

THRESHOLDS = {
    "src/loro/approvals.py": 85.0,
    "src/loro/audit/": 85.0,
    "src/loro/budgets.py": 90.0,
    "src/loro/data_protection.py": 85.0,
    "src/loro/identity.py": 90.0,
    "src/loro/resources.py": 85.0,
    "src/loro/sandbox.py": 70.0,
}


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "coverage.json")
    files = json.loads(path.read_text(encoding="utf-8"))["files"]
    failed = False
    for prefix, threshold in THRESHOLDS.items():
        matched = [summary["summary"] for name, summary in files.items() if name.startswith(prefix)]
        if not matched:
            print(f"ERROR {prefix}: no coverage data")
            failed = True
            continue
        covered = sum(item["covered_lines"] + item["covered_branches"] for item in matched)
        total = sum(item["num_statements"] + item["num_branches"] for item in matched)
        percent = 100.0 if total == 0 else covered * 100 / total
        print(f"{prefix}: {percent:.2f}% (required {threshold:.2f}%)")
        failed = failed or percent < threshold
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
