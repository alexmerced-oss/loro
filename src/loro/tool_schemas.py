"""Typed tool-schema catalog published to model providers.

`models.py` has always parsed native provider tool calls, but the runtime never sent the
provider a description of the tools, so a real model could only invoke a tool by emitting
Loro's textual ``@tool {json}`` directive. This module declares the schemas once and
renders them into each provider's wire format.

The textual DSL remains fully supported: it is the deterministic path used by tests and
by providers with no native tool-calling support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loro.config import LoroConfig

__all__ = [
    "ToolSchema",
    "canonical_tool_name",
    "anthropic_tools",
    "bedrock_tool_config",
    "gemini_tools",
    "openai_tools",
    "provider_tool_name",
    "provider_tool_payload",
    "tool_catalog",
]


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


def _object(
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_BOOLEAN = {"type": "boolean"}

BUILTIN_TOOL_SCHEMAS: tuple[ToolSchema, ...] = (
    ToolSchema(
        name="file.read",
        description="Read a UTF-8 text file inside the configured workspace roots.",
        parameters=_object(
            {
                "path": {**_STRING, "description": "Workspace-relative or absolute file path."},
                "limit": {**_INTEGER, "description": "Maximum characters to return."},
            },
            ["path"],
        ),
    ),
    ToolSchema(
        name="file.search",
        description="Case-insensitive substring search across text files under a directory.",
        parameters=_object(
            {
                "root": {**_STRING, "description": "Directory to search."},
                "query": {**_STRING, "description": "Substring to look for."},
                "limit": {**_INTEGER, "description": "Maximum matches to return."},
            },
            ["query"],
        ),
    ),
    ToolSchema(
        name="file.write",
        description="Write or append UTF-8 text to a file. Requires policy approval.",
        parameters=_object(
            {
                "path": _STRING,
                "content": _STRING,
                "append": {**_BOOLEAN, "description": "Append instead of overwriting."},
            },
            ["path", "content"],
        ),
    ),
    ToolSchema(
        name="file.replace",
        description="Replace exact text within an existing file. Requires policy approval.",
        parameters=_object(
            {
                "path": _STRING,
                "old": _STRING,
                "new": _STRING,
                "count": {**_INTEGER, "description": "Replacements to make; -1 for all."},
            },
            ["path", "old", "new"],
        ),
    ),
    ToolSchema(
        name="shell.run",
        description="Run an allowlisted command in a sandbox profile. Requires policy approval.",
        parameters=_object(
            {
                "args": {
                    "type": "array",
                    "items": _STRING,
                    "description": "Argument vector; no shell interpretation is performed.",
                },
                "cwd": _STRING,
            },
            ["args"],
        ),
    ),
    ToolSchema(
        name="git.status",
        description="Show the working tree status of a repository in the workspace.",
        parameters=_object({"cwd": _STRING}),
    ),
    ToolSchema(
        name="git.diff",
        description="Show the working tree diff of a repository in the workspace.",
        parameters=_object({"cwd": _STRING, "paths": {"type": "array", "items": _STRING}}),
    ),
    ToolSchema(
        name="memory.search",
        description="Search this agent's local memory records.",
        parameters=_object({"query": _STRING, "limit": _INTEGER}, ["query"]),
    ),
    ToolSchema(
        name="memory.shared_search",
        description="Search governed shared memory for the caller's tenant.",
        parameters=_object({"query": _STRING, "limit": _INTEGER}, ["query"]),
    ),
    ToolSchema(
        name="polaris.readonly",
        description="Run a read-only Apache Polaris catalog metadata command.",
        parameters=_object(
            {
                "args": {
                    "type": "array",
                    "items": _STRING,
                    "description": "Polaris argv, e.g. ['tables', 'list', '--catalog', 'prod'].",
                }
            },
            ["args"],
        ),
    ),
    ToolSchema(
        name="artifact.create",
        description="Generate a document, presentation, spreadsheet, or brief artifact.",
        parameters=_object(
            {
                "kind": {
                    "type": "string",
                    "enum": ["document", "presentation", "spreadsheet", "brief"],
                },
                "prompt": _STRING,
                "output_dir": _STRING,
                "brief_type": _STRING,
            },
            ["kind", "prompt"],
        ),
    ),
    ToolSchema(
        name="graph.emit_output",
        description="Emit a named output value for the current Agentic Graph node.",
        parameters=_object({"name": _STRING, "value": {}}, ["name", "value"]),
    ),
)


def tool_catalog(config: LoroConfig) -> list[ToolSchema]:
    """Schemas for the tools this configuration can actually execute.

    Tools whose subsystem is disabled are omitted so the model is never offered a
    capability that would be refused on invocation.
    """

    disabled: set[str] = set()
    if not config.polaris.enabled:
        disabled.add("polaris.readonly")
    if not config.memory.local.enabled:
        disabled.add("memory.search")
    if not config.memory.shared.enabled:
        disabled.add("memory.shared_search")
    if config.permissions.shell == "deny":
        disabled.add("shell.run")
    if config.permissions.edit == "deny":
        disabled.update({"file.read", "file.search", "file.write", "file.replace"})
    if config.permissions.artifact == "deny":
        disabled.add("artifact.create")
    return [schema for schema in BUILTIN_TOOL_SCHEMAS if schema.name not in disabled]


def openai_tools(schemas: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": provider_tool_name(schema.name),
                "description": schema.description,
                "parameters": schema.parameters,
            },
        }
        for schema in schemas
    ]


def anthropic_tools(schemas: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "name": provider_tool_name(schema.name),
            "description": schema.description,
            "input_schema": schema.parameters,
        }
        for schema in schemas
    ]


def gemini_tools(schemas: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": provider_tool_name(schema.name),
                    "description": schema.description,
                    "parameters": schema.parameters,
                }
                for schema in schemas
            ]
        }
    ]


def bedrock_tool_config(schemas: list[ToolSchema]) -> dict[str, Any]:
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": provider_tool_name(schema.name),
                    "description": schema.description,
                    "inputSchema": {"json": schema.parameters},
                }
            }
            for schema in schemas
        ]
    }


def provider_tool_payload(protocol: str, schemas: list[ToolSchema]) -> dict[str, Any]:
    """Request fields that publish the catalog for one provider protocol."""

    if not schemas:
        return {}
    if protocol == "openai-compatible":
        return {"tools": openai_tools(schemas)}
    if protocol == "anthropic":
        return {"tools": anthropic_tools(schemas)}
    if protocol == "gemini":
        return {"tools": gemini_tools(schemas)}
    if protocol == "bedrock":
        return {"toolConfig": bedrock_tool_config(schemas)}
    return {}


def canonical_tool_name(name: str, schemas: list[ToolSchema] | None = None) -> str:
    """Map a provider-sanitized tool name back to its Loro name.

    Provider tool-name contracts reject ".", so the catalog publishes names with "_".
    A native tool call therefore comes back as `file_read`, not `file.read`.
    """

    candidates = schemas if schemas is not None else list(BUILTIN_TOOL_SCHEMAS)
    for schema in candidates:
        if name in (schema.name, provider_tool_name(schema.name)):
            return schema.name
    return name


def provider_tool_name(name: str) -> str:
    """Render a Loro tool name within provider function-name contracts."""

    return name.replace(".", "_")
