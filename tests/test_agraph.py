from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import pytest
import yaml
from typer.testing import CliRunner

from loro.agraph.criteria import CriteriaEvaluator
from loro.agraph.document import GraphDocumentError, canonical_json, graph_digest, load_graph
from loro.agraph.execute import GraphExecutionError, GraphExecutor
from loro.agraph.expressions import ExpressionError, evaluate
from loro.agraph.generate import generate_graph
from loro.agraph.plan import build_plan
from loro.agraph.policy import evaluate_policy
from loro.agraph.record import validate_run_record
from loro.agraph.routing import RoutingError, route_model
from loro.agraph.validate import validate_graph
from loro.audit import verify_jsonl_audit
from loro.cli import app
from loro.config import LoroConfig, ModelConfig, ModelTierConfig

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "agraph"


@dataclass
class FakeResult:
    summary: str = "done"
    session_id: str = "fake-session"
    stop_reason: str = "completed"
    usage: dict[str, int | float] = None  # type: ignore[assignment]
    emitted_outputs: dict[str, object] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.usage = {
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "cost_usd": 0.01,
            "tool_calls": 1,
        }
        self.emitted_outputs = {"result": "finished"}


class FakeRuntime:
    def run(self, _prompt: str, mode: str) -> FakeResult:
        assert mode == "run"
        return FakeResult()


def test_spec_examples_validate() -> None:
    for path in (FIXTURES / "examples").iterdir():
        assert validate_graph(path).ok, path.name


def test_invalid_conformance_fixtures_report_expected_code() -> None:
    for path in (FIXTURES / "invalid").iterdir():
        match = re.search(r"# EXPECT: (AG\d+)", path.read_text(encoding="utf-8"))
        assert match is not None
        assert match.group(1) in {finding.code for finding in validate_graph(path).findings}


def test_loader_rejects_duplicate_keys_and_digest_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.agraph.yaml"
    path.write_text("ags_version: '1.0'\nags_version: '1.0'\n", encoding="utf-8")
    with pytest.raises(GraphDocumentError, match="duplicate key"):
        load_graph(path)
    json_path = tmp_path / "duplicate.agraph.json"
    json_path.write_text('{"kind":"AgenticGraph","kind":"AgenticGraph"}', encoding="utf-8")
    with pytest.raises(GraphDocumentError, match="duplicate key"):
        load_graph(json_path)
    assert graph_digest({"b": 2, "a": 1}) == graph_digest({"a": 1, "b": 2})
    assert canonical_json({"whole": 1.0, "small": 1e-7}) == b'{"small":1e-7,"whole":1}'


def test_expression_evaluator_is_strict_and_never_uses_host_eval() -> None:
    scope = {"params": {"ready": True}, "nodes": {"build": {"status": "succeeded"}}}
    assert evaluate("params.ready && succeeded('build')", scope) is True
    # Spec (references/expressions.md §4): different types are never equal, and comparing
    # them is not an error. Ordering comparisons remain strictly typed.
    assert evaluate("1 == true", scope) is False
    assert evaluate("1 != true", scope) is True
    with pytest.raises(ExpressionError, match="same type"):
        evaluate("1 < 'two'", scope)
    with pytest.raises(ExpressionError):
        evaluate("__import__('os')", scope)


def test_policy_and_routing_fail_closed() -> None:
    config = LoroConfig()
    graph = generate_graph("Prepare evidence", config)
    graph["nodes"]["execute"]["intelligence"] = {"tier": "frontier"}
    managed = config.agraph.model_copy(update={"max_tier": "standard"})
    assert {finding.code for finding in evaluate_policy(graph, managed)} == {"LP003"}
    config.model.tiers["minimal"] = ModelTierConfig(provider="mock", model="small")
    with pytest.raises(RoutingError, match="RT011"):
        route_model(config, {"tier": "frontier"})


def test_tier_routing_preserves_provider_connection_settings() -> None:
    config = LoroConfig()
    config.model.tiers["advanced"] = ModelTierConfig(
        provider="openai-compatible",
        model="enterprise-model",
        api_key_env="ENTERPRISE_MODEL_KEY",  # pragma: allowlist secret
        base_url="https://models.example.test/v1",
    )
    routed = route_model(config, {"tier": "advanced"}).model_config(ModelConfig())
    assert routed.provider == "openai-compatible"
    assert routed.model == "enterprise-model"
    assert routed.api_key_env == "ENTERPRISE_MODEL_KEY"  # pragma: allowlist secret
    assert routed.base_url == "https://models.example.test/v1"


