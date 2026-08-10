"""Scope bindings, usage accounting, and output typing for the AGS executor.

Split out of `execute.py` so the scheduler module carries only scheduling and dispatch.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

__all__ = ["bind_self", "empty_usage", "matches_type", "now"]


def bind_self(
    scope: dict[str, Any],
    *,
    node_id: str,
    attempt: int,
    inputs: Mapping[str, Any],
    outputs: Mapping[str, Any] | None = None,
    status: str,
) -> None:
    """Bind the `self` and `nodes.self` namespaces the AGX spec documents."""

    binding = {
        "id": node_id,
        "attempt": attempt,
        "status": status,
        "inputs": dict(inputs),
        "outputs": dict(outputs or {}),
    }
    scope["self"] = binding
    scope.setdefault("nodes", {})["self"] = binding


def empty_usage() -> dict[str, int | float]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "tool_calls": 0,
        "wall_clock_seconds": 0.0,
        "node_executions": 0,
    }


def now() -> str:
    return datetime.now(UTC).isoformat()


def matches_type(value: Any, expected: str) -> bool:
    if expected == "any":
        return True
    if expected in {"string", "text", "markdown", "file", "directory", "artifact", "reference"}:
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected in {"object", "json"}:
        return isinstance(value, dict)
    if expected in {"array", "file_set"}:
        return isinstance(value, list)
    return False
