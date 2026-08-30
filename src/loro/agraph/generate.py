from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from loro.agraph.policy import evaluate_policy
from loro.agraph.validate import validate_graph
from loro.config import LoroConfig

GENERATION_TOOLS = (
    "file_read",
    "file_search",
    "file_write",
    "file_replace",
    "shell_exec",
    "git_status",
    "git_diff",
    "memory_search",
    "shared_memory_search",
    "polaris_read",
    "artifact_create",
    "skill_read",
    "skill_run",
)


class WorkflowStepDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1)
    output_description: str = Field(min_length=1)
    required_tools: list[str] = Field(default_factory=list)


class WorkflowDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    steps: list[WorkflowStepDraft] = Field(min_length=1, max_length=12)


def graph_generation_prompt(
    goal: str,
    *,
    max_steps: int = 12,
    feedback: str | None = None,
) -> str:
    correction = (
        "\n\nThe previous graph was rejected. Return a complete corrected replacement. "
        f"Validation feedback: {feedback[:2000]}"
        if feedback
        else ""
    )
    return (
        "Author the substantive workflow for an Agentic Graph. Loro will compile your workflow "
        "into the exact governed AGS schema, so do not emit AGS fields. Return exactly one JSON "
        "object and no markdown fence with fields: title (string), description (string), and "
        "steps (array). Every step must contain exactly title, description, "
        "output_description, and required_tools. required_tools is an array using only these "
        f"canonical logical names: {', '.join(GENERATION_TOOLS)}. Never invent a tool name. "
        "Declare every capability the step may call. External web research uses shell_exec in "
        "Loro because governed curl/wget execution is its web-fetch backend. Steps execute "
        "sequentially in the order returned. "
        f"Use between 1 and {max_steps} concrete steps, decomposing the work whenever that "
        "materially improves execution or review. Describe actual work and useful outputs, not "
        "generic planning placeholders. Do not execute the goal.\n\n"
        f"GOAL:\n{goal.strip()}"
        + correction
    )


