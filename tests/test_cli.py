import json
import tomllib

from typer.testing import CliRunner

from loro.audit import AuditDeliveryError
from loro.cli import app
from loro.memory.base import SharedMemoryBackendCheck
from loro.sandbox import SandboxResult


def fake_sandbox_run(commands: list[list[str]], stdout: str):
    def run(self, args, *, profile_name, cwd=None, timeout=None):
        commands.append(args)
        return SandboxResult(
            args=args,
            stdout=stdout,
            stderr="",
            returncode=0,
            profile=profile_name,
            os_enforced=False,
            output_truncated=False,
        )

    return run


def test_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "loro" in result.stdout


def test_provider_conformance_command_reports_advertised_contracts() -> None:
    result = CliRunner().invoke(app, ["providers", "conformance"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert "openai-compatible" in payload["protocols"]
    assert "anthropic.json" in payload["fixtures"]


def test_gateway_setup_writes_references_and_scope(tmp_path) -> None:
    output = tmp_path / "config.local.toml"
    result = CliRunner().invoke(
        app,
        [
            "gateway",
            "setup",
            "--id",
            "work-slack",
            "--platform",
            "slack",
            "--user-id",
            "U123",
            "--subject",
            "alex",
            "--tenant",
            "acme",
            "--channel",
            "C123",
            "--workspace",
            "T123",
            "--credential",
            "signing-secret=vault://gateway/work-slack/signing-secret",
            "--credential",
            "bot-token=vault://gateway/work-slack/bot-token",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.stdout
    written = output.read_text(encoding="utf-8")
    endpoint = tomllib.loads(written)["gateway"]["endpoints"]["work-slack"]
    assert endpoint["allowed_channels"] == ["C123"]
    assert endpoint["allowed_workspaces"] == ["T123"]
    assert "vault://gateway/work-slack/signing-secret" in written
    assert "secret value" not in written


def test_policy_explain_reports_structured_match(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        """
        [permissions]
        version = "enterprise-42"
        shell = "allow"

        [[permissions.rules]]
        tool = "shell"
        action = "run*"
        resource_kind = "shell"
        decision = "deny"
        reason = "interpreters require a sandbox"

        [permissions.rules.resource]
        executable_name = "python"
        """,
    )
    fixture = json.dumps(
        {
            "tool": "shell",
            "action": "run command",
            "resource": {
                "kind": "shell",
                "executable_name": "python",
                "arguments": ["-V"],
            },
        }
    )

    result = CliRunner().invoke(app, ["policy", "explain", fixture])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "deny"
    assert payload["policy_version"] == "enterprise-42"
    assert payload["policy_source"] == "permissions.rules[0]"
    assert payload["normalized_resource"]["executable_name"] == "python"


def test_audit_doctor_reports_jsonl_ready(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n'
        f'buffer_path = "{tmp_path / "buffer.jsonl"}"\n',
    )

    result = CliRunner().invoke(app, ["audit", "doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["schema_version"] == "1.0"
    assert payload["sink"] == "jsonl"


def test_audit_doctor_fails_for_incomplete_http_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[audit]\nsink = "http"\nhttp_token_env = "MISSING_TOKEN"\n'
        f'buffer_path = "{tmp_path / "buffer.jsonl"}"\n',
    )

    result = CliRunner().invoke(app, ["audit", "doctor"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert len(payload["issues"]) == 2


def test_audit_flush_retries_buffered_events(tmp_path, monkeypatch) -> None:
    buffer_path = tmp_path / "buffer.jsonl"
    buffer_path.write_text('{"event_id":"event-1","event_type":"test"}\n', encoding="utf-8")
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[audit]\nsink = "http"\nhttp_url = "https://audit.example/events"\n'
        f'buffer_path = "{buffer_path}"\n',
    )
    delivered: list[dict] = []
    monkeypatch.setattr(
        "loro.audit.HttpAuditSink.deliver",
        lambda self, payload: delivered.append(payload),
    )

    result = CliRunner().invoke(app, ["audit", "flush"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"attempted": 1, "delivered": 1, "remaining": 0}
    assert delivered[0]["event_id"] == "event-1"
    assert buffer_path.read_text(encoding="utf-8") == ""


def test_audit_verify_reports_valid_chain(tmp_path, monkeypatch) -> None:
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{audit_path}"\n[sessions]\npath = "{tmp_path / "sessions"}"\n',
    )
    assert CliRunner().invoke(app, ["plan", "Prepare a short plan."]).exit_code == 0

    result = CliRunner().invoke(app, ["audit", "verify"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["events"] > 0
    assert payload["final_hash"].startswith("sha256:")


def test_plan_scaffold() -> None:
    result = CliRunner().invoke(app, ["plan", "Draft a rollout plan"])
    assert result.exit_code == 0
    assert "Loro plan mode completed" in result.stdout


def test_plan_with_explicit_tool_call(tmp_path, monkeypatch) -> None:
    note = tmp_path / "note.txt"
    note.write_text("hello from tool\n", encoding="utf-8")
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[sessions]\npath = "{tmp_path / "sessions"}"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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
        f'[sessions]\npath = "{tmp_path / "sessions"}"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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
    monkeypatch.setattr("loro.sandbox.SandboxRunner.run", fake_sandbox_run(commands, "events\n"))
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[polaris]\n"
        "enabled = true\n"
        'cli_path = "polaris"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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


def test_governed_data_ask_policy_prompts_before_execution(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("loro.sandbox.SandboxRunner.run", fake_sandbox_run(commands, "prod\n"))
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[polaris]\n"
        "enabled = true\n"
        'cli_path = "polaris"\n'
        "[permissions]\n"
        'governed_data = "ask"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )

    result = CliRunner().invoke(app, ["data", "catalogs"], input="once\n")

    assert result.exit_code == 0
    assert "Approval required" in result.stdout
    assert commands == [["polaris", "catalogs", "list"]]


def test_data_applicable_policies_typed_command(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("loro.sandbox.SandboxRunner.run", fake_sandbox_run(commands, "pii-mask\n"))
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[polaris]\n"
        "enabled = true\n"
        'cli_path = "polaris"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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


def test_data_schema_command(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        "loro.sandbox.SandboxRunner.run",
        fake_sandbox_run(commands, '{"columns": []}\n'),
    )
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[polaris]\n"
        "enabled = true\n"
        'cli_path = "polaris"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    result = CliRunner().invoke(
        app,
        ["data", "schema", "events", "--namespace", "analytics", "--catalog", "prod"],
    )
    assert result.exit_code == 0
    assert '"table": "events"' in result.stdout
    assert commands == [
        [
            "polaris",
            "tables",
            "get",
            "--namespace",
            "analytics",
            "--catalog",
            "prod",
            "--",
            "events",
        ]
    ]


def test_data_explain_access_command(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("loro.sandbox.SandboxRunner.run", fake_sandbox_run(commands, "ok\n"))
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[polaris]\n"
        "enabled = true\n"
        'cli_path = "polaris"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    result = CliRunner().invoke(
        app,
        [
            "data",
            "explain-access",
            "events",
            "--namespace",
            "analytics",
            "--catalog",
            "prod",
            "--catalog-role",
            "reader",
        ],
    )
    assert result.exit_code == 0
    assert "read-only discovery" in result.stdout
    assert commands == [
        [
            "polaris",
            "tables",
            "get",
            "--namespace",
            "analytics",
            "--catalog",
            "prod",
            "--",
            "events",
        ],
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
        ],
        [
            "polaris",
            "privileges",
            "list",
            "--catalog-role",
            "reader",
            "--catalog",
            "prod",
        ],
    ]


def test_shell_run_requires_yes() -> None:
    result = CliRunner().invoke(app, ["shell", "run", "--", "python", "-c", "print('loro')"])
    assert result.exit_code != 0
    assert "Approval required" in result.stdout


def test_shell_run_with_yes() -> None:
    result = CliRunner().invoke(
        app,
        ["shell", "run", "--yes", "--", "python", "-c", "print('loro')"],
    )
    assert result.exit_code == 0
    assert "loro" in result.stdout


def test_shell_run_interactive_approval_is_audited(tmp_path, monkeypatch) -> None:
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{audit_path}"\n',
    )

    result = CliRunner().invoke(
        app,
        ["shell", "run", "--", "python", "-c", "print('approved')"],
        input="once\n",
    )

    assert result.exit_code == 0
    assert "Approval required" in result.stdout
    assert "approved" in result.stdout
    events = [json.loads(line)["event_type"] for line in audit_path.read_text().splitlines()]
    assert events[:3] == ["approval.requested", "approval.granted", "approval.used"]
    assert events[-1] == "shell.executed"


def test_shell_non_interactive_approval_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[approvals]\nallow_non_interactive = false\n",
    )

    result = CliRunner().invoke(
        app,
        ["shell", "run", "--yes", "--", "python", "-c", "print('blocked')"],
    )

    assert result.exit_code != 0
    assert "Non-interactive approvals are disabled" in result.stderr


def test_shared_memory_schema_command() -> None:
    result = CliRunner().invoke(app, ["memory", "schema", "--backend", "iceberg"])
    assert result.exit_code == 0
    assert "USING iceberg" in result.stdout


def test_shared_memory_schema_command_uses_iceberg_config(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[memory.shared]\niceberg_namespace = "enterprise_memory"\niceberg_table = "agent_facts"\n',
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
    monkeypatch.setattr(
        "loro.memory.iceberg.IcebergSharedMemoryStore.check",
        lambda self: SharedMemoryBackendCheck(
            backend="iceberg",
            ok=False,
            messages=[
                f"Iceberg memory table: {self.memory_table}",
                "pyiceberg is not installed. Install the data extra.",
            ],
        ),
    )
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[memory.shared]\nbackend = "iceberg"\niceberg_table = "agent_facts"\n',
    )
    result = CliRunner().invoke(app, ["memory", "backend-check"])
    assert result.exit_code == 1
    assert "agent_memory.agent_facts" in result.stdout
    assert "pyiceberg is not installed" in result.stdout


def test_shared_memory_draft_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[memory.local]\npath = "{tmp_path / "memory"}"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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
        f'[memory.local]\npath = "{tmp_path / "memory"}"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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


def test_shared_memory_commit_draft_reports_iceberg_readiness_error(
    tmp_path,
    monkeypatch,
) -> None:
    def missing_catalog(_self):
        raise RuntimeError("pyiceberg is required for Iceberg shared memory access.")

    monkeypatch.setattr(
        "loro.memory.iceberg.IcebergSharedMemoryStore._load_catalog",
        missing_catalog,
    )
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[memory.local]\npath = "{tmp_path / "memory"}"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n'
        '[memory.shared]\nbackend = "iceberg"\n',
    )
    runner = CliRunner()
    remember_result = runner.invoke(
        app,
        ["remember", "--shared", "Use the enterprise launch readiness template"],
    )
    assert remember_result.exit_code == 0
    draft_id = remember_result.stdout.splitlines()[0].split(": ", maxsplit=1)[1]
    commit_result = runner.invoke(
        app,
        ["memory", "commit-draft", draft_id, "--execute", "--yes"],
    )
    assert commit_result.exit_code != 0
    assert "required for Iceberg shared memory" in commit_result.stderr


def test_shared_memory_search_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    result = CliRunner().invoke(
        app,
        ["memory", "shared-search", "launch", "--tenant-id", "acme", "--dry-run"],
    )
    assert result.exit_code == 0
    assert '"backend": "postgres"' in result.stdout
    assert "FROM public.shared_memories" in result.stdout


def test_memory_migrate_renders_versioned_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    result = CliRunner().invoke(app, ["memory", "migrate", "--target", "2"])

    assert result.exit_code == 0
    assert "1: shared_memory_baseline" in result.stdout
    assert "2: idempotent_operation_ids" in result.stdout
    assert "render only" in result.stdout


def test_operations_backup_is_dry_run_without_execute(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    result = CliRunner().invoke(
        app,
        ["operations", "backup", "--output", str(tmp_path / "memory.dump")],
    )

    assert result.exit_code == 0
    assert '"execute": false' in result.stdout
    assert '"rpo_seconds": 300' in result.stdout


def test_shared_memory_lifecycle_accepts_release_hold_alias(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    result = CliRunner().invoke(
        app,
        [
            "memory",
            "lifecycle",
            "memory-1",
            "--action",
            "release-hold",
            "--reason",
            "approved release",
            "--tenant-id",
            "acme",
            "--operation-id",
            "12345678-1234-5678-1234-567812345678",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["action"] == "release_hold"
    assert payload["operation_id"] == "12345678-1234-5678-1234-567812345678"
    assert "legal_hold = FALSE" in payload["sql"]


def test_shared_memory_lifecycle_rejects_invalid_operation_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "lifecycle",
            "memory-1",
            "--action",
            "hold",
            "--reason",
            "legal request",
            "--operation-id",
            "not-a-uuid",
        ],
    )

    assert result.exit_code != 0
    assert "operation-id must be a UUID" in result.stderr


def test_memory_proposal_accepts_local(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[memory.local]\npath = "{tmp_path / "memory"}"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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
        f'[memory.local]\npath = "{tmp_path / "memory"}"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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


def test_shared_memory_search_rejects_cross_tenant_in_identity_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'''[identity]
tenant = "acme"
[memory.local]
path = "{tmp_path / "memory"}"
[memory.shared]
enabled = true
tenant_isolation = "identity"
[audit]
enabled = false
''',
    )

    result = CliRunner().invoke(
        app,
        ["memory", "shared-search", "launch", "--tenant-id", "other", "--dry-run"],
    )

    assert result.exit_code != 0
    assert "Cross-tenant" in result.stderr


def test_memory_write_blocks_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[memory.local]\npath = "{tmp_path / "memory"}"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    result = CliRunner().invoke(app, ["remember", "--local", "token = 'abc123456789'"])
    assert result.exit_code != 0
    assert "Sensitive content detected" in result.stderr


def test_memory_write_allows_secret_with_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[memory.local]\npath = "{tmp_path / "memory"}"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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
    assert "inference-api.nousresearch.com" in result.stdout


def test_configure_non_interactive(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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


def test_configure_new_project_writes_strict_ready_local_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    runner = CliRunner()

    configured = runner.invoke(app, ["configure", "--provider", "mock"])
    checked = runner.invoke(app, ["config", "check", "--strict", "--json"])

    assert configured.exit_code == 0, configured.stdout
    assert checked.exit_code == 0, checked.stdout
    payload = tomllib.loads((tmp_path / ".loro/config.local.toml").read_text())
    assert payload["permissions"]["workspace_roots"] == [str(tmp_path)]
    assert "*" not in payload["sandbox"]["profiles"]["controlled-shell"][
        "allowed_executables"
    ]
    assert "*" not in payload["sandbox"]["profiles"]["mcp-stdio"]["allowed_executables"]


def test_configure_existing_file_preserves_policy_sections(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = tmp_path / ".loro/config.local.toml"
    output.parent.mkdir()
    output.write_text(
        '[permissions]\ndefault = "deny"\nworkspace_roots = ["/managed/workspace"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )

    result = CliRunner().invoke(app, ["configure", "--provider", "mock"])

    assert result.exit_code == 0, result.stdout
    payload = tomllib.loads(output.read_text(encoding="utf-8"))
    assert payload["permissions"] == {
        "default": "deny",
        "workspace_roots": ["/managed/workspace"],
    }


def test_configure_rolls_back_when_required_audit_delivery_fails(tmp_path, monkeypatch) -> None:
    class FailingAudit:
        def write(self, *args, **kwargs):
            raise AuditDeliveryError("audit path is unwritable")

    output = tmp_path / "config.local.toml"
    original = b'[model]\nprovider = "mock"\nmodel = "original"\n'
    output.write_bytes(original)
    monkeypatch.setattr("loro.cli._audit", lambda: FailingAudit())

    result = CliRunner().invoke(
        app,
        ["configure", "--provider", "ollama", "--output", str(output)],
    )

    assert result.exit_code == 2
    assert "rolled back" in result.stderr
    assert output.read_bytes() == original


def test_configure_removes_new_file_when_required_audit_delivery_fails(
    tmp_path, monkeypatch
) -> None:
    class FailingAudit:
        def write(self, *args, **kwargs):
            raise AuditDeliveryError("audit path is unwritable")

    output = tmp_path / "config.local.toml"
    monkeypatch.setattr("loro.cli._audit", lambda: FailingAudit())

    result = CliRunner().invoke(
        app,
        ["configure", "--provider", "mock", "--output", str(output)],
    )

    assert result.exit_code == 2
    assert "rolled back" in result.stderr
    assert not output.exists()


def test_artifacts_verify_cli_detects_mutation(tmp_path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("original", encoding="utf-8")
    from loro.artifacts.common import ArtifactResult, write_provenance

    provenance = write_provenance(
        result=ArtifactResult("Artifact", "document", [artifact], "created"),
        prompt_preview="artifact",
    )
    runner = CliRunner()

    valid = runner.invoke(app, ["artifacts", "verify", str(provenance)])
    artifact.write_text("changed", encoding="utf-8")
    invalid = runner.invoke(app, ["artifacts", "verify", str(provenance)])

    assert valid.exit_code == 0, valid.stdout
    assert json.loads(valid.stdout)["ok"] is True
    assert invalid.exit_code == 1
    assert json.loads(invalid.stdout)["ok"] is False


def test_configure_with_provider_alias(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
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


def test_setup_memory_preserves_provider_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    output = tmp_path / "config.local.toml"
    runner = CliRunner()
    provider_result = runner.invoke(
        app,
        [
            "setup",
            "provider",
            "--provider",
            "ollama",
            "--model",
            "llama3.2",
            "--small-model",
            "llama3.2",
            "--output",
            str(output),
        ],
    )
    assert provider_result.exit_code == 0
    memory_result = runner.invoke(
        app,
        [
            "setup",
            "memory",
            "--enabled",
            "--path",
            str(tmp_path / "memory"),
            "--no-auto-propose",
            "--output",
            str(output),
        ],
    )
    assert memory_result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert 'provider = "ollama"' in text
    assert "[memory.local]" in text
    assert f'path = "{tmp_path / "memory"}"' in text
    assert "auto_propose = false" in text


def test_setup_shared_memory_postgres(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    output = tmp_path / "config.local.toml"
    result = CliRunner().invoke(
        app,
        [
            "setup",
            "shared-memory",
            "--enabled",
            "--backend",
            "postgres",
            "--postgres-dsn-env",
            "TEST_LORO_POSTGRES_DSN",
            "--postgres-schema",
            "agent_memory",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert "[memory.shared]" in text
    assert 'backend = "postgres"' in text
    assert 'postgres_dsn_env = "TEST_LORO_POSTGRES_DSN"' in text
    assert 'postgres_schema = "agent_memory"' in text
    assert "explicit-only" in result.stdout


def test_setup_shared_memory_iceberg(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    output = tmp_path / "config.local.toml"
    result = CliRunner().invoke(
        app,
        [
            "setup",
            "shared-memory",
            "--enabled",
            "--backend",
            "iceberg",
            "--iceberg-catalog-name",
            "polaris",
            "--iceberg-catalog-uri-env",
            "TEST_ICEBERG_URI",
            "--iceberg-credential-env",
            "TEST_ICEBERG_CREDENTIAL",
            "--iceberg-token-env",
            "TEST_ICEBERG_TOKEN",
            "--iceberg-warehouse",
            "quickstart_catalog",
            "--iceberg-namespace",
            "agent_memory",
            "--iceberg-table",
            "shared_memories",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert 'backend = "iceberg"' in text
    assert 'iceberg_catalog_name = "polaris"' in text
    assert 'iceberg_warehouse = "quickstart_catalog"' in text


def test_setup_polaris(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    output = tmp_path / "config.local.toml"
    result = CliRunner().invoke(
        app,
        [
            "setup",
            "polaris",
            "--enabled",
            "--cli-path",
            "/usr/local/bin/polaris",
            "--realm",
            "quickstart",
            "--catalog",
            "quickstart_catalog",
            "--no-require-role-inspection",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert "[polaris]" in text
    assert 'cli_path = "/usr/local/bin/polaris"' in text
    assert 'realm = "quickstart"' in text
    assert 'catalog = "quickstart_catalog"' in text
    assert "require_role_inspection = false" in text


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


def test_providers_smoke_dry_run() -> None:
    result = CliRunner().invoke(app, ["providers", "smoke", "hello", "--provider", "mock"])
    assert result.exit_code == 0
    assert '"execute": false' in result.stdout
    assert "mock://local" in result.stdout


def test_providers_smoke_execute_mock() -> None:
    result = CliRunner().invoke(
        app,
        ["providers", "smoke", "hello", "--provider", "mock", "--execute", "--stream"],
    )
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert "Mock response for" in result.stdout


def test_identity_show_uses_environment(monkeypatch) -> None:
    monkeypatch.setenv("LORO_IDENTITY_SUBJECT", "user-123")
    monkeypatch.setenv("LORO_IDENTITY_ORGANIZATION", "acme")
    monkeypatch.setenv("LORO_IDENTITY_TENANT", "platform")
    monkeypatch.setenv("LORO_IDENTITY_ROLES", "developer,memory-reader")

    result = CliRunner().invoke(app, ["identity", "show"])

    assert result.exit_code == 0
    assert '"subject": "user-123"' in result.stdout
    assert '"tenant": "platform"' in result.stdout
    assert '"memory-reader"' in result.stdout


def test_identity_doctor_reports_missing_required_fields(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[identity]\nenvironment_enabled = false\nrequired_fields = ["organization"]\n',
    )

    result = CliRunner().invoke(app, ["identity", "doctor"])

    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"organization"' in result.stdout


def test_runtime_fails_closed_when_identity_is_incomplete(monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        '[identity]\nenvironment_enabled = false\nrequired_fields = ["organization"]\n',
    )

    result = CliRunner().invoke(app, ["plan", "Draft a plan"])

    assert result.exit_code != 0
    assert "Required identity fields are missing: organization" in result.stderr


def test_setup_identity_writes_configuration(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    output = tmp_path / "config.local.toml"
    result = CliRunner().invoke(
        app,
        [
            "setup",
            "identity",
            "--subject",
            "user-123",
            "--organization",
            "acme",
            "--tenant",
            "platform",
            "--groups",
            "engineering,data-platform",
            "--roles",
            "developer",
            "--auth-method",
            "oidc",
            "--source",
            "managed-env",
            "--required-fields",
            "subject,organization,tenant",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert "[identity]" in text
    assert 'subject = "user-123"' in text
    assert 'organization = "acme"' in text
    assert "required_fields = [" in text


def test_setup_identity_can_remediate_missing_required_field(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[identity]\n"
        "environment_enabled = false\n"
        'required_fields = ["organization"]\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    output = tmp_path / "config.local.toml"

    result = CliRunner().invoke(
        app,
        ["setup", "identity", "--organization", "acme", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert 'organization = "acme"' in output.read_text(encoding="utf-8")


def test_setup_approvals_writes_configuration(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )
    output = tmp_path / "config.local.toml"

    result = CliRunner().invoke(
        app,
        [
            "setup",
            "approvals",
            "--interactive",
            "--deny-non-interactive",
            "--allow-session-scope",
            "--once-ttl",
            "60",
            "--session-ttl",
            "300",
            "--store",
            "json",
            "--store-path",
            str(tmp_path / "approvals.json"),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert "[approvals]" in text
    assert "interactive = true" in text
    assert "allow_non_interactive = false" in text
    assert "once_ttl_seconds = 60" in text
    assert 'store = "json"' in text
    assert f'store_path = "{tmp_path / "approvals.json"}"' in text


def test_setup_audit_writes_external_sink_configuration(tmp_path, monkeypatch) -> None:
    output = tmp_path / "config.toml"
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[audit]\npath = "{tmp_path / "setup-audit.jsonl"}"\n',
    )

    result = CliRunner().invoke(
        app,
        [
            "setup",
            "audit",
            "--sink",
            "http",
            "--http-url",
            "https://audit.example/events",
            "--http-token-env",
            "LORO_AUDIT_TOKEN",
            "--failure-mode",
            "fail",
            "--buffer-path",
            str(tmp_path / "buffer.jsonl"),
            "--max-buffer-events",
            "500",
            "--max-retries",
            "3",
            "--backoff-seconds",
            "0.5",
            "--timeout-seconds",
            "15",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert 'sink = "http"' in text
    assert 'http_url = "https://audit.example/events"' in text
    assert 'http_token_env = "LORO_AUDIT_TOKEN"' in text
    assert 'failure_mode = "fail"' in text
    assert "max_buffer_events = 500" in text


def test_shared_memory_defaults_to_active_identity(tmp_path, monkeypatch) -> None:
    memory_path = tmp_path / "memory"
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[memory.local]\npath = "{memory_path}"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n'
        "[identity]\n"
        'subject = "user-123"\n'
        'tenant = "platform"\n',
    )

    result = CliRunner().invoke(app, ["remember", "--shared", "Use approved templates"])

    assert result.exit_code == 0
    draft = json.loads((memory_path / "shared-memory-drafts.jsonl").read_text(encoding="utf-8"))
    assert draft["tenant_id"] == "platform"
    assert draft["created_by"] == "user-123"
