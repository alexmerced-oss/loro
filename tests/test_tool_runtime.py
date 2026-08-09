from subprocess import run

import pytest

from loro.config import (
    ApprovalsConfig,
    LocalMemoryConfig,
    LoroConfig,
    MCPConfig,
    MCPServerConfig,
    MemoryConfig,
    PermissionRuleConfig,
    PermissionsConfig,
    PolarisConfig,
    SharedMemoryConfig,
)
from loro.memory.local import LocalMemoryStore
from loro.tool_runtime import ToolRegistry, parse_tool_calls


class FakeRuntimeMCPService:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.task_updates: list[tuple[str, str, dict[str, object], bool]] = []

    async def list_tools(self, server_id: str):
        self.requests.append(server_id)
        return {
            "connection": {
                "server_id": server_id,
                "transport": "stdio",
                "protocol_version": "2026-07-28",
                "lifecycle": "stateless",
            },
            "tools": [{"name": "echo"}],
        }

    async def update_task(
        self,
        server_id: str,
        task_id: str,
        responses: dict[str, object],
        *,
        user_approved: bool,
    ):
        self.task_updates.append((server_id, task_id, responses, user_approved))
        return {
            "connection": {
                "server_id": server_id,
                "transport": "stdio",
                "protocol_version": "2026-07-28",
                "lifecycle": "stateless",
            },
            "acknowledged": True,
        }


def test_parse_tool_calls() -> None:
    calls = parse_tool_calls(
        'Review this.\n@tool file.read {"path": "README.md", "limit": 20}\nThanks.'
    )
    assert len(calls) == 1
    assert calls[0].name == "file.read"
    assert calls[0].args == {"path": "README.md", "limit": 20}


def test_parse_json_tool_directive() -> None:
    calls = parse_tool_calls(
        '@tool {"name": "file.search", "args": {"query": "Loro", "root": "."}}'
    )
    assert len(calls) == 1
    assert calls[0].name == "file.search"
    assert calls[0].args == {"query": "Loro", "root": "."}


def test_parse_tool_calls_rejects_non_object_args() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_tool_calls('@tool file.read ["README.md"]')