def write_ai_generated_graph(
    goal: str,
    output: Path,
    config: LoroConfig,
    author: Callable[[str], str],
) -> Path:
    if config.model.provider == "mock":
        raise ValueError(
            "AI graph generation requires a configured model provider; the resolved provider "
            "is mock. Run `loro configure`, then retry. Use --no-ai only for the explicit "
            "offline skeleton. No graph was written."
        )
    max_steps = max(
        1,
        min(12, config.agraph.max_nodes, max(1, config.agraph.max_node_executions // 2)),
    )
    feedback: str | None = None
    for _attempt in range(2):
        response = author(
            graph_generation_prompt(goal, max_steps=max_steps, feedback=feedback)
        )
        try:
            draft = _parse_workflow_draft(response)
            if len(draft.steps) > max_steps:
                raise ValueError(
                    f"The workflow has {len(draft.steps)} steps; managed limits allow {max_steps}."
                )
            graph = _compile_workflow_draft(goal, draft, config)
            return _write_reviewed_graph(graph, output, config)
        except ValueError as error:
            feedback = str(error)
    raise ValueError(
        f"The model could not produce a valid governed graph after one correction: {feedback}. "
        "No graph was written."
    )


def _parse_workflow_draft(content: str) -> WorkflowDraft:
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            try:
                return WorkflowDraft.model_validate(candidate)
            except ValidationError as error:
                raise ValueError(
                    f"The model returned an invalid workflow draft: {error}"
                ) from error
        break
    raise ValueError("The model did not return one JSON workflow object.")


def _compile_workflow_draft(
    goal: str,
    draft: WorkflowDraft,
    config: LoroConfig,
) -> dict[str, Any]:
    tier = "minimal" if config.agraph.max_tier == "minimal" else "standard"
    attempts = 2 if config.agraph.max_node_executions >= len(draft.steps) * 2 else 1
    cost = 0.25
    if config.agraph.max_cost_usd is not None:
        cost = min(cost, config.agraph.max_cost_usd / len(draft.steps))
    nodes: dict[str, Any] = {}
    used_ids: set[str] = set()
    previous: str | None = None
    for index, step in enumerate(draft.steps, start=1):
        tools = step.required_tools or _infer_step_tools(step)
        unknown = sorted(set(tools) - set(GENERATION_TOOLS))
        if unknown:
            raise ValueError(
                "The workflow requested unavailable tools: "
                + ", ".join(unknown)
                + ". Choose only from the canonical logical tool list."
            )
        base = re.sub(r"[^a-z0-9]+", "_", step.title.lower())[:48].strip("_")
        base = base or f"step_{index}"
        node_id = base
        suffix = 2
        while node_id in used_ids:
            node_id = f"{base[:44].rstrip('_')}_{suffix}"
            suffix += 1
        used_ids.add(node_id)
        node: dict[str, Any] = {
            "type": "task",
            "title": step.title,
            "description": step.description,
            "intelligence": {"tier": tier, "allow_downgrade": True},
            "requirements": {
                "tools": tools,
                "permissions": _permissions_for_tools(tools),
                "workspace": (
                    "read_write"
                    if any(tool in tools for tool in ("file_write", "file_replace"))
                    else "read_only"
                ),
            },
            "constraints": {
                "max_agent_steps": min(config.runtime.max_steps, 10),
                "max_tool_calls": min(config.runtime.max_tool_calls, 50),
            },
            "outputs": {
                "result": {"type": "text", "description": step.output_description}
            },
            "failure": {
                "retry": {
                    "max_attempts": attempts,
                    "retry_on": ["transient", "tool_error", "criteria_failed"],
                    "feedback": "failed_criteria",
                },
                "on_exhausted": "fail",
            },
            "success": {
                "summary": "The step emitted its declared result.",
                "criteria": [
                    {
                        "id": "result_present",
                        "kind": "regex",
                        "description": "The declared result is not empty.",
                        "output": "result",
                        "pattern": ".+",
                    }
                ],
            },
            "estimate": {"effort": "m", "cost_usd": round(cost, 6)},
        }
        if previous is not None:
            node["depends_on"] = [previous]
            node["inputs"] = {
                "previous_result": {
                    "type": "text",
                    "description": "The completed result from the preceding workflow step.",
                    "from": f"nodes.{previous}.outputs.result",
                }
            }
        nodes[node_id] = node
        previous = node_id
    first = next(iter(nodes))
    final = previous or first
    slug = re.sub(r"[^a-z0-9]+", "-", draft.title.lower())[:48].strip("-") or "workflow"
    return {
        "ags_version": "1.0",
        "kind": "AgenticGraph",
        "id": f"loro.{slug}",
        "title": draft.title,
        "description": draft.description,
        "objective": goal,
        "requires_conformance": 1,
        "entrypoints": [first],
        "constraints": {
            "max_parallel_nodes": 1,
            "max_node_executions": len(nodes) * attempts,
        },
        "nodes": nodes,
        "outputs": {
            "result": {
                "type": "text",
                "description": draft.steps[-1].output_description,
                "from": f"nodes.{final}.outputs.result",
            }
        },
    }


def _infer_step_tools(step: WorkflowStepDraft) -> list[str]:
    """Keep older three-field model drafts runnable with conservative capability inference."""
    text = " ".join((step.title, step.description, step.output_description)).lower()
    tools: list[str] = []
    if any(word in text for word in ("research", "source", "web", "internet", "history")):
        tools.append("shell_exec")
    if any(word in text for word in ("project", "code", "file", "inspect", "review")):
        tools.extend(("file_read", "file_search"))
    if any(word in text for word in ("create", "write", "edit", "implement", "publish", "website")):
        tools.extend(("file_read", "file_write"))
    if any(word in text for word in ("build", "test", "verify", "command")):
        tools.append("shell_exec")
    return list(dict.fromkeys(tools))


def _permissions_for_tools(tools: list[str]) -> list[str]:
    permissions: list[str] = []
    if any(tool in tools for tool in ("file_read", "file_search")):
        permissions.append("fs:read:**")
    if any(tool in tools for tool in ("file_write", "file_replace", "artifact_create")):
        permissions.append("fs:write:**")
    if "shell_exec" in tools:
        permissions.extend(("shell:exec:*", "net:fetch:*"))
    if any(tool.startswith("git_") for tool in tools):
        permissions.append("git:read:*")
    return permissions


def _write_reviewed_graph(graph: dict[str, Any], output: Path, config: LoroConfig) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(graph, sort_keys=False), encoding="utf-8")
    report = validate_graph(output)
    policy = evaluate_policy(graph, config.agraph)
    failures = [item.message for item in report.errors + report.warnings] + [
        item.message for item in policy
    ]
    if failures:
        output.unlink(missing_ok=True)
        raise ValueError("generated graph failed review: " + "; ".join(failures))
    return output


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
    inferred_tools = _infer_step_tools(
        WorkflowStepDraft(
            title=str(node.get("title") or "Execute the goal"),
            description=str(node.get("description") or graph.get("objective") or ""),
            output_description="A concise account of the completed work.",
        )
    )
    node.update(
        {
            "intelligence": {"tier": tier, "allow_downgrade": True},
            "requirements": {
                "tools": inferred_tools,
                "permissions": _permissions_for_tools(inferred_tools),
                "workspace": (
                    "read_write"
                    if any(tool in inferred_tools for tool in ("file_write", "file_replace"))
                    else "read_only"
                ),
            },
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
