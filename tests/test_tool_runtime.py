from loro.config import LoroConfig
from loro.tool_runtime import ToolRegistry, parse_tool_calls


def test_parse_tool_calls() -> None:
    calls = parse_tool_calls(
        'Review this.\n@tool file.read {"path": "README.md", "limit": 20}\nThanks.'
    )
    assert len(calls) == 1
    assert calls[0].name == "file.read"
    assert calls[0].args == {"path": "README.md", "limit": 20}


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