def test_plan_accounts_for_retries() -> None:
    graph = generate_graph("Prepare evidence", LoroConfig())
    graph["nodes"]["execute"]["failure"] = {"retry": {"max_attempts": 3}}
    assert build_plan(graph).worst_case_executions == 3


def test_executor_emits_conformant_durable_run_record(tmp_path: Path) -> None:
    config = LoroConfig.model_validate(
        {
            "agraph": {"state_path": str(tmp_path / "runs")},
            "safety": {"enabled": False},
            "identity": {"roles": ["project-owner"]},
            "permissions": {"default": "allow", "edit": "allow"},
            "audit": {"path": str(tmp_path / "audit.jsonl")},
        }
    )
    graph_path = tmp_path / "task.agraph.yaml"
    graph_path.write_text(
        yaml.safe_dump(generate_graph("Prepare evidence", config), sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(GraphExecutionError, match="requires explicit plan approval"):
        GraphExecutor(
            config, workspace=tmp_path, runtime_factory=lambda _config: FakeRuntime()
        ).run(graph_path)
    record = GraphExecutor(
        config, workspace=tmp_path, runtime_factory=lambda _config: FakeRuntime()
    ).run(graph_path, plan_approved=True)
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"result": "finished"}
    assert record["usage"]["node_executions"] == 1
    validate_run_record(record)
    assert (tmp_path / "runs" / f"{record['run_id']}.json").is_file()
    audit = verify_jsonl_audit(tmp_path / "audit.jsonl")
    assert audit.ok


def test_gate_pauses_and_resumes(tmp_path: Path) -> None:
    source = ROOT / "docs" / "examples" / "agraph" / "enterprise-brief.agraph.yaml"
    config = LoroConfig.model_validate(
        {
            "agraph": {"state_path": str(tmp_path / "runs")},
            "safety": {"enabled": False},
            "identity": {"roles": ["project-owner"]},
            "permissions": {"default": "allow", "edit": "allow"},
            "audit": {"path": str(tmp_path / "audit.jsonl")},
        }
    )
    copied = tmp_path / source.name
    copied.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    artifact = tmp_path / "artifacts" / "project-brief.md"
    artifact.parent.mkdir()
    artifact.write_text("content " * 100, encoding="utf-8")
    paused = GraphExecutor(
        config, workspace=tmp_path, runtime_factory=lambda _config: FakeRuntime()
    ).run(copied, plan_approved=True)
    assert paused["status"] == "awaiting_human"
    completed = GraphExecutor(
        config,
        workspace=tmp_path,
        runtime_factory=lambda _config: FakeRuntime(),
        gate_provider=lambda _prompt, _roles: True,
    ).resume(paused["run_id"])
    assert completed["status"] == "succeeded"
    validate_run_record(completed)


def test_graph_cli_validate_plan_and_generate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LORO_CONFIG_CONTENT", f'[agraph]\nstate_path = "{tmp_path / "runs"}"')
    output = tmp_path / "generated.agraph.yaml"
    runner = CliRunner()
    generated = runner.invoke(app, ["graph", "generate", "Prepare evidence", "--out", str(output)])
    assert generated.exit_code == 0, generated.output
    assert runner.invoke(app, ["graph", "validate", str(output), "--strict"]).exit_code == 0
    planned = runner.invoke(app, ["graph", "plan", str(output), "--json"])
    assert planned.exit_code == 0
    assert '"worst_case_executions": 2' in planned.output
    second = tmp_path / "from-plan.agraph.yaml"
    generated_from_plan = runner.invoke(
        app,
        ["plan", "Prepare another report", "--format", "agraph", "--out", str(second)],
    )
    assert generated_from_plan.exit_code == 0
    assert validate_graph(second).ok
    skill_path = runner.invoke(app, ["graph", "skill-path"])
    assert skill_path.exit_code == 0
    assert (Path(skill_path.output.strip()) / "SKILL.md").is_file()


def test_criteria_variants_and_external_registry(tmp_path: Path) -> None:
    artifact = tmp_path / "result.json"
    artifact.write_text('{"ready": true}', encoding="utf-8")
    config = LoroConfig.model_validate(
        {
            "agraph": {
                "allow_command_criteria": True,
                "allow_external_criteria": True,
                "external_criteria": ["enterprise-check"],
            }
        }
    )
    evaluator = CriteriaEvaluator(
        config,
        workspace=tmp_path,
        human=lambda _prompt, _roles: True,
        external={"enterprise-check": lambda params, _scope: (params["ready"], "checked")},
    )
    outputs = {"artifact": "result.json", "payload": {"ready": True}, "text": "release ready"}
    scope = {"params": {"ready": True}, "nodes": {}}
    criteria = [
        {"id": "file", "kind": "file_exists", "description": "file", "path": "result.json"},
        {
            "id": "artifact",
            "kind": "artifact_present",
            "description": "artifact",
            "output": "artifact",
        },
        {
            "id": "schema",
            "kind": "json_schema",
            "description": "schema",
            "output": "payload",
            "schema": {"type": "object", "required": ["ready"]},
        },
        {
            "id": "regex",
            "kind": "regex",
            "description": "regex",
            "output": "text",
            "pattern": "ready$",
        },
        {"id": "expr", "kind": "expression", "description": "expr", "expr": "params.ready"},
        {"id": "human", "kind": "human", "description": "human", "prompt": "Approve?"},
        {
            "id": "external",
            "kind": "external",
            "description": "external",
            "check": "enterprise-check",
            "params": {"ready": True},
        },
    ]
    assert all(evaluator.evaluate(item, outputs, scope).passed for item in criteria)


def test_expression_decision_and_composite_nodes(tmp_path: Path) -> None:
    executor = GraphExecutor(
        LoroConfig.model_validate(
            {
                "agraph": {"state_path": str(tmp_path / "runs")},
                "safety": {"enabled": False},
                "audit": {"enabled": False},
            }
        ),
        workspace=tmp_path,
        runtime_factory=lambda _config: FakeRuntime(),
    )
    scope = {
        "graph": {"id": "test"},
        "params": {"ready": True},
        "context": {},
        "attachments": {},
        "env": {},
        "nodes": {},
    }
    decision = {
        "title": "Choose",
        "description": "Choose",
        "type": "decision",
        "decision": {
            "evaluator": "expression",
            "branches": [
                {"label": "yes", "description": "yes", "when": "params.ready"},
                {"label": "no", "description": "no", "when": "!params.ready"},
            ],
        },
    }
    assert executor._run_decision(decision, scope)[1] == {"decision": "yes"}
    fragment = {
        "entrypoints": ["choose"],
        "nodes": {"choose": decision},
        "outputs": {
            "choice": {
                "type": "text",
                "description": "choice",
                "from": "nodes.choose.outputs.decision",
            }
        },
    }
    subgraph = {
        "type": "subgraph",
        "title": "Child",
        "description": "Child",
        "subgraph": {
            "inline": fragment,
            "inherit_context": True,
            "outputs_from": {"choice": "nodes.choose.outputs.decision"},
        },
    }
    status, outputs, _ = executor._run_composite("child", subgraph, {"constraints": {}}, scope)
    assert status == "succeeded"
    assert outputs == {"choice": "yes"}


def test_map_and_loop_composition_preserve_order_and_bounds(tmp_path: Path) -> None:
    executor = GraphExecutor(
        LoroConfig.model_validate(
            {
                "agraph": {"state_path": str(tmp_path / "runs")},
                "safety": {"enabled": False},
                "audit": {"enabled": False},
            }
        ),
        workspace=tmp_path,
        runtime_factory=lambda _config: FakeRuntime(),
    )
    fragment = {
        "entrypoints": ["choose"],
        "nodes": {
            "choose": {
                "title": "Choose",
                "description": "Choose",
                "type": "decision",
                "decision": {
                    "evaluator": "expression",
                    "branches": [
                        {"label": "first", "description": "first", "when": "item == 'a'"},
                        {"label": "other", "description": "other", "when": "item != 'a'"},
                    ],
                },
            }
        },
    }
    scope = {
        "graph": {"id": "test"},
        "params": {"items": ["a", "b"], "ready": True},
        "context": {},
        "attachments": {},
        "env": {},
        "nodes": {},
    }
    map_node = {
        "type": "map",
        "map": {
            "over": "params.items",
            "as": "item",
            "max_items": 2,
            "body": fragment,
            "collect": {"choices": "nodes.choose.outputs.decision"},
        },
    }
    status, outputs, details = executor._run_composite(
        "mapped", map_node, {"constraints": {}}, scope
    )
    assert status == "succeeded"
    assert outputs == {"choices": ["first", "other"]}
    assert details == {"items": 2}

    repeat_fragment = {
        "entrypoints": ["choose"],
        "nodes": {
            "choose": {
                "title": "Choose",
                "description": "Choose",
                "type": "decision",
                "decision": {
                    "evaluator": "expression",
                    "branches": [
                        {"label": "yes", "description": "yes", "when": "params.ready"},
                        {"label": "no", "description": "no", "when": "!params.ready"},
                    ],
                },
            }
        },
    }
    loop_node = {
        "type": "loop",
        "loop": {
            "mode": "repeat",
            "max_iterations": 2,
            "body": repeat_fragment,
            "collect": {"choice": "nodes.choose.outputs.decision"},
        },
    }
    status, outputs, details = executor._run_composite(
        "repeated", loop_node, {"constraints": {}}, scope
    )
    assert status == "succeeded"
    assert outputs == {"choice": "yes"}
    assert details == {"iterations": 2}


def test_ready_nodes_execute_in_parallel(tmp_path: Path) -> None:
    lock = Lock()
    active = 0
    maximum = 0

    class ParallelRuntime:
        def run(self, _prompt: str, mode: str) -> FakeResult:
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return FakeResult()

    config = LoroConfig.model_validate(
        {
            "agraph": {"state_path": str(tmp_path / "runs"), "max_parallel_nodes": 2},
            "safety": {"enabled": False},
            "audit": {"enabled": False},
        }
    )
    graph = generate_graph("Parallel work", config)
    graph["nodes"]["second"] = dict(graph["nodes"]["execute"])
    graph["nodes"]["second"]["title"] = "Second"
    graph["entrypoints"] = ["execute", "second"]
    graph["constraints"]["max_parallel_nodes"] = 2
    graph["outputs"] = {}
    path = tmp_path / "parallel.agraph.yaml"
    path.write_text(yaml.safe_dump(graph, sort_keys=False), encoding="utf-8")
    record = GraphExecutor(
        config, workspace=tmp_path, runtime_factory=lambda _config: ParallelRuntime()
    ).run(path, plan_approved=True)
    assert record["status"] == "succeeded"
    assert maximum == 2


def test_fallback_and_global_budget_failure(tmp_path: Path) -> None:
    config = LoroConfig.model_validate(
        {
            "agraph": {"state_path": str(tmp_path / "runs"), "max_cost_usd": 0.005},
            "safety": {"enabled": False},
            "audit": {"enabled": False},
        }
    )
    graph = generate_graph("Bounded work", config)
    graph["nodes"]["execute"]["failure"]["fallback"] = [{"strategy": "skip"}]

    class MissingRuntime:
        def run(self, _prompt: str, mode: str) -> FakeResult:
            result = FakeResult()
            result.emitted_outputs = {}
            return result

    executor = GraphExecutor(
        config, workspace=tmp_path, runtime_factory=lambda _config: MissingRuntime()
    )
    scope = {
        "graph": {"id": graph["id"]},
        "params": {},
        "context": {},
        "attachments": {},
        "env": {},
        "nodes": {},
    }
    fallback_status, _, details = executor._run_node(
        "execute", graph["nodes"]["execute"], graph, scope
    )
    assert fallback_status == "skipped"
    assert details["fallback_used"] == "skip"
    path = tmp_path / "budget.agraph.yaml"
    path.write_text(yaml.safe_dump(graph, sort_keys=False), encoding="utf-8")
    record = GraphExecutor(
        config, workspace=tmp_path, runtime_factory=lambda _config: FakeRuntime()
    ).run(path, plan_approved=True)
    assert record["status"] == "failed"
    assert record["diagnostics"][0]["code"] == "RT031"
