"""Check that every place naming Loro's version agrees with the package version.

The check reads the version from ``pyproject.toml`` and compares it with:

- ``src/loro/__init__.py`` ``__version__``;
- ``webui/package.json`` and ``webui/package-lock.json``;
- ``docs/release-contract.json`` ``package_version``;
- the ``implementation_version`` of ``docs/*-conformance.json``;
- the README "Current release" line, which must also not call a shipped release pending;
- the newest released notes page under ``docs/releases/`` (the local CHANGELOG), which must not
  still describe itself as a release candidate;
- the milestone table in ``docs/roadmap-1.0.md``, which must list the current minor as Released;
- on tag builds, the tag name itself.

It is strict on tag builds (``GITHUB_REF`` starts with ``refs/tags/``) or with ``--strict``, and
advisory otherwise, so an in-progress version bump on a branch does not block work. ``--json``
prints a machine-readable report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass
class Report:
    version: str = ""
    issues: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def expect(self, label: str, found: str | None, expected: str) -> None:
        self.checked.append(label)
        if found != expected:
            self.issues.append(f"{label}: found {found!r}, expected {expected!r}")


def _version_key(value: str) -> tuple[int, int, int]:
    match = VERSION.match(value)
    if match is None:
        raise ValueError(f"Not a release version: {value!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def check(root: Path = ROOT, *, tag: str | None = None) -> Report:
    report = Report()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(project["project"]["version"])
    report.version = version

    init = (root / "src" / "loro" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', init, re.MULTILINE)
    report.expect("src/loro/__init__.py __version__", match.group(1) if match else None, version)

    package = json.loads((root / "webui" / "package.json").read_text(encoding="utf-8"))
    report.expect("webui/package.json version", package.get("version"), version)
    lock_path = root / "webui" / "package-lock.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        report.expect("webui/package-lock.json version", lock.get("version"), version)
        root_package = lock.get("packages", {}).get("", {})
        report.expect(
            "webui/package-lock.json packages[''].version", root_package.get("version"), version
        )

    contract = json.loads((root / "docs" / "release-contract.json").read_text(encoding="utf-8"))
    report.expect(
        "docs/release-contract.json package_version", contract.get("package_version"), version
    )

    for path in sorted((root / "docs").glob("*-conformance.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        report.expect(
            f"docs/{path.name} implementation_version",
            payload.get("implementation_version"),
            version,
        )

    readme = (root / "README.md").read_text(encoding="utf-8")
    current = re.search(r"Current release: Loro `([^`]+)`", readme)
    report.expect("README.md current-release line", current.group(1) if current else None, version)
    report.checked.append("README.md pending-release wording")
    for pending in re.finditer(r"`(\d+\.\d+\.\d+)` is the pending", readme):
        if _version_key(pending.group(1)) <= _version_key(version):
            report.issues.append(
                f"README.md calls {pending.group(1)} pending, but {version} is released"
            )

    releases = root / "docs" / "releases"
    released: list[tuple[tuple[int, int, int], Path]] = []
    for path in releases.glob("*.md"):
        if VERSION.match(path.stem) is None:
            continue
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:6])
        if re.search(r"^Unreleased\b", head, re.MULTILINE):
            continue
        released.append((_version_key(path.stem), path))
    top = max(released)[1] if released else None
    report.expect("newest released notes in docs/releases/", top.stem if top else None, version)
    if top is not None:
        head = "\n".join(top.read_text(encoding="utf-8").splitlines()[:6])
        report.checked.append(f"docs/releases/{top.name} status wording")
        if re.search(r"release candidate prepared|\bpending\b", head, re.IGNORECASE):
            report.issues.append(
                f"docs/releases/{top.name} still describes a shipped release as pending"
            )

    roadmap = (root / "docs" / "roadmap-1.0.md").read_text(encoding="utf-8")
    major, minor, _ = _version_key(version)
    row = re.search(rf"^\| `{major}\.{minor}` \| ([^|]+) \|", roadmap, re.MULTILINE)
    report.expect(
        f"docs/roadmap-1.0.md milestone {major}.{minor}",
        row.group(1).strip() if row else None,
        "Released",
    )

    if tag:
        report.expect("git tag", tag.removeprefix("v"), version)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that release metadata agrees with the package version.",
        epilog=(
            "Examples:\n"
            "  python scripts/check_release_metadata.py\n"
            "  python scripts/check_release_metadata.py --strict --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--strict", action="store_true", help="Exit 1 on any drift.")
    parser.add_argument("--json", action="store_true", help="Print a JSON report.")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    ref = os.environ.get("GITHUB_REF", "")
    tag = ref.removeprefix("refs/tags/") if ref.startswith("refs/tags/") else None
    strict = args.strict or tag is not None
    report = check(args.root, tag=tag)
    failed = bool(report.issues)

    if args.json:
        print(
            json.dumps(
                {
                    "version": report.version,
                    "strict": strict,
                    "ok": not failed,
                    "checked": report.checked,
                    "issues": report.issues,
                },
                indent=2,
            )
        )
    elif failed:
        label = "error" if strict else "warning"
        for issue in report.issues:
            print(f"::{label}::Release metadata drift: {issue}")
        if not strict:
            print("Advisory only on branches; tag builds and --strict fail on this drift.")
    else:
        print(f"Release metadata OK: {len(report.checked)} checks agree on {report.version}.")
    return 1 if failed and strict else 0


if __name__ == "__main__":
    sys.exit(main())
