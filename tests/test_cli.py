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


def test_sessions_list_after_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[sessions]\npath = \"{tmp_path / 'sessions'}\"\n"
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    runner = CliRunner()
    plan_result = runner.invoke(app, ["plan", "Draft a rollout plan"])
    assert plan_result.exit_code == 0
    list_result = runner.invoke(app, ["sessions", "list"])
    assert list_result.exit_code == 0
    assert "Draft a rollout plan" in list_result.stdout


def test_file_read_command(tmp_path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("hello loro\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["file", "read", str(note)])
    assert result.exit_code == 0
    assert "hello loro" in result.stdout


def test_shell_run_requires_yes() -> None:
    result = CliRunner().invoke(app, ["shell", "run", "--", "python", "-c", "print('loro')"])
    assert result.exit_code != 0
    assert "requires approval" in str(result.exception)


def test_shell_run_with_yes() -> None:
    result = CliRunner().invoke(
        app,
        ["shell", "run", "--yes", "--", "python", "-c", "print('loro')"],
    )
    assert result.exit_code == 0
    assert "loro" in result.stdout


def test_shared_memory_schema_command() -> None:
    result = CliRunner().invoke(app, ["memory", "schema", "--backend", "iceberg"])
    assert result.exit_code == 0
    assert "USING iceberg" in result.stdout


def test_shared_memory_draft_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[memory.local]\npath = \"{tmp_path / 'memory'}\"\n"
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    runner = CliRunner()
    remember_result = runner.invoke(
        app,
        ["remember", "--shared", "Use the enterprise launch readiness template"],
    )
    assert remember_result.exit_code == 0
    assert "Staged shared memory draft" in remember_result.stdout
    drafts_result = runner.invoke(app, ["memory", "drafts"])
    assert drafts_result.exit_code == 0
    assert "enterprise" in drafts_result.stdout
    assert "launch readiness" in drafts_result.stdout
