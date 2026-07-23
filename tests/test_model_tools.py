import pytest

from loro.model_tools import (
    ModelToolCallParseError,
    parse_bedrock_tool_calls,
    parse_provider_tool_calls,
)


def test_parse_bedrock_tool_calls() -> None:
    calls = parse_bedrock_tool_calls(
        {
            "output": {
                "message": {
                    "content": [
                        {"text": "looking"},
                        {
                            "toolUse": {
                                "name": "polaris.readonly",
                                "input": {"args": ["catalogs", "list"]},
                            }
                        },
                    ]
                }
            }
        }
    )

    assert len(calls) == 1
    assert calls[0].name == "polaris.readonly"
    assert calls[0].args == {"args": ["catalogs", "list"]}


def test_parse_provider_tool_calls_ignores_unknown_protocol() -> None:
    assert parse_provider_tool_calls("custom", {"whatever": True}) == []


def test_parse_bedrock_tool_calls_rejects_non_object_input() -> None:
    with pytest.raises(ModelToolCallParseError, match="JSON object"):
        parse_bedrock_tool_calls(
            {
                "output": {
                    "message": {
                        "content": [{"toolUse": {"name": "file.read", "input": "nope"}}]
                    }
                }
            }
        )
