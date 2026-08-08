from dataclasses import dataclass

from loro.audit import AuditLogger, prompt_preview
from loro.config import LoroConfig
from loro.identity import IdentityContext, resolve_identity
from loro.memory.base import SharedMemorySearchRecord
from loro.memory.local import LocalMemoryStore
from loro.memory.operations import search_shared_memories
from loro.model_tools import ModelToolCall
from loro.models import ModelMessage, ModelProviderError, create_model_client
from loro.sessions import SessionRecord, SessionStore
from loro.tool_runtime import ToolCall, ToolExecution, ToolRegistry, parse_tool_calls


@dataclass(frozen=True)
class AgentResult:
    summary: str
    mode: str
    session_id: str
    recalled_memories: list[str]
    recalled_shared_memories: list[SharedMemorySearchRecord]
    tool_executions: list[ToolExecution]
    stop_reason: str
    steps: int


class AgentRuntime:
    """Bounded model-directed runtime loop for Loro agent tasks."""

    def __init__(self, config: LoroConfig) -> None:
        self.config = config
        self.identity = resolve_identity(config.identity)
        self.audit = AuditLogger(config.audit, self.identity)
        self.sessions = SessionStore(config.sessions)
        self.tools = ToolRegistry(config, identity=self.identity)

    def run(self, prompt: str, mode: str) -> AgentResult:
        recalled_memories: list[str] = []
        if self.config.memory.local.enabled:
            store = LocalMemoryStore.from_config(self.config.memory.local)
            recalled_memories = [memory.content for memory in store.search(prompt)]
        recalled_shared_memories = self._recall_shared_memories(prompt)
        self.audit.write(
            "runtime.task_started",
            mode=mode,
            model_provider=self.config.model.provider,
            prompt_preview=prompt_preview(prompt)
            if self.config.audit.include_prompt_preview
            else None,
        )
        memory_section = _format_memory_section(recalled_memories, recalled_shared_memories)
        tool_executions: list[ToolExecution] = []
        tool_executions.extend(self._execute_tool_calls(parse_tool_calls(prompt), step=0))

        messages = [
            ModelMessage(
                role="user",
                content=_initial_model_prompt(
                    prompt=prompt,
                    mode=mode,
                    memory_section=memory_section,
                    tool_executions=tool_executions,
                ),
            )
        ]
        client = create_model_client(self.config.model)
        model_response_content = ""
        stop_reason = "completed"
        steps = 0

        for step in range(1, self.config.runtime.max_steps + 1):
            steps = step
            try:
                model_response = client.complete(messages)
                model_response_content = model_response.content
            except ModelProviderError as error:
                model_response_content = f"Provider error: {error}"
                stop_reason = "provider_error"
                self.audit.write(
                    "runtime.model_failed",
                    mode=mode,
                    step=step,
                    error=str(error),
                )
                break
            self.audit.write("runtime.model_completed", mode=mode, step=step)
            tool_calls = [
                *_tool_calls_from_model_response(model_response.tool_calls),
                *parse_tool_calls(model_response_content),
            ]
            if not tool_calls:
                stop_reason = "completed"
                break
            step_executions = self._execute_tool_calls(tool_calls, step=step)
            tool_executions.extend(step_executions)
            messages.append(ModelMessage(role="assistant", content=model_response_content))
            messages.append(
                ModelMessage(
                    role="user",
                    content=(
                        "Tool results:\n"
                        + "\n\n".join(
                            _format_tool_execution(execution) for execution in step_executions
                        )
                        + "\n\nContinue the task. If you are done, respond without tool directives."
                    ),
                )
            )
        else:
            stop_reason = "max_steps"

        tool_section = _format_tool_section(tool_executions)
        summary = (
            f"Loro {mode} mode completed.\n\n"
            f"Provider: {self.config.model.provider} / {self.config.model.model}\n\n"
            f"Stop reason: {stop_reason}\n"
            f"Steps: {steps}\n\n"
            f"Prompt: {prompt}"
            f"{memory_section}"
            f"{tool_section}\n\n"
            f"Model response: {model_response_content}"
        )
        record = self.sessions.save(
            SessionRecord(
                prompt=prompt,
                mode=mode,
                summary=summary,
                recalled_memories=recalled_memories,
                recalled_shared_memories=[
                    _shared_memory_payload(memory) for memory in recalled_shared_memories
                ],
                tool_executions=[execution.to_payload() for execution in tool_executions],
                identity=self.identity.to_payload(),
                stop_reason=stop_reason,
            )
        )
        self.audit.write(
            "runtime.task_completed",
            mode=mode,
            session_id=record.session_id,
            recalled_memory_count=len(recalled_memories),
            recalled_shared_memory_count=len(recalled_shared_memories),
            tool_execution_count=len(tool_executions),
            stop_reason=stop_reason,
            steps=steps,
        )
        return AgentResult(
            summary=summary,
            mode=mode,
            session_id=record.session_id,
            recalled_memories=recalled_memories,
            recalled_shared_memories=recalled_shared_memories,
            tool_executions=tool_executions,
            stop_reason=stop_reason,
            steps=steps,
        )

    def _recall_shared_memories(self, prompt: str) -> list[SharedMemorySearchRecord]:
        if not self.config.memory.shared.enabled:
            return []
        result = search_shared_memories(
            self.config,
            query=prompt,
            tenant_id=self.identity.tenant,
            limit=5,
            execute=True,
        )
        self.audit.write(
            "runtime.shared_memory_recalled",
            backend=result.backend,
            executed=result.executed,
            record_count=len(result.records),
            messages=result.messages,
            tenant_id=self.identity.tenant,
        )
        return result.records

    def _execute_tool_calls(self, calls: list[ToolCall], *, step: int) -> list[ToolExecution]:
        executions = [self.tools.execute(call) for call in calls]
        for execution in executions:
            self.audit.write(
                "runtime.tool_executed",
                tool=execution.call.name,
                ok=execution.ok,
                step=step,
                tool_identity=_tool_identity_payload(self.identity),
            )
        return executions


