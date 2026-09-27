"""Golden snapshots of every `--help` screen and every `--json` output shape.

They pin the CLI's user-facing surface so internal refactors (such as splitting the CLI into
modules) cannot change it by accident. Regenerate deliberately with:

    LORO_UPDATE_GOLDEN=1 python -m pytest tests/test_cli_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from loro.cli import app

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("LORO_UPDATE_GOLDEN") == "1"
HELP_ENV = {"COLUMNS": "100", "NO_COLOR": "1", "TERM": "dumb"}
EXAMPLE_GRAPH = Path(__file__).parent / "fixtures" / "agraph" / "examples" / "minimal.agraph.yaml"


def _command_paths() -> list[list[str]]:
    paths: list[list[str]] = [[]]

    def walk(command: Any, prefix: list[str]) -> None:
        for name, child in sorted((getattr(command, "commands", None) or {}).items()):
            paths.append([*prefix, name])
            walk(child, [*prefix, name])

    walk(get_command(app), [])
    return paths


def _compare(name: str, actual: dict[str, Any]) -> None:
    path = GOLDEN / name
    if UPDATE or not path.exists():
        path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not UPDATE:
            pytest.fail(f"Created missing golden file {path}; rerun to compare.")
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    changed = sorted(
        key for key in expected.keys() | actual.keys() if expected.get(key) != actual.get(key)
    )
    assert not changed, (
        f"CLI surface changed for: {', '.join(changed)}. If intended, regenerate with "
        "LORO_UPDATE_GOLDEN=1."
    )


def test_every_help_screen_matches_golden() -> None:
    runner = CliRunner()
    screens: dict[str, str] = {}
    for path in _command_paths():
        result = runner.invoke(app, [*path, "--help"], env=HELP_ENV)
        assert result.exit_code == 0, f"loro {' '.join(path)} --help failed: {result.output}"
        screens[" ".join(["loro", *path])] = "\n".join(
            line.rstrip() for line in result.output.splitlines()
        )
    _compare("cli-help.json", screens)


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    if value is None:
        return "null"
    return type(value).__name__


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.chdir(project)
    for name, value in {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        'schema_version = "1.0"\n[model]\nprovider = "mock"\nmodel = "mock-agent"\n'
        f'[audit]\npath = "{project / "audit.jsonl"}"\n'
        f'buffer_path = "{project / "buffer.jsonl"}"\n'
        f'[sessions]\npath = "{project / "sessions"}"\n'
        f'message_path = "{project / "messages"}"\n'
        "[memory.local]\nenabled = false\n",
    )
    return project


def test_every_json_output_shape_matches_golden(isolated: Path) -> None:
    runner = CliRunner()
    shapes: dict[str, Any] = {}

    def capture(label: str, arguments: list[str], ok_codes: tuple[int, ...] = (0,)) -> Any:
        result = runner.invoke(app, arguments)
        assert result.exit_code in ok_codes, f"{label}: {result.output}"
        payload = json.loads(result.stdout)
        shapes[label] = _shape(payload)
        return payload

    capture("loro doctor --json", ["doctor", "--json"], (0, 1))
    capture("loro capabilities --json", ["capabilities", "--json"])
    capture("loro config check --json", ["config", "check", "--json"], (0, 1))
    ran = capture("loro run --json", ["run", "Say hello.", "--json"])
    capture("loro run list --json", ["run", "list", "--json"])
    capture(
        "loro run export --json",
        ["run", "export", ran["run_id"], "--out", "run.zip", "--json"],
    )
    capture("loro run verify --json", ["run", "verify", "run.zip", "--json"])
    capture("loro graph plan --json", ["graph", "plan", str(EXAMPLE_GRAPH), "--json"], (0, 1))
    capture("loro approvals list --json", ["approvals", "list", "--json"])
    capture("loro approvals recovery --json", ["approvals", "recovery", "--json"])
    capture("loro plugins list --json", ["plugins", "list", "--json"])
    _compare("cli-json-shapes.json", shapes)
