"""Resuming a graph run must never feed a redaction marker back in place of a real value."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loro.agraph.execute import GraphExecutionError, GraphExecutor
from loro.cli import app
from loro.config import LoroConfig

SECRET_URL = "https://api.example.test/v1?token=abcdefgh12345678"

GRAPH = """\
ags_version: "1.0"
kind: AgenticGraph
id: loro.tests.redacted-params
title: Redacted params
objective: Pause at a gate so the run can be resumed.
requires_conformance: 1
entrypoints: [review]
params:
  endpoint:
    type: string
    description: Service URL that carries a token.
  retries:
    type: integer
    description: Retry count.
    default: 1
nodes:
  review:
    type: gate
    title: Review
    description: Wait for a person.
    gate:
      mode: approve
      roles: [project-owner]
      prompt: Continue?
      on_timeout: hold
      on_reject: fail
"""


def _config(tmp_path: Path) -> dict:
    return {
        "agraph": {"state_path": str(tmp_path / "runs")},
        "identity": {"roles": ["project-owner"]},
        "audit": {"path": str(tmp_path / "audit.jsonl")},
        # The run store redacts a token inside a longer string instead of refusing to save.
        "safety": {"surfaces": {"session": {"action": "redact"}}},
    }


def _paused_run(tmp_path: Path) -> tuple[LoroConfig, Path, str]:
    config = LoroConfig.model_validate(_config(tmp_path))
    graph = tmp_path / "redacted.agraph.yaml"
    graph.write_text(GRAPH, encoding="utf-8")
    record = GraphExecutor(config, workspace=tmp_path).run(
        graph, params={"endpoint": SECRET_URL}, plan_approved=True
    )
    assert record["status"] == "awaiting_human"
    saved = json.loads((tmp_path / "runs" / f"{record['run_id']}.json").read_text())
    endpoint = saved["metadata"]["params"]["endpoint"]
    assert "[redacted]" in endpoint and endpoint != "[redacted]"  # partial redaction
    return config, graph, record["run_id"]


def test_marker_detection_is_containment_in_any_value() -> None:
    from loro.agraph.execute import redacted_param_names

    params = {
        "whole": "[redacted]",
        "partial": "Bearer [REDACTED] trailing",
        "nested": {"headers": ["x", {"auth": "k=[redacted]"}]},
        "clean": "https://example.test",
        "number": 3,
    }
    assert redacted_param_names(params) == ["nested", "partial", "whole"]
    assert redacted_param_names({"a": "<hidden>"}, ("<hidden>",)) == ["a"]


def test_resume_refuses_partially_redacted_params_and_names_them(tmp_path: Path) -> None:
    config, _graph, run_id = _paused_run(tmp_path)
    executor = GraphExecutor(config, workspace=tmp_path)
    with pytest.raises(GraphExecutionError, match="endpoint") as raised:
        executor.resume(run_id)
    assert getattr(raised.value, "names", None) == ["endpoint"]
    assert "--param NAME=VALUE" in str(raised.value)
    resumed = GraphExecutor(
        config, workspace=tmp_path, gate_provider=lambda _prompt, _roles: True
    ).resume(run_id, params={"endpoint": SECRET_URL})
    assert resumed["status"] == "succeeded"


def test_resume_refuses_the_uppercase_marker(tmp_path: Path) -> None:
    config, _graph, run_id = _paused_run(tmp_path)
    path = tmp_path / "runs" / f"{run_id}.json"
    saved = json.loads(path.read_text())
    saved["metadata"]["params"]["endpoint"] = "[REDACTED]"
    path.write_text(json.dumps(saved))
    with pytest.raises(GraphExecutionError, match="endpoint"):
        GraphExecutor(config, workspace=tmp_path).resume(run_id)


def test_executor_refuses_a_marker_from_any_caller_before_saving(tmp_path: Path) -> None:
    config = LoroConfig.model_validate(_config(tmp_path))
    graph = tmp_path / "redacted.agraph.yaml"
    graph.write_text(GRAPH, encoding="utf-8")
    with pytest.raises(GraphExecutionError, match="endpoint"):
        GraphExecutor(config, workspace=tmp_path).run(
            graph, params={"endpoint": "token=[REDACTED]"}, plan_approved=True
        )
    assert not (tmp_path / "runs").exists() or not list((tmp_path / "runs").iterdir())


def _cli_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[agraph]\nstate_path = "{tmp_path / "runs"}"\n'
        f'[identity]\nroles = ["project-owner"]\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n'
        '[safety.surfaces.session]\naction = "redact"\n',
    )


def test_cli_resume_without_values_exits_2_and_names_params(tmp_path, monkeypatch) -> None:
    _config_obj, _graph, run_id = _paused_run(tmp_path)
    _cli_env(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["graph", "resume", run_id])
    assert result.exit_code == 2
    assert "endpoint" in result.output and "--param" in result.output
    assert "abcdefgh12345678" not in result.output


@pytest.mark.parametrize("style", ["param", "params", "param-file"])
def test_cli_resume_accepts_values_supplied_again(tmp_path, monkeypatch, style) -> None:
    _config_obj, _graph, run_id = _paused_run(tmp_path)
    _cli_env(tmp_path, monkeypatch)
    if style == "param":
        extra = ["--param", f"endpoint={SECRET_URL}", "--param", "retries=3"]
    elif style == "params":
        extra = ["--params", json.dumps({"endpoint": SECRET_URL})]
    else:
        values = tmp_path / "values.json"
        values.write_text(json.dumps({"endpoint": SECRET_URL}))
        extra = ["--param-file", str(values)]
    result = CliRunner().invoke(app, ["graph", "resume", run_id, *extra])
    assert result.exit_code == 0, result.output
    assert '"awaiting_human"' in result.output or '"succeeded"' in result.output


def test_cli_resume_prompts_with_hidden_input_in_a_terminal(tmp_path, monkeypatch) -> None:
    _config_obj, _graph, run_id = _paused_run(tmp_path)
    _cli_env(tmp_path, monkeypatch)
    monkeypatch.setattr("loro.cli.graph._interactive", lambda: True)
    prompts: list[tuple[str, bool]] = []

    def fake_prompt(text: str, hide_input: bool = False, **_kwargs) -> str:
        prompts.append((text, hide_input))
        return SECRET_URL

    monkeypatch.setattr("loro.cli.graph.typer.prompt", fake_prompt)
    result = CliRunner().invoke(app, ["graph", "resume", run_id])
    assert result.exit_code == 0, result.output
    assert prompts == [("endpoint", True)]


def test_cli_param_rejects_malformed_pairs(tmp_path, monkeypatch) -> None:
    _config_obj, _graph, run_id = _paused_run(tmp_path)
    _cli_env(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["graph", "resume", run_id, "--param", "novalue"])
    assert result.exit_code == 2 and "NAME=VALUE" in result.output