def test_tool_registry_file_read(tmp_path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("hello loro\n", encoding="utf-8")
    call = parse_tool_calls(f'@tool file.read {{"path": "{note}", "limit": 20}}')[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is True
    assert result.output == "hello loro\n"


def test_tool_registry_file_search(tmp_path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("hello loro\nanother line\n", encoding="utf-8")
    call = parse_tool_calls(
        f'@tool file.search {{"root": "{tmp_path}", "query": "loro", "limit": 5}}'
    )[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is True
    assert "note.txt:1: hello loro" in result.output


def test_tool_registry_file_write_requires_approval(tmp_path) -> None:
    target = tmp_path / "note.txt"
    call = parse_tool_calls(f'@tool file.write {{"path": "{target}", "content": "hello"}}')[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is False
    assert "requires approval" in result.output
    assert not target.exists()


def test_tool_registry_file_write_with_approval(tmp_path) -> None:
    target = tmp_path / "note.txt"
    call = parse_tool_calls(
        f'@tool file.write {{"path": "{target}", "content": "hello", "approved": true}}'
    )[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "hello"


def test_model_cannot_self_approve_file_write(tmp_path) -> None:
    target = tmp_path / "model-note.txt"
    call = parse_tool_calls(
        f'@tool file.write {{"path": "{target}", "content": "hello", "approved": true}}',
        origin="model",
    )[0]

    result = ToolRegistry(LoroConfig()).execute(call)

    assert result.ok is False
    assert "Model-provided approval arguments are not trusted" in result.output
    assert not target.exists()


def test_model_action_can_receive_interactive_user_approval(tmp_path) -> None:
    target = tmp_path / "approved-note.txt"
    requests = []
    call = parse_tool_calls(
        f'@tool file.write {{"path": "{target}", "content": "hello", "approved": true}}',
        origin="model",
    )[0]
    registry = ToolRegistry(
        LoroConfig(),
        approval_provider=lambda request: requests.append(request) or "once",
    )

    result = registry.execute(call)

    assert result.ok is True
    assert len(requests) == 1
    assert target.read_text(encoding="utf-8") == "hello"


def test_non_interactive_runtime_approval_can_be_disabled(tmp_path) -> None:
    target = tmp_path / "blocked-note.txt"
    call = parse_tool_calls(
        f'@tool file.write {{"path": "{target}", "content": "hello", "approved": true}}'
    )[0]
    config = LoroConfig(approvals=ApprovalsConfig(allow_non_interactive=False))

    result = ToolRegistry(config).execute(call)

    assert result.ok is False
    assert "Non-interactive approvals are disabled" in result.output
    assert not target.exists()


def test_session_approval_reuses_only_exact_tool_request(tmp_path) -> None:
    target = tmp_path / "session-note.txt"
    requests = []
    registry = ToolRegistry(
        LoroConfig(),
        approval_provider=lambda request: requests.append(request) or "session",
    )
    original = parse_tool_calls(
        f'@tool file.write {{"path": "{target}", "content": "first"}}',
        origin="model",
    )[0]
    changed = parse_tool_calls(
        f'@tool file.write {{"path": "{target}", "content": "second"}}',
        origin="model",
    )[0]

    assert registry.execute(original).ok is True
    assert registry.execute(original).ok is True
    assert registry.execute(changed).ok is True
    assert len(requests) == 2


def test_tool_registry_file_replace_with_approval(tmp_path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("hello loro\n", encoding="utf-8")
    call = parse_tool_calls(
        f'@tool file.replace {{"path": "{target}", "old": "loro", "new": "team", "approved": true}}'
    )[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is True
    assert "replacements: 1" in result.output
    assert target.read_text(encoding="utf-8") == "hello team\n"


def test_tool_registry_file_write_blocks_sensitive_content(tmp_path) -> None:
    target = tmp_path / "secret.txt"
    call = parse_tool_calls(
        f'@tool file.write {{"path": "{target}", '
        '"content": "api_key = abcdefghijklmnop", "approved": true}'
    )[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is False
    assert "Sensitive content detected" in result.output
    assert not target.exists()


def test_tool_registry_unknown_tool() -> None:
    result = ToolRegistry(LoroConfig()).execute(parse_tool_calls("@tool nope {}")[0])
    assert result.ok is False
    assert result.output == "Unknown tool: nope"


def test_tool_registry_mcp_discovery_uses_policy_and_connection_metadata() -> None:
    service = FakeRuntimeMCPService()
    config = LoroConfig(
        mcp=MCPConfig(
            enabled=True,
            servers={"fixture": MCPServerConfig(command="fixture-server")},
        )
    )
    call = parse_tool_calls('@tool mcp.tools {"server_id":"fixture","approved":true}')[0]

    result = ToolRegistry(config, mcp_service=service).execute(call)

    assert result.ok is True
    assert service.requests == ["fixture"]
    assert result.metadata["mcp_protocol_version"] == "2026-07-28"
    assert '"name": "echo"' in result.output


def test_model_cannot_self_approve_mcp_operation() -> None:
    service = FakeRuntimeMCPService()
    config = LoroConfig(
        mcp=MCPConfig(
            enabled=True,
            servers={"fixture": MCPServerConfig(command="fixture-server")},
        )
    )
    call = parse_tool_calls(
        '@tool mcp.tools {"server_id":"fixture","approved":true}', origin="model"
    )[0]

    result = ToolRegistry(config, mcp_service=service).execute(call)

    assert result.ok is False
    assert "Model-provided approval arguments are not trusted" in result.output
    assert service.requests == []


def test_user_can_explicitly_approve_mcp_task_input() -> None:
    service = FakeRuntimeMCPService()
    config = LoroConfig(
        mcp=MCPConfig(
            enabled=True,
            servers={"fixture": MCPServerConfig(command="fixture-server")},
        )
    )
    call = parse_tool_calls(
        '@tool mcp.task_update {"server_id":"fixture","task_id":"task-1",'
        '"responses":{"format":"pptx"},"approved":true}'
    )[0]

    result = ToolRegistry(config, mcp_service=service).execute(call)

    assert result.ok is True
    assert service.task_updates == [("fixture", "task-1", {"format": "pptx"}, True)]


def test_model_cannot_self_approve_mcp_task_input() -> None:
    service = FakeRuntimeMCPService()
    config = LoroConfig(
        mcp=MCPConfig(
            enabled=True,
            servers={"fixture": MCPServerConfig(command="fixture-server")},
        )
    )
    call = parse_tool_calls(
        '@tool mcp.task_update {"server_id":"fixture","task_id":"task-1",'
        '"responses":{"format":"pptx"},"approved":true}',
        origin="model",
    )[0]

    result = ToolRegistry(config, mcp_service=service).execute(call)

    assert result.ok is False
    assert "Model-provided approval arguments are not trusted" in result.output
    assert service.task_updates == []


def test_tool_registry_shell_run_requires_approval() -> None:
    call = parse_tool_calls('@tool shell.run {"args": ["python", "-c", "print(123)"]}')[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is False
    assert "requires approval" in result.output


def test_tool_registry_shell_run_with_approval() -> None:
    call = parse_tool_calls(
        '@tool shell.run {"args": ["python", "-c", "print(123)"], "approved": true}'
    )[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is True
    assert "returncode: 0" in result.output
    assert "123" in result.output


def test_tool_registry_shell_run_respects_deny() -> None:
    call = parse_tool_calls(
        '@tool shell.run {"args": ["python", "-c", "print(123)"], "approved": true}'
    )[0]
    result = ToolRegistry(LoroConfig(permissions=PermissionsConfig(shell="deny"))).execute(call)
    assert result.ok is False
    assert "denied by policy" in result.output


def test_tool_registry_shell_structured_rule_blocks_absolute_executable() -> None:
    call = parse_tool_calls(
        '@tool shell.run {"args": ["/usr/bin/python3", "-c", "print(123)"], "approved": true}'
    )[0]
    config = LoroConfig(
        permissions=PermissionsConfig(
            shell="allow",
            rules=[
                PermissionRuleConfig(
                    tool="shell",
                    action="run*",
                    resource_kind="shell",
                    resource={"executable_name": "python3"},
                    decision="deny",
                )
            ],
        )
    )

    result = ToolRegistry(config).execute(call)

    assert result.ok is False
    assert "denied by policy" in result.output


def test_tool_registry_rejects_symlink_outside_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (workspace / "linked").symlink_to(outside, target_is_directory=True)
    call = parse_tool_calls(f'@tool file.read {{"path": "{workspace / "linked" / "secret.txt"}"}}')[
        0
    ]
    config = LoroConfig(permissions=PermissionsConfig(workspace_roots=[str(workspace)]))

    result = ToolRegistry(config).execute(call)

    assert result.ok is False
    assert "outside configured workspace roots" in result.output


def test_tool_registry_git_status(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    call = parse_tool_calls(f'@tool git.status {{"cwd": "{tmp_path}"}}')[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is True
    assert "?? note.txt" in result.output


def test_tool_registry_git_add_requires_approval(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    call = parse_tool_calls(f'@tool git.add {{"cwd": "{tmp_path}", "paths": ["note.txt"]}}')[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is False
    assert "requires approval" in result.output


def test_tool_registry_git_add_and_commit_with_approval(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    registry = ToolRegistry(LoroConfig())

    add_call = parse_tool_calls(
        f'@tool git.add {{"cwd": "{tmp_path}", "paths": ["note.txt"], "approved": true}}'
    )[0]
    add_result = registry.execute(add_call)
    assert add_result.ok is True

    commit_call = parse_tool_calls(
        f'@tool git.commit {{"cwd": "{tmp_path}", "message": "Add note", "approved": true}}'
    )[0]
    commit_result = registry.execute(commit_call)
    assert commit_result.ok is True
    assert "Add note" in commit_result.output

    show_call = parse_tool_calls(f'@tool git.show {{"cwd": "{tmp_path}"}}')[0]
    show_result = registry.execute(show_call)
    assert show_result.ok is True
    assert "Add note" in show_result.output


def test_tool_registry_memory_search(tmp_path) -> None:
    memory_config = LocalMemoryConfig(path=str(tmp_path / "memory"))
    LocalMemoryStore.from_config(memory_config).remember("Launch briefs include risks.")
    call = parse_tool_calls('@tool memory.search {"query": "launch"}')[0]
    result = ToolRegistry(LoroConfig(memory=MemoryConfig(local=memory_config))).execute(call)
    assert result.ok is True
    assert "Launch briefs include risks." in result.output


def test_tool_registry_shared_memory_search_renders_sql() -> None:
    call = parse_tool_calls('@tool memory.shared_search {"query": "launch", "execute": false}')[0]
    result = ToolRegistry(
        LoroConfig(memory=MemoryConfig(shared=SharedMemoryConfig(enabled=True)))
    ).execute(call)
    assert result.ok is False
    assert "FROM public.shared_memories" in result.output


def test_tool_registry_polaris_readonly(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, capture_output, text, check):
        commands.append(command)
        from subprocess import CompletedProcess

        return CompletedProcess(command, 0, stdout="prod\n", stderr="")

    monkeypatch.setattr("loro.polaris.run", fake_run)
    call = parse_tool_calls('@tool polaris.readonly {"args": ["catalogs", "list"]}')[0]
    result = ToolRegistry(
        LoroConfig(polaris=PolarisConfig(enabled=True, cli_path="polaris"))
    ).execute(call)
    assert result.ok is True
    assert "prod" in result.output
    assert commands == [["polaris", "catalogs", "list"]]


def test_tool_registry_polaris_readonly_rejects_mutation() -> None:
    call = parse_tool_calls('@tool polaris.readonly {"args": ["catalogs", "create", "prod"]}')[0]
    result = ToolRegistry(
        LoroConfig(polaris=PolarisConfig(enabled=True, cli_path="polaris"))
    ).execute(call)
    assert result.ok is False
    assert "not read-only" in result.output


def test_tool_registry_artifact_create_document(tmp_path) -> None:
    call = parse_tool_calls(
        "@tool artifact.create "
        f'{{"kind": "document", "prompt": "Draft onboarding guide", '
        f'"output_dir": "{tmp_path}"}}'
    )[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is True
    assert "Created document artifacts" in result.output
    assert "provenance:" in result.output
    assert any(path.suffix == ".docx" for path in tmp_path.iterdir())
    assert any(path.name.endswith(".provenance.json") for path in tmp_path.iterdir())


def test_tool_registry_artifact_create_brief(tmp_path) -> None:
    call = parse_tool_calls(
        "@tool artifact.create "
        f'{{"kind": "brief", "brief_type": "executive", '
        f'"prompt": "Summarize launch readiness", "output_dir": "{tmp_path}"}}'
    )[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is True
    assert "Created executive brief artifact" in result.output
    assert any(path.suffix == ".md" for path in tmp_path.iterdir())


def test_tool_registry_artifact_create_rejects_unknown_kind(tmp_path) -> None:
    call = parse_tool_calls(
        "@tool artifact.create "
        f'{{"kind": "video", "prompt": "Make a clip", "output_dir": "{tmp_path}"}}'
    )[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is False
    assert "kind must be one of" in result.output


def test_tool_registry_artifact_create_blocks_sensitive_prompt(tmp_path) -> None:
    call = parse_tool_calls(
        "@tool artifact.create "
        f'{{"kind": "document", "prompt": "api_key = abcdefghijklmnop", '
        f'"output_dir": "{tmp_path}"}}'
    )[0]
    result = ToolRegistry(LoroConfig()).execute(call)
    assert result.ok is False
    assert "Sensitive content detected" in result.output
    assert not any(tmp_path.iterdir())


def _init_repo(path) -> None:
    result = run(["git", "init"], cwd=path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
