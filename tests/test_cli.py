from typer.testing import CliRunner

from loro.cli import app


def test_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "loro" in result.stdout


def test_plan_scaffold() -> None:
    result = CliRunner().invoke(app, ["plan", "Draft a rollout plan"])
    assert result.exit_code == 0
    assert "Loro plan mode is scaffolded" in result.stdout
