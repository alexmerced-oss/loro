"""The mypy ignore list in pyproject.toml is a backlog that may only shrink.

A module listed under ``[[tool.mypy.overrides]] ignore_errors`` must still fail the baseline
(otherwise remove it), and every unlisted module must pass (otherwise fix it rather than
adding it to the list). The ceiling below pins the current size; lower it when you remove
entries.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IGNORE_CEILING = 25


def _config() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mypy"]


def _ignored_modules() -> list[str]:
    overrides = [item for item in _config().get("overrides", []) if item.get("ignore_errors")]
    assert len(overrides) == 1, "Keep a single ignore_errors override block."
    return list(overrides[0]["module"])


def _module_path(module: str) -> Path:
    base = ROOT / "src" / Path(*module.split("."))
    return base / "__init__.py" if base.is_dir() else base.with_suffix(".py")


def test_ignore_list_is_sorted_unique_and_real() -> None:
    modules = _ignored_modules()
    assert modules == sorted(set(modules)), "Keep the ignore list sorted and unique."
    missing = [module for module in modules if not _module_path(module).exists()]
    assert missing == [], f"Remove deleted modules from the ignore list: {missing}"


def test_ignore_list_never_grows() -> None:
    size = len(_ignored_modules())
    assert size <= IGNORE_CEILING, (
        f"The mypy ignore list grew to {size} modules (ceiling {IGNORE_CEILING}). "
        "Fix the new type errors instead of ignoring the module."
    )


def test_ignore_list_matches_modules_that_still_fail(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    config = _config()
    lines = ["[mypy]"]
    for key in (
        "python_version",
        "ignore_missing_imports",
        "warn_redundant_casts",
        "no_implicit_optional",
        "check_untyped_defs",
    ):
        value = config[key]
        lines.append(f"{key} = {str(value) if not isinstance(value, bool) else value}")
    unrestricted = tmp_path / "mypy.ini"
    unrestricted.write_text("\n".join(lines) + "\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(unrestricted),
            "--cache-dir",
            str(tmp_path / "cache"),
            "src/loro",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    failing = {
        re.sub(r"\.__init__$", "", match.group(1).replace("/", "."))
        for match in re.finditer(r"^src/(loro/[\w/]+)\.py:\d+: error:", completed.stdout, re.M)
    }
    listed = set(_ignored_modules())
    now_passing = sorted(listed - failing)
    newly_failing = sorted(failing - listed)
    assert now_passing == [], (
        f"These modules now type-check; remove them from the ignore list: {now_passing}"
    )
    assert newly_failing == [], (
        f"These modules gained type errors; fix them (do not list them): {newly_failing}"
    )
