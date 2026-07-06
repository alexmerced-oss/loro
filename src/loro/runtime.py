from dataclasses import dataclass

from loro.audit import AuditLogger, prompt_preview
from loro.config import LoroConfig
from loro.memory.local import LocalMemoryStore
from loro.models import ModelMessage, create_model_client
from loro.sessions import SessionRecord, SessionStore
from loro.tool_runtime import ToolExecution, ToolRegistry, parse_tool_calls


@dataclass(frozen=True)
class AgentResult:
    summary: str
    mode: str
    session_id: str
    recalled_memories: list[str]
    tool_executions: list[ToolExecution]


class AgentRuntime:
    """Small runtime placeholder for the scaffolded agent loop."""

    def __init__(self, config: LoroConfig) -> None:
        self.config = config
        self.audit = AuditLogger(config.audit)
        self.sessions = SessionStore(config.sessions)
        self.tools = ToolRegistry(config)

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
        tool_executions = [self.tools.execute(call) for call in parse_tool_calls(prompt)]
        for execution in tool_executions:
            self.audit.write(
                "runtime.tool_executed",
                tool=execution.call.name,
                ok=execution.ok,
            )
        tool_section = ""
        if tool_executions:
            tool_section = "\n\nTool results:\n" + "\n\n".join(
                _format_tool_execution(execution) for execution in tool_executions
            )
        model_response = create_model_client(self.config.model).complete(
            [ModelMessage(role="user", content=prompt)]
        )
        summary = (
            f"Loro {mode} mode is scaffolded.\n\n"
            f"Provider: {self.config.model.provider} / {self.config.model.model}\n\n"
            f"Prompt: {prompt}"
            f"{memory_section}"
            f"{tool_section}\n\n"
            f"Model response: {model_response.content}\n\n"
            "Next implementation step: connect model-directed tool calls and governed "
            "shared memory retrieval."
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
            tool_execution_count=len(tool_executions),
        )
        return AgentResult(
            summary=summary,
            mode=mode,
            session_id=record.session_id,
            recalled_memories=recalled_memories,
            tool_executions=tool_executions,
        )


def _format_tool_execution(execution: ToolExecution) -> str:
    status = "ok" if execution.ok else "error"
    return f"[{status}] {execution.call.name}\n{execution.output}"
