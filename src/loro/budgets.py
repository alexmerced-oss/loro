from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loro.config import ModelConfig, RuntimeConfig
from loro.models import ModelMessage, ModelResponse


class BudgetExceeded(RuntimeError):
    def __init__(self, budget: str, message: str) -> None:
        super().__init__(message)
        self.budget = budget


@dataclass
class UsageBudget:
    runtime: RuntimeConfig
    model: ModelConfig
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    _pending_input_tokens: int = 0

    def before_model(self, messages: list[ModelMessage]) -> None:
        input_bytes = sum(len(message.content.encode("utf-8")) for message in messages)
        if input_bytes > self.runtime.max_model_input_bytes:
            raise BudgetExceeded("model_input_bytes", "Model input byte budget exceeded.")
        self._pending_input_tokens = _estimate_tokens(
            "\n".join(message.content for message in messages)
        )
        projected_input_tokens = self.input_tokens + self._pending_input_tokens
        if (
            self.runtime.max_input_tokens is not None
            and projected_input_tokens > self.runtime.max_input_tokens
        ):
            raise BudgetExceeded("input_tokens", "Input-token budget exceeded.")
        projected_input_cost = (
            self.cost_usd
            + self._pending_input_tokens * self.model.input_cost_per_million / 1_000_000
        )
        if (
            self.runtime.max_cost_usd is not None
            and projected_input_cost > self.runtime.max_cost_usd
        ):
            raise BudgetExceeded("cost", "Model cost budget exceeded.")

    def after_model(self, response: ModelResponse) -> None:
        output_bytes = len(response.content.encode("utf-8"))
        if output_bytes > self.runtime.max_model_output_bytes:
            raise BudgetExceeded("model_output_bytes", "Model output byte budget exceeded.")
        input_tokens, output_tokens = _usage_tokens(response)
        if input_tokens == 0:
            input_tokens = self._pending_input_tokens
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self._pending_input_tokens = 0
        self.cost_usd += (
            input_tokens * self.model.input_cost_per_million
            + output_tokens * self.model.output_cost_per_million
        ) / 1_000_000
        self._require_within_limits()

    def add_tool_calls(self, count: int) -> None:
        self.tool_calls += count
        if self.tool_calls > self.runtime.max_tool_calls:
            raise BudgetExceeded("tool_calls", "Tool-call budget exceeded.")

    def payload(self) -> dict[str, int | float]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 8),
            "tool_calls": self.tool_calls,
        }

    def _require_within_limits(self) -> None:
        if (
            self.runtime.max_input_tokens is not None
            and self.input_tokens > self.runtime.max_input_tokens
        ):
            raise BudgetExceeded("input_tokens", "Input-token budget exceeded.")
        if (
            self.runtime.max_output_tokens is not None
            and self.output_tokens > self.runtime.max_output_tokens
        ):
            raise BudgetExceeded("output_tokens", "Output-token budget exceeded.")
        if self.runtime.max_cost_usd is not None and self.cost_usd > self.runtime.max_cost_usd:
            raise BudgetExceeded("cost", "Model cost budget exceeded.")


def _usage_tokens(response: ModelResponse) -> tuple[int, int]:
    raw = response.raw or {}
    usage = raw.get("usage")
    if isinstance(usage, dict):
        return (
            _integer(usage, "prompt_tokens", "input_tokens", "inputTokens"),
            _integer(usage, "completion_tokens", "output_tokens", "outputTokens"),
        )
    metadata = raw.get("usageMetadata")
    if isinstance(metadata, dict):
        return (
            _integer(metadata, "promptTokenCount"),
            _integer(metadata, "candidatesTokenCount"),
        )
    return 0, _estimate_tokens(response.content)


def _integer(payload: dict[str, Any], *names: str) -> int:
    for name in names:
        value = payload.get(name)
        if isinstance(value, int) and value >= 0:
            return value
    return 0


def _estimate_tokens(content: str) -> int:
    if not content:
        return 0
    return max(1, (len(content) + 3) // 4)
