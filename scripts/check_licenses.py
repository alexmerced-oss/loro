#!/usr/bin/env python3
"""Reject dependencies with licenses prohibited by the repository policy."""

import json
import sys
from pathlib import Path

PROHIBITED = ("GNU Affero General Public License", "AGPL")


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "security-licenses.json")
    packages = json.loads(path.read_text(encoding="utf-8"))
    rejected = [
        f"{package['Name']}=={package['Version']} ({package.get('License', 'UNKNOWN')})"
        for package in packages
        if any(term.casefold() in package.get("License", "").casefold() for term in PROHIBITED)
    ]
    if rejected:
        print("Prohibited dependency licenses:\n" + "\n".join(rejected))
        return 1
    unknown = [package["Name"] for package in packages if package.get("License") == "UNKNOWN"]
    if unknown:
        print("Review UNKNOWN licenses: " + ", ".join(sorted(unknown)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
