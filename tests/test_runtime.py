from loro.config import AuditConfig, LocalMemoryConfig, LoroConfig, MemoryConfig, RuntimeConfig
from loro.memory.base import SharedMemorySearchRecord, SharedMemorySearchResult
from loro.models import ModelMessage, ModelProviderError, ModelResponse
from loro.runtime import AgentRuntime
from loro.sessions import SessionConfig


class SequencedModelClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.messages: list[list[ModelMessage]] = []

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        self.messages.append(messages)
        if len(self.messages) <= len(self.responses):
            return ModelResponse(content=self.responses[len(self.messages) - 1])
        return ModelResponse(content=self.responses[-1])


class FailingModelClient:
    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        raise ModelProviderError("provider unavailable")


def test_runtime_executes_model_requested_tool(tmp_path, monkeypatch) -> None:
    note = tmp_path / "note.txt"
    note.write_text("hello from runtime\n", encoding="utf-8")
    client = SequencedModelClient(
        [
            f'@tool {{"name": "file.read", "args": {{"path": "{note}", "limit": 50}}}}',
            "Final answer after reading the file.",
        ]
    )
    monkeypatch.setattr("loro.runtime.create_model_client", lambda config: client)
    runtime = AgentRuntime(_runtime_config(tmp_path, max_steps=3))

    result = runtime.run("Read the note and summarize it.", mode="run")

    assert result.stop_reason == "completed"
    assert result.steps == 2
    assert len(result.tool_executions) == 1
    assert result.tool_executions[0].output == "hello from runtime\n"
    assert "Final answer after reading the file." in result.summary
    assert "Tool results" in client.messages[1][-1].content


def test_runtime_stops_at_max_steps(tmp_path, monkeypatch) -> None:
    note = tmp_path / "note.txt"
    note.write_text("loop\n", encoding="utf-8")
    client = SequencedModelClient(
        [f'@tool {{"name": "file.read", "args": {{"path": "{note}", "limit": 10}}}}']
    )
    monkeypatch.setattr("loro.runtime.create_model_client", lambda config: client)
    runtime = AgentRuntime(_runtime_config(tmp_path, max_steps=2))

    result = runtime.run("Keep asking for the note.", mode="run")

    assert result.stop_reason == "max_steps"
    assert result.steps == 2
    assert len(result.tool_executions) == 2


def test_runtime_recalls_shared_memory_with_citation(tmp_path, monkeypatch) -> None:
    client = SequencedModelClient(["Final answer with memory."])
    shared_record = SharedMemorySearchRecord(
        memory_id="mem-1",
        tenant_id="default",
        scope_type="team",
        scope_key="platform",
        memory_type="fact",
        content="Use the enterprise launch readiness template.",
        summary="Launch readiness template",
        classification="public-internal",
        created_by="alex",
        created_at="2026-07-14T00:00:00+00:00",
        status="active",
        backend="postgres",
    )

    def fake_search(config, *, query, tenant_id, limit, execute):
        return SharedMemorySearchResult(
            backend="postgres",
            query=query,
            tenant_id=tenant_id,
            executed=True,
            records=[shared_record],
        )

    monkeypatch.setattr("loro.runtime.create_model_client", lambda config: client)
    monkeypatch.setattr("loro.runtime.search_shared_memories", fake_search)
    config = _runtime_config(tmp_path, max_steps=3)
    config.memory.shared.enabled = True
    runtime = AgentRuntime(config)

    result = runtime.run("Prepare launch checklist", mode="run")

    assert result.recalled_shared_memories == [shared_record]
    assert "postgres:default/team/platform/mem-1" in result.summary
    assert "Use the enterprise launch readiness template" in client.messages[0][0].content


def test_runtime_returns_provider_error_stop_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("loro.runtime.create_model_client", lambda config: FailingModelClient())
    runtime = AgentRuntime(_runtime_config(tmp_path, max_steps=3))
    result = runtime.run("hello", mode="run")
    assert result.stop_reason == "provider_error"
    assert "provider unavailable" in result.summary


def _runtime_config(tmp_path, *, max_steps: int) -> LoroConfig:
    return LoroConfig(
        runtime=RuntimeConfig(max_steps=max_steps),
        memory=MemoryConfig(local=LocalMemoryConfig(enabled=False)),
        audit=AuditConfig(path=str(tmp_path / "audit.jsonl")),
        sessions=SessionConfig(path=str(tmp_path / "sessions")),
    )
