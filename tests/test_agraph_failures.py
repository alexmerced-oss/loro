from __future__ import annotations

import inspect

from loro.agraph.execute import GraphExecutor
from loro.agraph.generate import generate_graph
from loro.agraph.plan import build_plan
from loro.config import LoroConfig


def test_failure_policy_matrix_is_bounded_and_explicit() -> None:
    graph = generate_graph("Exercise governed failure behavior", LoroConfig())
    failure = graph["nodes"]["execute"]["failure"]
    failure["retry"] = {
        "max_attempts": 3,
        "retry_on": ["transient", "tool_error", "criteria_failed"],
    }
    failure["fallback"] = [{"strategy": "skip"}]
    failure["compensation"] = "execute"

    assert build_plan(graph).worst_case_executions == 3
    assert GraphExecutor._retry_allowed(failure["retry"], "transient") is True
    assert GraphExecutor._retry_allowed(failure["retry"], "model_error") is False
    assert failure["fallback"] == [{"strategy": "skip"}]
    assert failure["compensation"] == "execute"


def test_generated_graph_requires_approval_and_budget_constraints() -> None:
    config = LoroConfig()
    graph = generate_graph("Exercise policy", config)
    assert inspect.signature(GraphExecutor.run).parameters["plan_approved"].default is False
    assert graph["constraints"]["max_node_executions"] > 0
    assert graph["constraints"]["max_parallel_nodes"] > 0
