from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_release_metadata", ROOT / "scripts" / "check_release_metadata.py"
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for relative in (
        "pyproject.toml",
        "README.md",
        "src/loro/__init__.py",
        "webui/package.json",
        "webui/package-lock.json",
        "docs/release-contract.json",
        "docs/oap-conformance.json",
        "docs/ags-conformance.json",
        "docs/roadmap-1.0.md",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    shutil.copytree(ROOT / "docs" / "releases", tmp_path / "docs" / "releases")
    return tmp_path


def test_repository_release_metadata_is_checked() -> None:
    # Drift here is advisory on branches (a version bump may be in progress); CI runs the
    # script strictly on tag builds. This only proves every source is found and parsed.
    report = checker.check(ROOT)
    assert len(report.checked) >= 10
    assert report.version


def test_pending_wording_for_a_shipped_release_fails(tree: Path) -> None:
    readme = tree / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\nLoro `0.20.0` is the pending release candidate.\n",
        encoding="utf-8",
    )
    report = checker.check(tree)
    assert any("calls 0.20.0 pending" in issue for issue in report.issues)


def test_stale_roadmap_and_readme_line_fail(tree: Path) -> None:
    readme = tree / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("Current release: Loro", "Release: Loro"),
        encoding="utf-8",
    )
    roadmap = tree / "docs" / "roadmap-1.0.md"
    lines = [
        line
        for line in roadmap.read_text(encoding="utf-8").splitlines()
        if not line.startswith(f"| `{'.'.join(checker.check(ROOT).version.split('.')[:2])}` |")
    ]
    roadmap.write_text("\n".join(lines), encoding="utf-8")
    issues = checker.check(tree).issues
    assert any("current-release line" in issue for issue in issues)
    assert any("roadmap-1.0.md milestone" in issue for issue in issues)


def test_conformance_and_notes_drift_fail(tree: Path) -> None:
    conformance = tree / "docs" / "oap-conformance.json"
    payload = json.loads(conformance.read_text(encoding="utf-8"))
    payload["implementation_version"] = "0.0.1"
    conformance.write_text(json.dumps(payload), encoding="utf-8")
    version = checker.check(ROOT).version
    notes = tree / "docs" / "releases" / f"{version}.md"
    notes.write_text("# Loro\n\nRelease candidate prepared today.\n", encoding="utf-8")
    issues = checker.check(tree).issues
    assert any("oap-conformance.json" in issue for issue in issues)
    assert any("still describes a shipped release as pending" in issue for issue in issues)


def test_unreleased_notes_are_not_the_top_release(tree: Path) -> None:
    (tree / "docs" / "releases" / "99.0.0.md").write_text(
        "# Loro 99.0.0\n\nUnreleased. Work in progress.\n", encoding="utf-8"
    )
    assert checker.check(tree).issues == []


def test_tag_mismatch_fails_and_is_strict(
    tree: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_REF", "refs/tags/v9.9.9")
    assert checker.main(["--root", str(tree), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["strict"] is True
    assert any("git tag" in issue for issue in report["issues"])


def test_branch_drift_is_advisory(
    tree: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_REF", raising=False)
    (tree / "README.md").write_text("# Loro\n", encoding="utf-8")
    assert checker.main(["--root", str(tree)]) == 0
    assert "::warning::" in capsys.readouterr().out
    assert checker.main(["--root", str(tree), "--strict"]) == 1
