from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from typer.testing import CliRunner

from loro.config import MCPConfig, MCPExtensionConfig, MCPServerConfig
from loro.mcp.client import MCPService, MCPTaskApprovalError
from loro.mcp.extensions import TASKS_EXTENSION_ID, MCPExtensionRegistry
from loro.mcp.tasks import MCPTaskError, MCPTaskStore


def task_config(tmp_path, *, extension_id: str = TASKS_EXTENSION_ID) -> MCPConfig:
    return MCPConfig(
        enabled=True,
        task_store_path=str(tmp_path / "tasks"),
        extensions={extension_id: MCPExtensionConfig(version="draft", adapter="tasks")},
        servers={
            "fixture": MCPServerConfig(
                command="fixture-server",
                allowed_protocol_versions=["2026-07-28"],
                extensions=[extension_id],
            )
        },
    )


class FakeTaskClient:
    protocol_version = "2026-07-28"
    server_info = {"name": "task-fixture"}

    def __init__(self) -> None:
        self.server_capabilities = {"extensions": {TASKS_EXTENSION_ID: {}}}
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.cancellations: list[str] = []

    async def start_task(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return task_payload(status="working")

    async def get_task(self, task_id: str) -> dict[str, Any]:
        assert task_id == "task-1"
        return task_payload(status="completed", result={"value": "done"})

    async def update_task(self, task_id: str, responses: dict[str, Any]) -> dict[str, Any]:
        self.updates.append((task_id, responses))
        return {"resultType": "complete"}

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        self.cancellations.append(task_id)
        return {"resultType": "complete"}

    def listen(self, **_filters: Any) -> Any:
        @asynccontextmanager
        async def subscription():
            async def events():
                for index in range(5):
                    yield {"method": "notifications/tools/list_changed", "index": index}

            yield events()

        return subscription()


def task_payload(*, status: str, **details: Any) -> dict[str, Any]:
    return {
        "resultType": "task" if status == "working" else "complete",
        "taskId": "task-1",
        "status": status,
        "createdAt": "2026-08-09T00:00:00Z",
        "lastUpdatedAt": "2026-08-09T00:00:01Z",
        "ttlMs": 60_000,
        **details,
    }


def factory(client: FakeTaskClient):
    @asynccontextmanager
    async def connect(_server: MCPServerConfig):
        yield client

    return connect


def test_extension_registry_keeps_unknown_and_disallowed_extensions_inert(tmp_path) -> None:
    unknown = "com.example/unknown"
    config = task_config(tmp_path, extension_id=unknown)
    status = MCPExtensionRegistry(config).statuses(config.servers["fixture"])[0]
    assert status.active is False
    assert "inert" in (status.reason or "")

    managed = task_config(tmp_path).model_copy(update={"allowed_extensions": [unknown]})
    status = MCPExtensionRegistry(managed).statuses(managed.servers["fixture"])[0]
    assert status.active is False
    assert "allowlist" in (status.reason or "")


def test_task_store_path_cannot_be_empty() -> None:
    with pytest.raises(ValueError, match="task_store_path"):
        MCPConfig(task_store_path="  ")


def test_extension_registry_validates_settings_schema(tmp_path) -> None:
    config = task_config(tmp_path)
    config.extensions[TASKS_EXTENSION_ID] = MCPExtensionConfig(
        version="draft",
        adapter="tasks",
        settings={"poll_seconds": "fast"},
        settings_schema={
            "type": "object",
            "properties": {"poll_seconds": {"type": "number"}},
        },
    )
    status = MCPExtensionRegistry(config).statuses(config.servers["fixture"])[0]
    assert status.active is False
    assert "schema validation" in (status.reason or "")


def test_extension_registry_rejects_unsupported_adapter_version(tmp_path) -> None:
    config = task_config(tmp_path)
    config.extensions[TASKS_EXTENSION_ID] = MCPExtensionConfig(version="v99", adapter="tasks")
    status = MCPExtensionRegistry(config).statuses(config.servers["fixture"])[0]
    assert status.implemented is True
    assert status.version_supported is False
    assert status.active is False


@pytest.mark.asyncio
async def test_task_handle_resumes_across_service_instances(tmp_path) -> None:
    client = FakeTaskClient()
    config = task_config(tmp_path)
    first = MCPService(config, client_factory=factory(client))
    created = await first.start_task("fixture", "build-report", {"quarter": "Q2"})

    resumed = MCPService(config, client_factory=factory(client))
    completed = await resumed.get_task("fixture", created["task"]["task_id"])

    assert created["task_created"] is True
    assert completed["task"]["status"] == "completed"
    assert completed["task"]["remote"]["result"]["value"] == "done"


@pytest.mark.asyncio
async def test_task_input_requires_approval_and_deduplicates_before_remote_call(tmp_path) -> None:
    client = FakeTaskClient()
    service = MCPService(task_config(tmp_path), client_factory=factory(client))
    service.task_store.record_remote(
        server_id="fixture",
        operation="tools/call",
        name="build-report",
        remote=task_payload(status="input_required", inputRequests={"format": {}}),
    )

    with pytest.raises(MCPTaskApprovalError):
        await service.update_task("fixture", "task-1", {"format": "pptx"})
    await service.update_task("fixture", "task-1", {"format": "pptx"}, user_approved=True)
    with pytest.raises(MCPTaskError, match="already answered"):
        await service.update_task("fixture", "task-1", {"format": "pdf"}, user_approved=True)

    assert client.updates == [("task-1", {"format": "pptx"})]


@pytest.mark.asyncio
async def test_task_cancellation_records_intent_without_claiming_terminal_state(tmp_path) -> None:
    client = FakeTaskClient()
    service = MCPService(task_config(tmp_path), client_factory=factory(client))
    service.task_store.record_remote(
        server_id="fixture",
        operation="tools/call",
        remote=task_payload(status="working"),
    )

    result = await service.cancel_task("fixture", "task-1", user_approved=True)

    assert result["cooperative"] is True
    assert result["task"]["status"] == "working"
    assert result["task"]["cancellation_requested_at"] is not None
    assert client.cancellations == ["task-1"]


@pytest.mark.asyncio
async def test_listen_changes_stops_at_managed_event_limit(tmp_path) -> None:
    client = FakeTaskClient()
    service = MCPService(task_config(tmp_path), client_factory=factory(client))

    result = await service.listen_changes("fixture", tools=True, max_events=2, max_seconds=1)

    assert result["event_count"] == 2
    assert result["timed_out"] is False


def test_task_store_rejects_empty_and_unknown_input_keys(tmp_path) -> None:
    store = MCPTaskStore(tmp_path)
    handle = store.record_remote(
        server_id="fixture",
        operation="tools/call",
        remote=task_payload(status="input_required", inputRequests={"format": {}}),
    )
    with pytest.raises(MCPTaskError, match="cannot be empty"):
        store.validate_response_keys(handle, [])
    with pytest.raises(MCPTaskError, match="not outstanding"):
        store.validate_response_keys(handle, ["destination"])


def test_task_store_rejects_oversized_remote_handle_before_disk_write(tmp_path) -> None:
    store = MCPTaskStore(tmp_path, max_record_bytes=1024)
    with pytest.raises(MCPTaskError, match="managed limit"):
        store.record_remote(
            server_id="fixture",
            operation="tools/call",
            remote=task_payload(status="working", statusMessage="x" * 2000),
        )
    assert list(tmp_path.iterdir()) == []


def test_task_store_rejects_terminal_status_regression(tmp_path) -> None:
    store = MCPTaskStore(tmp_path)
    store.record_remote(
        server_id="fixture",
        operation="tools/call",
        remote=task_payload(status="completed", result={"value": "done"}),
    )
    with pytest.raises(MCPTaskError, match="terminal status"):
        store.record_remote(
            server_id="fixture",
            operation="tools/call",
            remote=task_payload(status="working"),
        )


def test_cli_task_cancellation_preserves_audit_continuity(tmp_path, monkeypatch) -> None:
    from loro.cli import app

    client = FakeTaskClient()
    config = task_config(tmp_path)
    service = MCPService(config, client_factory=factory(client))
    service.task_store.record_remote(
        server_id="fixture",
        operation="tools/call",
        remote=task_payload(status="working"),
    )
    monkeypatch.setattr("loro.cli._mcp_service", lambda: service)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[mcp]\nenabled = true\n"
        'task_store_path = ".loro/mcp-tasks"\n'
        '[mcp.extensions."io.modelcontextprotocol/tasks"]\n'
        'version = "draft"\nadapter = "tasks"\n'
        '[mcp.servers.fixture]\ncommand = "fixture-server"\n'
        'extensions = ["io.modelcontextprotocol/tasks"]\n'
        '[permissions]\nmcp = "ask"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )

    result = CliRunner().invoke(app, ["mcp", "task-cancel", "fixture", "task-1"], input="once\n")

    assert result.exit_code == 0, result.stdout
    events = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "mcp.request_started" in events
    assert "mcp.request_completed" in events
    assert client.cancellations == ["task-1"]


def test_cli_registers_and_attaches_tasks_extension(tmp_path, monkeypatch) -> None:
    from loro.cli import app
    from loro.config import load_config

    output = tmp_path / "config.toml"
    monkeypatch.setenv("LORO_CONFIG_CONTENT", f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n')
    runner = CliRunner()
    registered = runner.invoke(
        app,
        [
            "mcp",
            "extension-add",
            TASKS_EXTENSION_ID,
            "--version",
            "draft",
            "--adapter",
            "tasks",
            "--settings",
            '{"notifications":false}',
            "--output",
            str(output),
        ],
    )
    assert registered.exit_code == 0, registered.stdout

    monkeypatch.setenv("LORO_CONFIG", str(output))
    monkeypatch.delenv("LORO_CONFIG_CONTENT")
    attached = runner.invoke(
        app,
        [
            "mcp",
            "add",
            "fixture",
            "--command",
            "fixture-server",
            "--extension",
            TASKS_EXTENSION_ID,
            "--output",
            str(output),
        ],
    )
    assert attached.exit_code == 0, attached.stdout

    config = load_config()
    assert config.mcp.extensions[TASKS_EXTENSION_ID].settings == {"notifications": False}
    assert config.mcp.servers["fixture"].extensions == [TASKS_EXTENSION_ID]


def test_mcp_setup_wizard_can_enable_tasks_extension(tmp_path, monkeypatch) -> None:
    from loro.cli import app
    from loro.config import load_config

    output = tmp_path / "config.toml"
    monkeypatch.setenv("LORO_CONFIG_CONTENT", f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n')
    result = CliRunner().invoke(
        app,
        ["setup", "mcp", "--output", str(output)],
        input="y\nfixture\n\n\n\nfixture-server\n\n\ny\n",
    )
    assert result.exit_code == 0, result.stdout

    monkeypatch.setenv("LORO_CONFIG", str(output))
    monkeypatch.delenv("LORO_CONFIG_CONTENT")
    config = load_config()
    assert config.mcp.extensions[TASKS_EXTENSION_ID].adapter == "tasks"
    assert config.mcp.servers["fixture"].extensions == [TASKS_EXTENSION_ID]
