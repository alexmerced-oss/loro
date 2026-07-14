from subprocess import CompletedProcess

from typer.testing import CliRunner

from loro.cli import app


def test_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "loro" in result.stdout


def test_plan_scaffold() -> None:
    result = CliRunner().invoke(app, ["plan", "Draft a rollout plan"])
    assert result.exit_code == 0
    assert "Loro plan mode completed" in result.stdout


def test_plan_with_explicit_tool_call(tmp_path, monkeypatch) -> None:
    note = tmp_path / "note.txt"
    note.write_text("hello from tool\n", encoding="utf-8")
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[sessions]\npath = \"{tmp_path / 'sessions'}\"\n"
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    result = CliRunner().invoke(
        app,
        ["plan", f'Read the note.\n@tool file.read {{"path": "{note}", "limit": 30}}'],
    )
    assert result.exit_code == 0
    assert "Tool results" in result.stdout
    assert "hello from tool" in result.stdout


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


def test_data_tables_typed_command(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []

    def fake_run(command, capture_output, text, check):
        commands.append(command)
        return CompletedProcess(command, 0, stdout="events\n", stderr="")

    monkeypatch.setattr("loro.polaris.run", fake_run)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[polaris]\n"
        'enabled = true\n'
        'cli_path = "polaris"\n'
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    result = CliRunner().invoke(
        app,
        ["data", "tables", "--namespace", "analytics", "--catalog", "prod"],
    )
    assert result.exit_code == 0
    assert "events" in result.stdout
    assert commands == [
        [
            "polaris",
            "tables",
            "list",
            "--namespace",
            "analytics",
            "--catalog",
            "prod",
        ]
    ]


def test_data_applicable_policies_typed_command(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []

    def fake_run(command, capture_output, text, check):
        commands.append(command)
        return CompletedProcess(command, 0, stdout="pii-mask\n", stderr="")

    monkeypatch.setattr("loro.polaris.run", fake_run)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[polaris]\n"
        'enabled = true\n'
        'cli_path = "polaris"\n'
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    result = CliRunner().invoke(
        app,
        [
            "data",
            "applicable-policies",
            "events",
            "--namespace",
            "analytics",
            "--catalog",
            "prod",
        ],
    )
    assert result.exit_code == 0
    assert "pii-mask" in result.stdout
    assert commands == [
        [
            "polaris",
            "applicable-policies",
            "list",
            "--resource",
            "events",
            "--catalog",
            "prod",
            "--namespace",
            "analytics",
        ]
    ]


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


def test_shared_memory_schema_command_uses_iceberg_config(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[memory.shared]\n"
        'iceberg_namespace = "enterprise_memory"\n'
        'iceberg_table = "agent_facts"\n',
    )
    result = CliRunner().invoke(app, ["memory", "schema", "--backend", "iceberg"])
    assert result.exit_code == 0
    assert "enterprise_memory.agent_facts" in result.stdout


def test_shared_memory_apply_schema_dry_run(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[memory.shared]\npostgres_schema = "agent_memory"\n',
    )
    result = CliRunner().invoke(app, ["memory", "apply-schema"])
    assert result.exit_code == 0
    assert "CREATE SCHEMA IF NOT EXISTS agent_memory" in result.stdout
    assert "agent_memory.shared_memories" in result.stdout


def test_shared_memory_apply_schema_execute_requires_dsn(monkeypatch) -> None:
    monkeypatch.delenv("LORO_POSTGRES_DSN", raising=False)
    result = CliRunner().invoke(app, ["memory", "apply-schema", "--execute"])
    assert result.exit_code != 0
    assert "Missing DSN env var" in result.stderr


def test_shared_memory_backend_check_missing_postgres_dsn(monkeypatch) -> None:
    monkeypatch.delenv("LORO_POSTGRES_DSN", raising=False)
    result = CliRunner().invoke(app, ["memory", "backend-check"])
    assert result.exit_code == 1
    assert "postgres" in result.stdout
    assert "LORO_POSTGRES_DSN" in result.stdout


def test_shared_memory_backend_check_iceberg(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[memory.shared]\nbackend = \"iceberg\"\niceberg_table = \"agent_facts\"\n",
    )
    result = CliRunner().invoke(app, ["memory", "backend-check"])
    assert result.exit_code == 1
    assert "agent_memory.agent_facts" in result.stdout
    assert "Live Iceberg commits" in result.stdout


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


def test_shared_memory_commit_draft_renders_postgres_sql(tmp_path, monkeypatch) -> None:
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
    draft_id = remember_result.stdout.splitlines()[0].split(": ", maxsplit=1)[1]
    commit_result = runner.invoke(app, ["memory", "commit-draft", draft_id])
    assert commit_result.exit_code == 0
    assert '"backend": "postgres"' in commit_result.stdout
    assert "INSERT INTO public.shared_memories" in commit_result.stdout
    assert "enterprise launch readiness" in commit_result.stdout


def test_shared_memory_commit_draft_rejects_iceberg_execute(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[memory.local]\npath = \"{tmp_path / 'memory'}\"\n"
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n"
        '[memory.shared]\nbackend = "iceberg"\n',
    )
    runner = CliRunner()
    remember_result = runner.invoke(
        app,
        ["remember", "--shared", "Use the enterprise launch readiness template"],
    )
    assert remember_result.exit_code == 0
    draft_id = remember_result.stdout.splitlines()[0].split(": ", maxsplit=1)[1]
    commit_result = runner.invoke(app, ["memory", "commit-draft", draft_id, "--execute"])
    assert commit_result.exit_code != 0
    assert "Live Iceberg commits are not enabled" in commit_result.stderr


def test_shared_memory_search_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    result = CliRunner().invoke(
        app,
        ["memory", "shared-search", "launch", "--tenant-id", "acme", "--dry-run"],
    )
    assert result.exit_code == 0
    assert '"backend": "postgres"' in result.stdout
    assert "FROM public.shared_memories" in result.stdout


def test_memory_proposal_accepts_local(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[memory.local]\npath = \"{tmp_path / 'memory'}\"\n"
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    runner = CliRunner()
    propose_result = runner.invoke(
        app,
        ["memory", "propose", "Use concise launch summaries", "--target", "local"],
    )
    assert propose_result.exit_code == 0
    proposal_id = propose_result.stdout.strip().split(": ", maxsplit=1)[1]
    accept_result = runner.invoke(app, ["memory", "accept-proposal", proposal_id])
    assert accept_result.exit_code == 0
    assert "Accepted proposal as local memory" in accept_result.stdout
    list_result = runner.invoke(app, ["memory", "proposals"])
    assert "accepted" in list_result.stdout


def test_memory_proposal_accepts_shared_as_draft(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[memory.local]\npath = \"{tmp_path / 'memory'}\"\n"
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    runner = CliRunner()
    propose_result = runner.invoke(
        app,
        ["memory", "propose", "Use shared launch template", "--target", "shared"],
    )
    assert propose_result.exit_code == 0
    proposal_id = propose_result.stdout.strip().split(": ", maxsplit=1)[1]
    accept_result = runner.invoke(app, ["memory", "accept-proposal", proposal_id])
    assert accept_result.exit_code == 0
    assert "Accepted proposal as shared memory draft" in accept_result.stdout
    drafts_result = runner.invoke(app, ["memory", "drafts"])
    assert "Use shared launch" in drafts_result.stdout
    assert "template" in drafts_result.stdout


def test_safety_scan_detects_secret() -> None:
    result = CliRunner().invoke(app, ["safety", "scan", "api_key = 'abc123456789'"])
    assert result.exit_code == 1
    assert "assignment_secret" in result.stdout


def test_memory_write_blocks_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[memory.local]\npath = \"{tmp_path / 'memory'}\"\n"
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    result = CliRunner().invoke(app, ["remember", "--local", "token = 'abc123456789'"])
    assert result.exit_code != 0
    assert "Sensitive content detected" in result.stderr


def test_memory_write_allows_secret_with_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[memory.local]\npath = \"{tmp_path / 'memory'}\"\n"
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    result = CliRunner().invoke(
        app,
        ["remember", "--local", "--allow-sensitive", "token = 'abc123456789'"],
    )
    assert result.exit_code == 0
    assert "Saved local memory" in result.stdout


def test_providers_list() -> None:
    result = CliRunner().invoke(app, ["providers", "list"])
    assert result.exit_code == 0
    assert "openai" in result.stdout
    assert "ollama" in result.stdout


def test_provider_show() -> None:
    result = CliRunner().invoke(app, ["providers", "show", "anthropic"])
    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY" in result.stdout


def test_provider_show_alias() -> None:
    result = CliRunner().invoke(app, ["providers", "show", "nous-portal"])
    assert result.exit_code == 0
    assert "NOUS_API_KEY" in result.stdout
    assert "inference.nousresearch.com" in result.stdout


def test_configure_non_interactive(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    output = tmp_path / "config.local.toml"
    result = CliRunner().invoke(
        app,
        [
            "configure",
            "--provider",
            "ollama",
            "--model",
            "llama3.2",
            "--small-model",
            "llama3.2",
            "--base-url",
            "http://localhost:11434",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0
    assert output.exists()
    assert 'provider = "ollama"' in output.read_text(encoding="utf-8")


def test_configure_with_provider_alias(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f"[audit]\npath = \"{tmp_path / 'audit.jsonl'}\"\n",
    )
    output = tmp_path / "config.local.toml"
    result = CliRunner().invoke(
        app,
        [
            "configure",
            "--provider",
            "go",
            "--model",
            "kimi-k2",
            "--small-model",
            "glm-5",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert 'provider = "opencode-go"' in text
    assert 'api_key_env = "OPENCODE_GO_API_KEY"' in text


def test_providers_check_missing_key(monkeypatch) -> None:
    monkeypatch.delenv("NOUS_API_KEY", raising=False)
    result = CliRunner().invoke(app, ["providers", "check", "nous"])
    assert result.exit_code == 1
    assert "NOUS_API_KEY" in result.stdout


def test_providers_request_openai_compatible(monkeypatch) -> None:
    monkeypatch.setenv("NOUS_API_KEY", "secret")
    result = CliRunner().invoke(
        app,
        [
            "providers",
            "request",
            "hello",
            "--provider",
            "nous",
            "--model",
            "hermes-test",
        ],
    )
    assert result.exit_code == 0
    assert "chat/completions" in result.stdout
    assert "[redacted]" in result.stdout
