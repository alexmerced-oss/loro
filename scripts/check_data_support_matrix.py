from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    matrix = json.loads(
        (ROOT / "docs" / "data-support-matrix.json").read_text(encoding="utf-8")
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github" / "workflows" / "integration.yml").read_text(
        encoding="utf-8"
    )
    data_dependencies = set(project["project"]["optional-dependencies"]["data"])
    integration_dependencies = set(
        project["project"]["optional-dependencies"]["integration"]
    )
    stack = matrix["reference_stack"]
    expected = {
        f"pyiceberg>={stack['pyiceberg']},<0.12": data_dependencies,
        f"duckdb=={stack['duckdb']}": integration_dependencies,
    }
    issues = [dependency for dependency, group in expected.items() if dependency not in group]
    workflow_pins = (
        f"apache-polaris-{stack['apache_polaris']}",
        f"apache/polaris:{stack['apache_polaris']}",
    )
    issues.extend(pin for pin in workflow_pins if pin not in workflow)
    if issues:
        for issue in issues:
            print(f"Data support matrix pin is missing: {issue}")
        return 1
    print("Data support matrix OK: dependency and integration pins agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
