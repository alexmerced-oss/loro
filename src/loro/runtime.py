from dataclasses import dataclass

from loro.audit import AuditLogger, prompt_preview
from loro.config import LoroConfig


@dataclass(frozen=True)
class AgentResult:
    summary: str
    mode: str


class AgentRuntime:
    """Small runtime placeholder for the scaffolded agent loop."""

    def __init__(self, config: LoroConfig) -> None:
        self.config = config
        self.audit = AuditLogger(config.audit)

    def run(self, prompt: str, mode: str) -> AgentResult:
        self.audit.write(
            "runtime.task_started",
            mode=mode,
            model_provider=self.config.model.provider,
            prompt_preview=prompt_preview(prompt)
            if self.config.audit.include_prompt_preview
            else None,
        )
        return AgentResult(
            summary=(
                f"Loro {mode} mode is scaffolded.\n\n"
                f"Prompt: {prompt}\n"
                "Next implementation step: connect model adapters, tools, permissions, "
                "memory retrieval, and artifact generation."
            ),
            mode=mode,
        )