def _initial_model_prompt(
    *,
    prompt: str,
    mode: str,
    memory_section: str,
    tool_executions: list[ToolExecution],
) -> str:
    instructions = (
        "You are Loro, a CLI agent harness. "
        "Use tool directives only when you need tool results. "
        'Tool directive format: @tool {"name": "file.read", "args": {"path": "README.md"}}. '
        "When the task is complete, respond without tool directives."
    )
    tool_section = _format_tool_section(tool_executions)
    return (
        f"{instructions}\n\n"
        f"Mode: {mode}\n\n"
        f"User task: {_strip_tool_directives(prompt)}{memory_section}{tool_section}"
    )


def _strip_tool_directives(prompt: str) -> str:
    lines = [line for line in prompt.splitlines() if not line.strip().startswith("@tool ")]
    return "\n".join(lines).strip()


def _format_memory_section(
    recalled_memories: list[str],
    recalled_shared_memories: list[SharedMemorySearchRecord],
) -> str:
    if not recalled_memories and not recalled_shared_memories:
        return ""
    sections: list[str] = []
    if recalled_memories:
        sections.append(
            "Recalled local memories:\n" + "\n".join(f"- {memory}" for memory in recalled_memories)
        )
    if recalled_shared_memories:
        sections.append(
            "Recalled shared memories:\n"
            + "\n".join(
                f"- [{memory.citation}] {memory.summary}: {memory.content}"
                for memory in recalled_shared_memories
            )
        )
    return "\n\n" + "\n\n".join(sections)


def _format_tool_section(tool_executions: list[ToolExecution]) -> str:
    if not tool_executions:
        return ""
    return "\n\nTool results:\n" + "\n\n".join(
        _format_tool_execution(execution) for execution in tool_executions
    )


def _format_tool_execution(execution: ToolExecution) -> str:
    status = "ok" if execution.ok else "error"
    return f"[{status}] {execution.call.name}\n{execution.output}"


def _shared_memory_payload(memory: SharedMemorySearchRecord) -> dict[str, str]:
    return {
        "memory_id": memory.memory_id,
        "citation": memory.citation,
        "tenant_id": memory.tenant_id,
        "scope_type": memory.scope_type,
        "scope_key": memory.scope_key,
        "memory_type": memory.memory_type,
        "summary": memory.summary,
        "classification": memory.classification,
        "created_by": memory.created_by,
        "created_at": memory.created_at,
        "backend": memory.backend,
    }


def _tool_calls_from_model_response(calls: list[ModelToolCall]) -> list[ToolCall]:
    return [ToolCall(name=call.name, args=call.args) for call in calls]


def _tool_identity_payload(identity: IdentityContext) -> dict[str, object]:
    return {
        "subject": identity.subject,
        "tenant": identity.tenant,
        "session_id": identity.session_id,
    }
