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


def test_docs_create(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        ["docs", "create", "Draft a rollout plan", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "Created document artifacts" in result.stdout


def test_brief_meeting(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        ["brief", "meeting", "Prepare for roadmap sync", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "Created meeting brief artifact" in result.stdout
