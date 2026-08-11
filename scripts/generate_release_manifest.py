from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Loro release evidence manifest.")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--workflow-run")
    args = parser.parse_args()
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    support_matrix = json.loads(
        (ROOT / "docs" / "support-matrix.json").read_text(encoding="utf-8")
    )
    commit = args.commit or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    artifacts = []
    for path in sorted(args.dist.iterdir()):
        if not path.is_file() or path.name in {"SHA256SUMS", "release-manifest.json"}:
            continue
        artifacts.append({"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    manifest = {
        "schema_version": "1.0",
        "project": metadata["project"]["name"],
        "version": metadata["project"]["version"],
        "commit": commit,
        "generated_at": datetime.now(UTC).isoformat(),
        "workflow_run": args.workflow_run or os.environ.get("GITHUB_RUN_ID"),
        "artifacts": artifacts,
        "support_matrix": support_matrix,
        "evidence": {
            "enterprise_register": "docs/enterprise-evidence.md",
            "external_requirements": "docs/external-enterprise-requirements.md",
            "roadmap": "docs/roadmap-1.0.md",
        },
    }
    output = args.output or args.dist / "release-manifest.json"
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
