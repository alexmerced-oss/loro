import pytest

from loro.config import (
    LocalMemoryConfig,
    LoroConfig,
    MemoryConfig,
    PermissionsConfig,
    PolarisConfig,
)
from loro.memory.local import LocalMemoryStore
from loro.tool_runtime import ToolRegistry, parse_tool_calls


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


def test_tool_registry_unknown_tool() -> None:
    result = ToolRegistry(LoroConfig()).execute(parse_tool_calls("@tool nope {}")[0])
    assert result.ok is False
    assert result.output == "Unknown tool: nope"


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


def test_tool_registry_memory_search(tmp_path) -> None:
    memory_config = LocalMemoryConfig(path=str(tmp_path / "memory"))
    LocalMemoryStore.from_config(memory_config).remember("Launch briefs include risks.")
    call = parse_tool_calls('@tool memory.search {"query": "launch"}')[0]
    result = ToolRegistry(
        LoroConfig(memory=MemoryConfig(local=memory_config))
    ).execute(call)
    assert result.ok is True
    assert "Launch briefs include risks." in result.output


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
