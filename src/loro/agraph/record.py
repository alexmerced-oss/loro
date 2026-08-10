from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

import jsonschema


@lru_cache(maxsize=1)
def _run_record_validator() -> jsonschema.Draft202012Validator:
    """Compile the run-record schema once.

    The record is validated on every save, and re-reading plus re-parsing the schema file
    each time dominated the cost of persisting a run.
    """

    schema = json.loads(
        files("loro.agraph.schema")
        .joinpath("agentic-graph-run-1.0.schema.json")
        .read_text(encoding="utf-8")
    )
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def validate_run_record(record: dict[str, Any]) -> None:
    _run_record_validator().validate(record)


def aggregate_usage(record: dict[str, Any]) -> dict[str, int | float]:
    totals: dict[str, int | float] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "tool_calls": 0,
        "wall_clock_seconds": 0.0,
        "node_executions": 0,
    }
    for node in record.get("nodes", {}).values():
        for attempt in node.get("attempts", []):
            totals["node_executions"] += 1
            for key, value in attempt.get("usage", {}).items():
                if key in totals and key != "node_executions" and isinstance(value, (int, float)):
                    totals[key] += value
    return totals
