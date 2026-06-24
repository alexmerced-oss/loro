from dataclasses import dataclass

from loro.config import LoroConfig


@dataclass(frozen=True)
class AgentResult:
    summary: str
    mode: str


class AgentRuntime:
    """Small runtime placeholder for the scaffolded agent loop."""

    def __init__(self, config: LoroConfig) -> None:
        self.config = config

    def run(self, prompt: str, mode: str) -> AgentResult:
        return AgentResult(
            summary=(
                f"Loro {mode} mode is scaffolded.\n\n"
                f"Prompt: {prompt}\n"
                "Next implementation step: connect model adapters, tools, permissions, "
                "memory retrieval, and artifact generation."
            ),
            mode=mode,
        )
