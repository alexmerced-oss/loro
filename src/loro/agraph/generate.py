from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from loro.agraph.policy import evaluate_policy
from loro.agraph.validate import validate_graph
from loro.config import LoroConfig


def generate_graph(goal: str, config: LoroConfig) -> dict[str, Any]:
    """Run deterministic skeleton and per-node specification passes."""
    if not config.agraph.allow_generation:
        raise ValueError("Agentic Graph generation is disabled by managed policy")
    graph = _skeleton_pass(goal)
    _specification_pass(graph, config)
    return graph


def _skeleton_pass(goal: str) -> dict[str, Any]:
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:48] or "generated-task"
    return {
        "ags_version": "1.0",
        "kind": "AgenticGraph",
        "id": f"loro.{slug}",
        "title": goal[:200],
        "description": f"A Loro-generated governed execution plan for: {goal}",
        "objective": goal,
        "requires_conformance": 1,
        "entrypoints": ["execute"],
        "constraints": {"max_parallel_nodes": 1, "max_node_executions": 3},
        "nodes": {
            "execute": {
                "type": "task",
                "title": "Execute the goal",
                "description": goal,
                "outputs": {
                    "result": {
                        "type": "text",
                        "description": "A concise account of the completed work.",
                    }
                },
            }
        },
        "outputs": {
            "result": {
                "type": "text",
                "description": "The completed result.",
                "from": "nodes.execute.outputs.result",
            }
        },
    }


def _specification_pass(graph: dict[str, Any], config: LoroConfig) -> None:
    """Attach measurable contracts, routing, bounds, failure policy, and estimates."""
    tier = "standard"
    if config.agraph.max_tier == "minimal":
        tier = "minimal"
    node = graph["nodes"]["execute"]
    node.update(
        {
            "intelligence": {"tier": tier, "allow_downgrade": True},
            "constraints": {
                "max_agent_steps": min(config.runtime.max_steps, 10),
                "max_tool_calls": min(config.runtime.max_tool_calls, 50),
            },
            "failure": {
                "retry": {
                    "max_attempts": 2,
                    "retry_on": ["transient", "tool_error", "criteria_failed"],
                    "feedback": "failed_criteria",
                },
                "on_exhausted": "fail",
            },
            "success": {
                "summary": "The agent emitted a non-empty result.",
                "criteria": [
                    {
                        "id": "result_present",
                        "kind": "regex",
                        "description": "The result is not empty.",
                        "output": "result",
                        "pattern": ".+",
                    }
                ],
            },
            "estimate": {"effort": "m", "cost_usd": 1.0},
        }
    )


def write_generated_graph(goal: str, output: Path, config: LoroConfig) -> Path:
    graph = generate_graph(goal, config)
    output.parent.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for _ in range(2):
        output.write_text(yaml.safe_dump(graph, sort_keys=False), encoding="utf-8")
        report = validate_graph(output)
        policy = evaluate_policy(graph, config.agraph)
        failures = [item.message for item in report.errors] + [item.message for item in policy]
        if not failures:
            break
        _repair_generated_graph(graph, config)
    if failures:
        output.unlink(missing_ok=True)
        raise ValueError("generated graph failed review: " + "; ".join(failures))
    return output


def _repair_generated_graph(graph: dict[str, Any], config: LoroConfig) -> None:
    """Apply only deterministic, policy-preserving repairs; never hide a denial."""
    node = graph["nodes"]["execute"]
    node["intelligence"]["tier"] = config.agraph.max_tier
    if config.agraph.max_tier in {"advanced", "frontier"}:
        node["intelligence"]["rationale"] = "Managed policy selected this tier."
