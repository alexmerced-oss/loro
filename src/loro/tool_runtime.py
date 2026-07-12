import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loro.config import LoroConfig
from loro.permissions import PermissionEngine, PermissionRequest
from loro.tools.files import FileTools


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolExecution:
    call: ToolCall
    ok: bool
    output: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "tool": self.call.name,
            "args": self.call.args,
            "ok": self.ok,
            "output": self.output,
        }


class ToolRegistry:
    """Executes explicit, typed tool calls for the runtime loop."""

    def __init__(self, config: LoroConfig) -> None:
        self.config = config
        self.permissions = PermissionEngine(config.permissions)
        self.files = FileTools()

    def execute(self, call: ToolCall) -> ToolExecution:
        try:
            if call.name == "file.read":
                return self._read_file(call)
            if call.name == "file.search":
                return self._search_files(call)
            return ToolExecution(call=call, ok=False, output=f"Unknown tool: {call.name}")
        except Exception as error:
            return ToolExecution(call=call, ok=False, output=str(error))

    def _read_file(self, call: ToolCall) -> ToolExecution:
        path = Path(str(call.args["path"]))
        limit = int(call.args.get("limit", 20000))
        self.permissions.require_allowed(
            PermissionRequest(tool="edit", action="read file", target=str(path)),
            approved=True,
        )
        return ToolExecution(
            call=call,
            ok=True,
            output=self.files.read_text(path, limit=limit),
        )

    def _search_files(self, call: ToolCall) -> ToolExecution:
        query = str(call.args["query"])
        root = Path(str(call.args.get("root", ".")))
        limit = int(call.args.get("limit", 50))
        self.permissions.require_allowed(
            PermissionRequest(tool="edit", action="search files", target=str(root)),
            approved=True,
        )
        matches = self.files.search(root=root, query=query, limit=limit)
        output = "\n".join(
            f"{match.path}:{match.line_number}: {match.line}" for match in matches
        )
        return ToolExecution(call=call, ok=True, output=output or "No matches.")


def parse_tool_calls(prompt: str) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for line in prompt.splitlines():
        stripped = line.strip()
        if not stripped.startswith("@tool "):
            continue
        directive = stripped.removeprefix("@tool ").strip()
        if directive.startswith("{"):
            calls.append(_parse_json_tool_call(directive))
            continue
        name, _, raw_args = directive.partition(" ")
        if not name or not raw_args.strip():
            calls.append(ToolCall(name=name or "unknown", args={}))
            continue
        data = json.loads(raw_args)
        if not isinstance(data, dict):
            raise ValueError("Tool call arguments must be a JSON object.")
        calls.append(ToolCall(name=name, args=data))
    return calls


def _parse_json_tool_call(raw: str) -> ToolCall:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Tool directive must be a JSON object.")
    name = data.get("name")
    args = data.get("args", {})
    if not isinstance(name, str) or not name:
        raise ValueError("Tool directive requires a non-empty string name.")
    if not isinstance(args, dict):
        raise ValueError("Tool directive args must be a JSON object.")
    return ToolCall(name=name, args=args)
