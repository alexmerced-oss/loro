"""Detect a project's test runner and summarize a bounded run of it."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

RUNNERS = ("pytest", "npm", "cargo")
MAX_EXTRA_ARGS = 32
_SAFE_ARG = re.compile(r"^[A-Za-z0-9_./:=@,+\-\[\]]{1,256}$")


@dataclass(frozen=True)
class TestCommand:
    runner: str
    args: list[str]
    reason: str


def detect_runner(project: Path) -> str | None:
    """pytest, npm or cargo, from the files a project actually has."""

    if (project / "Cargo.toml").is_file():
        return "cargo"
    package = project / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError):
            scripts = {}
        if isinstance(scripts, dict) and scripts.get("test"):
            return "npm"
    python_markers = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "conftest.py")
    if any((project / name).is_file() for name in python_markers) or (project / "tests").is_dir():
        return "pytest"
    return None


def build_command(project: Path, runner: str, extra: list[str]) -> TestCommand:
    """The argv to run; extra arguments must be plain tokens (no shell is involved)."""

    if runner == "auto":
        detected = detect_runner(project)
        if detected is None:
            raise ValueError(
                "No test runner detected: expected Cargo.toml, a package.json test script, "
                "or a Python project (pyproject.toml, pytest.ini, setup.cfg, tests/)."
            )
        runner = detected
        reason = f"detected {runner}"
    else:
        reason = f"requested {runner}"
    if runner not in RUNNERS:
        raise ValueError(f"Unsupported test runner {runner!r}; choose auto, pytest, npm or cargo.")
    if len(extra) > MAX_EXTRA_ARGS or not all(_SAFE_ARG.fullmatch(item) for item in extra):
        raise ValueError(
            "Extra test arguments must be at most 32 simple tokens "
            "(letters, digits, and ./:=@,+-[])."
        )
    if runner == "pytest":
        # The interpreter on PATH, not one inside the workspace: the sandbox refuses binaries
        # the agent could have planted there (see sandbox.trusted_executable_prefixes).
        argv = ["python3", "-m", "pytest", "-q", *extra]
    elif runner == "npm":
        argv = ["npm", "test", "--silent", *(["--", *extra] if extra else [])]
    else:
        argv = ["cargo", "test", *extra]
    return TestCommand(runner=runner, args=argv, reason=reason)


def summarize(runner: str, output: str) -> str | None:
    """The runner's own one-line result, when it printed one."""

    patterns = {
        "pytest": r"^=*\s*(\d+ (?:passed|failed|error).*?)\s*=*$",
        "cargo": r"^(test result: .*)$",
        "npm": r"^(Tests?:\s+.*|# (?:pass|fail)\s+\d+)$",
    }
    matches = re.findall(patterns[runner], output, flags=re.MULTILINE)
    return matches[-1].strip() if matches else None


def tail(output: str, limit: int) -> tuple[str, bool]:
    """Keep the end of the output, where test failures are reported."""

    if len(output) <= limit:
        return output, False
    return "[... earlier output truncated ...]\n" + output[-limit:], True
