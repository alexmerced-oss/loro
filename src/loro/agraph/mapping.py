from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TOOL_NAMES: dict[str, str] = {
    "file_read": "file.read",
    "file_search": "file.search",
    "file_write": "file.write",
    "file_replace": "file.replace",
    "shell_exec": "shell.run",
    "git_status": "git.status",
    "git_diff": "git.diff",
    "git_commit": "git.commit",
    "memory_search": "memory.search",
    "shared_memory_search": "memory.shared_search",
    "polaris_read": "polaris.readonly",
    "artifact_create": "artifact.create",
    "skill_read": "skill.read",
    "skill_run": "skill.run_script",
}

AVAILABLE_TOOLS = frozenset(TOOL_NAMES) | frozenset(TOOL_NAMES.values())


def loro_tool_name(name: str) -> str:
    """Translate an AGS logical tool name into Loro's typed tool name."""
    return TOOL_NAMES.get(name, name)


def missing_tools(required: Iterable[Any]) -> tuple[str, ...]:
    missing: list[str] = []
    for requirement in required:
        if isinstance(requirement, str):
            if requirement not in AVAILABLE_TOOLS:
                missing.append(requirement)
            continue
        if not isinstance(requirement, dict) or requirement.get("optional"):
            continue
        names = [str(requirement.get("name", "")), *requirement.get("alternatives", [])]
        if not any(name in AVAILABLE_TOOLS for name in names):
            missing.append(names[0])
    return tuple(sorted(missing))


def permission_resource(permission: str) -> tuple[str, str, str]:
    """Map an AGS permission to Loro's normalized tool/action/target tuple."""
    parts = permission.split(":", 2)
    scope = parts[0]
    action = parts[1] if len(parts) > 1 else "use"
    target = parts[2] if len(parts) > 2 else "*"
    tool = {
        "fs": "edit",
        "shell": "shell",
        "git": "git",
        "net": "network",
        "mcp": "mcp",
        "human": "approval",
        "custom": "governed_data" if action == "polaris_read" else "custom",
    }.get(scope, scope)
    return tool, action, target
