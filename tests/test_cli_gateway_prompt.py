"""Interactive gateway setup validates the platform without typer.Choice (removed in typer 0.27)."""

from __future__ import annotations

from typer.testing import CliRunner

from loro.cli import app


def test_interactive_platform_prompt_rejects_unknown_platforms(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["setup", "gateway"], input="remote\nbogus\n")

    assert "Choose one of:" in result.output
    assert "slack" in result.output
    assert not isinstance(result.exception, AttributeError)
