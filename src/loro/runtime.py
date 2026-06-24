from dataclasses import dataclass

from loro.audit import AuditLogger, prompt_preview
from loro.config import LoroConfig
from loro.memory.local import LocalMemoryStore
from loro.sessions import SessionRecord, SessionStore


@dataclass(frozen=True)
class AgentResult:
    summary: str
    mode: str
    session_id: str
    recalled_memories: list[str]


class AgentRuntime:
    """Small runtime placeholder for the scaffolded agent loop."""

    def __init__(self, config: LoroConfig) -> None:
        self.config = config
        self.audit = AuditLogger(config.audit)
        self.sessions = SessionStore(config.sessions)

    def run(self, prompt: str, mode: str) -> AgentResult:
        recalled_memories: list[str] = []
        if self.config.memory.local.enabled:
            store = LocalMemoryStore.from_config(self.config.memory.local)
            recalled_memories = [memory.content for memory in store.search(prompt)]
        self.audit.write(
            "runtime.task_started",
            mode=mode,
            model_provider=self.config.model.provider,
            prompt_preview=prompt_preview(prompt)
            if self.config.audit.include_prompt_preview
            else None,
        )
        memory_section = ""
        if recalled_memories:
            memory_section = "\n\nRecalled local memories:\n" + "\n".join(
                f"- {memory}" for memory in recalled_memories
            )
        summary = (
            f"Loro {mode} mode is scaffolded.\n\n"
            f"Provider: {self.config.model.provider} / {self.config.model.model}\n\n"
            f"Prompt: {prompt}"
            f"{memory_section}\n\n"
            "Next implementation step: connect model adapters, tools, permissions, "
            "and governed shared memory retrieval."
        )
        record = self.sessions.save(
            SessionRecord(
                prompt=prompt,
                mode=mode,
                summary=summary,
                recalled_memories=recalled_memories,
            )
        )
        self.audit.write(
            "runtime.task_completed",
            mode=mode,
            session_id=record.session_id,
            recalled_memory_count=len(recalled_memories),
        )
        return AgentResult(
            summary=summary,
            mode=mode,
            session_id=record.session_id,
            recalled_memories=recalled_memories,
        )
