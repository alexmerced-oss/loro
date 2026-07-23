import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelToolCall:
    name: str
    args: dict[str, Any]


class ModelToolCallParseError(ValueError):
    """Raised when a provider returns malformed native tool-call data."""


def parse_provider_tool_calls(protocol: str, payload: dict[str, Any]) -> list[ModelToolCall]:
    if protocol == "anthropic":
        return parse_anthropic_tool_calls(payload)
    if protocol == "gemini":
        return parse_gemini_tool_calls(payload)
    if protocol == "bedrock":
        return parse_bedrock_tool_calls(payload)
    if protocol == "openai-compatible":
        return parse_openai_tool_calls(payload)
    return []


def parse_openai_tool_calls(payload: dict[str, Any]) -> list[ModelToolCall]:
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return []
    if not isinstance(message, dict):
        return []
    raw_calls = message.get("tool_calls", [])
    if raw_calls is None:
        return []
    if not isinstance(raw_calls, list):
        raise ModelToolCallParseError("OpenAI tool_calls must be a list.")
    calls: list[ModelToolCall] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise ModelToolCallParseError("OpenAI tool call entries must be objects.")
        function = raw_call.get("function", {})
        if not isinstance(function, dict):
            raise ModelToolCallParseError("OpenAI tool call function must be an object.")
        name = _string_name(function.get("name"), "OpenAI tool call function.name")
        calls.append(ModelToolCall(name=name, args=_json_args(function.get("arguments"))))
    return calls


def parse_anthropic_tool_calls(payload: dict[str, Any]) -> list[ModelToolCall]:
    blocks = payload.get("content", [])
    if not isinstance(blocks, list):
        return []
    calls: list[ModelToolCall] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = _string_name(block.get("name"), "Anthropic tool_use.name")
        calls.append(ModelToolCall(name=name, args=_object_args(block.get("input", {}))))
    return calls


def parse_gemini_tool_calls(payload: dict[str, Any]) -> list[ModelToolCall]:
    calls: list[ModelToolCall] = []
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        return calls
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content", {})
        if not isinstance(content, dict):
            continue
        parts = content.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or "functionCall" not in part:
                continue
            function_call = part["functionCall"]
            if not isinstance(function_call, dict):
                raise ModelToolCallParseError("Gemini functionCall must be an object.")
            name = _string_name(function_call.get("name"), "Gemini functionCall.name")
            calls.append(ModelToolCall(name=name, args=_object_args(function_call.get("args", {}))))
    return calls


def parse_bedrock_tool_calls(payload: dict[str, Any]) -> list[ModelToolCall]:
    try:
        blocks = payload["output"]["message"]["content"]
    except (KeyError, TypeError):
        return []
    if not isinstance(blocks, list):
        return []
    calls: list[ModelToolCall] = []
    for block in blocks:
        if not isinstance(block, dict) or "toolUse" not in block:
            continue
        tool_use = block["toolUse"]
        if not isinstance(tool_use, dict):
            raise ModelToolCallParseError("Bedrock toolUse must be an object.")
        name = _string_name(tool_use.get("name"), "Bedrock toolUse.name")
        calls.append(ModelToolCall(name=name, args=_object_args(tool_use.get("input", {}))))
    return calls


def _string_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModelToolCallParseError(f"{field} must be a non-empty string.")
    return value


def _json_args(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ModelToolCallParseError("Tool-call arguments must be a JSON object or string.")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ModelToolCallParseError("Tool-call arguments are not valid JSON.") from error
    return _object_args(parsed)


def _object_args(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelToolCallParseError("Tool-call arguments must be a JSON object.")
    return value
