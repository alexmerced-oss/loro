"""`loro run` machine interface: exit codes, --json, and --prompt-file (L-8, L-9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loro.cli import app


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'schema_version = "1.0"\n{model}\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n'
        f'buffer_path = "{tmp_path / "buffer.jsonl"}"\n'
        f'[sessions]\npath = "{tmp_path / "sessions"}"\n'
        f'message_path = "{tmp_path / "messages"}"\n'
        "[memory.local]\nenabled = false\n",
    )
    return tmp_path


@pytest.fixture
def mock_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _project(tmp_path, monkeypatch, '[model]\nprovider = "mock"\nmodel = "mock-agent"')


def test_json_output_is_one_parseable_object(mock_project: Path) -> None:
    result = CliRunner().invoke(app, ["run", "Say hello.", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["stop_reason"] == "completed"
    assert payload["provider"] == "mock" and payload["model"] == "mock-agent"
    assert payload["run_id"] and payload["session_id"]
    assert "Say hello." in payload["response"]


def test_provider_error_exits_non_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Port 9 (discard) refuses connections, so the provider call fails fast.
    _project(
        tmp_path,
        monkeypatch,
        '[model]\nprovider = "nous"\nmodel = "deepseek/deepseek-v4-flash"\n'
        'base_url = "http://127.0.0.1:9/v1"\nmax_retries = 0\ntimeout_seconds = 5',
    )
    runner = CliRunner()

    text = runner.invoke(app, ["run", "Hello."])
    assert text.exit_code == 1
    assert "loro providers smoke --execute" in text.output

    as_json = runner.invoke(app, ["run", "Hello.", "--json"])
    assert as_json.exit_code == 1
    payload = json.loads(as_json.stdout)
    assert payload["ok"] is False and payload["stop_reason"] == "provider_error"


def test_prompt_file_supplies_large_prompts(mock_project: Path) -> None:
    prompt = "Review this change.\n" + "context line\n" * 20_000
    (mock_project / "task.md").write_text(prompt, encoding="utf-8")

    result = CliRunner().invoke(app, ["run", "--prompt-file", "task.md", "--json"])

    assert result.exit_code == 0, result.output
    assert "Review this change." in json.loads(result.output)["response"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["run", "inline", "--prompt-file", "task.md"], "not both"),
        (["run", "--prompt-file", "-"], "stdin is reserved"),
        (["run", "--prompt-file", "missing.md"], "not found"),
        (["run", "--prompt-file", "empty.md"], "empty"),
        (["run"], "Missing task prompt"),
        (["run", "hi", "--json", "--stream"], "cannot be combined"),
    ],
)
def test_prompt_usage_errors_are_explained(
    mock_project: Path, arguments: list[str], message: str
) -> None:
    (mock_project / "task.md").write_text("task", encoding="utf-8")
    (mock_project / "empty.md").write_text("  \n", encoding="utf-8")

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 2
    assert message in result.output
    assert "Traceback" not in result.output


def test_oversized_prompt_file_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(
        tmp_path,
        monkeypatch,
        '[model]\nprovider = "mock"\nmodel = "mock-agent"\n[runtime]\nmax_model_input_bytes = 2048',
    )
    (tmp_path / "big.md").write_text("x" * 4096, encoding="utf-8")

    result = CliRunner().invoke(app, ["run", "--prompt-file", "big.md"])

    assert result.exit_code == 2
    assert "max_model_input_bytes" in result.output
