import pytest

from loro.budgets import BudgetExceeded, UsageBudget
from loro.config import ModelConfig, RuntimeConfig
from loro.models import ModelMessage, ModelResponse


def test_usage_budget_tracks_reported_tokens_cost_and_tools() -> None:
    budget = UsageBudget(
        RuntimeConfig(max_tool_calls=2, max_cost_usd=1),
        ModelConfig(input_cost_per_million=2, output_cost_per_million=4),
    )
    budget.before_model([ModelMessage(role="user", content="hello")])
    budget.after_model(
        ModelResponse(
            content="answer",
            raw={"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
        )
    )
    budget.add_tool_calls(2)

    assert budget.payload() == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.0004,
        "tool_calls": 2,
    }


@pytest.mark.parametrize(
    ("runtime", "operation", "expected"),
    [
        (RuntimeConfig(max_model_input_bytes=1024), "input", "model_input_bytes"),
        (RuntimeConfig(max_model_output_bytes=1024), "output", "model_output_bytes"),
        (RuntimeConfig(max_tool_calls=0), "tool", "tool_calls"),
        (RuntimeConfig(max_input_tokens=1), "input_tokens", "input_tokens"),
        (RuntimeConfig(max_output_tokens=1), "tokens", "output_tokens"),
    ],
)
def test_usage_budget_enforces_each_boundary(runtime, operation, expected) -> None:
    budget = UsageBudget(runtime, ModelConfig())

    with pytest.raises(BudgetExceeded) as raised:
        if operation == "input":
            budget.before_model([ModelMessage(role="user", content="x" * 1025)])
        elif operation == "output":
            budget.after_model(ModelResponse(content="x" * 1025))
        elif operation == "tool":
            budget.add_tool_calls(1)
        elif operation == "input_tokens":
            budget.before_model([ModelMessage(role="user", content="more than one token")])
        else:
            budget.after_model(
                ModelResponse(content="many tokens", raw={"usage": {"completion_tokens": 2}})
            )

    assert raised.value.budget == expected


def test_usage_budget_blocks_projected_input_cost_before_request() -> None:
    budget = UsageBudget(
        RuntimeConfig(max_cost_usd=0.000001),
        ModelConfig(input_cost_per_million=10),
    )

    with pytest.raises(BudgetExceeded) as raised:
        budget.before_model([ModelMessage(role="user", content="costly prompt")])

    assert raised.value.budget == "cost"
