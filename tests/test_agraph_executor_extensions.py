"""Executor extensions Loro does not implement must not run as model tasks (AGS SPEC 23)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loro.agraph.execute import GraphExecutionError, GraphExecutor
from loro.agraph.policy import evaluate_policy, executor_extensions
from loro.cli import app
from loro.config import AGraphConfig, LoroConfig

GRAPH = """\
ags_version: "1.0"
kind: AgenticGraph
id: loro.tests.executor-extension
title: Executor extension
objective: Call a tool through another harness's executor.
requires_conformance: 1
entrypoints: [lookup]
nodes:
  lookup:
    type: task
    title: Look up the ticket
    description: Fetch the ticket through an MCP server.
    x-acme-priority: high
    x-magagent-executor:
      kind: mcp
      server: tickets
      tool: get_ticket
"""


class _Result:
    summary = "done"
    session_id = "fake"
    stop_reason = "completed"
    usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "cost_usd": 0.0}
    emitted_outputs: dict = {}


class _Runtime:
    calls = 0

    def run(self, _prompt: str, mode: str) -> _Result:
        _Runtime.calls += 1
        return _Result()


def _graph(tmp_path: Path) -> Path:
    path = tmp_path / "executor.agraph.yaml"
    path.write_text(GRAPH, encoding="utf-8")
    return path


def _config(tmp_path: Path, **agraph) -> LoroConfig:
    return LoroConfig.model_validate(
        {
            "agraph": {"state_path": str(tmp_path / "runs"), **agraph},
            "safety": {"enabled": False},
            "permissions": {"default": "allow"},
            "audit": {"path": str(tmp_path / "audit.jsonl")},
        }
    )


def test_only_executor_extensions_are_recognized() -> None:
    node = {"x-magagent-executor": {}, "x-executor": {}, "x-agent-profile": "a", "x-acme-x": 1}
    assert executor_extensions(node) == ["x-executor", "x-magagent-executor"]


def test_policy_refuses_an_unknown_executor_and_names_node_and_extension() -> None:
    import yaml

    findings = evaluate_policy(yaml.safe_load(GRAPH), AGraphConfig())
    [finding] = [item for item in findings if item.code == "LP011"]
    assert finding.severity == "error"
    assert "'lookup'" in finding.message and "'x-magagent-executor'" in finding.message
    assert "--allow-unknown-executors" in finding.message
    assert finding.pointer == "/nodes/lookup/x-magagent-executor"


def test_the_executor_no_longer_runs_the_node_as_a_model_task(tmp_path: Path) -> None:
    _Runtime.calls = 0
    with pytest.raises(GraphExecutionError, match="LP011"):
        GraphExecutor(
            _config(tmp_path), workspace=tmp_path, runtime_factory=lambda _config: _Runtime()
        ).run(_graph(tmp_path), plan_approved=True)
    assert _Runtime.calls == 0


def test_opt_in_runs_it_as_a_model_task_with_a_recorded_warning(tmp_path: Path) -> None:
    _Runtime.calls = 0
    record = GraphExecutor(
        _config(tmp_path, allow_unknown_executors=True),
        workspace=tmp_path,
        runtime_factory=lambda _config: _Runtime(),
    ).run(_graph(tmp_path), plan_approved=True)
    assert _Runtime.calls == 1
    [warning] = [item for item in record["diagnostics"] if item["code"] == "LP011"]
    assert warning["severity"] == "warning" and "ordinary model task" in warning["message"]


def test_cli_validate_plan_and_explain_refuse_unless_allowed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LORO_CONFIG_CONTENT", f'[agraph]\nstate_path = "{tmp_path / "runs"}"')
    runner = CliRunner()
    graph = str(_graph(tmp_path))
    for command in (["graph", "validate", graph], ["graph", "plan", graph, "--json"]):
        refused = runner.invoke(app, command)
        assert refused.exit_code == 1 and "x-magagent-executor" in refused.output
        allowed = runner.invoke(app, [*command, "--allow-unknown-executors"])
        assert allowed.exit_code == 0, allowed.output
    explained = runner.invoke(app, ["graph", "policy", "explain", graph])
    assert explained.exit_code == 1 and json.loads(explained.output)["allowed"] is False
    strict = runner.invoke(
        app, ["graph", "validate", graph, "--allow-unknown-executors", "--strict"]
    )
    assert strict.exit_code == 1  # --strict still treats the warning as a failure


def test_cli_run_prints_the_warning_when_allowed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LORO_CONFIG_CONTENT", f'[agraph]\nstate_path = "{tmp_path / "runs"}"')
    result = CliRunner().invoke(
        app,
        ["graph", "run", str(_graph(tmp_path)), "--dry-run", "--allow-unknown-executors"],
    )
    assert result.exit_code == 0, result.output
    assert "Warning LP011" in result.output
    refused = CliRunner().invoke(app, ["graph", "run", str(_graph(tmp_path)), "--dry-run"])
    assert refused.exit_code == 2 and "LP011" in refused.output
