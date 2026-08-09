from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from loro.config import MCPConfig

TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


class MCPTaskError(RuntimeError):
    """Raised when an MCP task cannot be validated, resumed, or updated."""


class MCPTaskHandle(BaseModel):
    server_id: str
    task_id: str
    status: TaskStatus
    operation: str
    name: str | None = None
    remote: dict[str, Any]
    answered_input_keys: list[str] = Field(default_factory=list)
    cancellation_requested_at: str | None = None
    first_seen_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_seen_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES


class MCPTaskStore:
    def __init__(self, root: str | Path, *, max_record_bytes: int = 1_000_000) -> None:
        self.root = Path(root).expanduser()
        self.max_record_bytes = max_record_bytes

    @classmethod
    def from_config(cls, config: MCPConfig) -> MCPTaskStore:
        return cls(config.task_store_path, max_record_bytes=config.max_output_bytes)

    def save(self, handle: MCPTaskHandle) -> MCPTaskHandle:
        serialized = json.dumps(handle.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
        if len(serialized.encode("utf-8")) > self.max_record_bytes:
            raise MCPTaskError(
                f"MCP task handle exceeded managed limit of {self.max_record_bytes} bytes."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        existing = self.find(handle.server_id, handle.task_id)
        if existing is not None:
            if existing.terminal and handle.status != existing.status:
                raise MCPTaskError(
                    "MCP task server attempted to change an observed terminal status."
                )
            handle.first_seen_at = existing.first_seen_at
            handle.answered_input_keys = list(
                dict.fromkeys(existing.answered_input_keys + handle.answered_input_keys)
            )
            handle.cancellation_requested_at = (
                handle.cancellation_requested_at or existing.cancellation_requested_at
            )
        handle.last_seen_at = datetime.now(UTC).isoformat()
        path = self._path(handle.server_id, handle.task_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(handle.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return handle

    def find(self, server_id: str, task_id: str) -> MCPTaskHandle | None:
        path = self._path(server_id, task_id)
        if not path.exists():
            return None
        return MCPTaskHandle.model_validate_json(path.read_text(encoding="utf-8"))

    def get(self, server_id: str, task_id: str) -> MCPTaskHandle:
        handle = self.find(server_id, task_id)
        if handle is None:
            raise MCPTaskError(f"Unknown local MCP task handle: {server_id}/{task_id}")
        return handle

    def list(self, server_id: str | None = None) -> list[MCPTaskHandle]:
        if not self.root.exists():
            return []
        handles = [
            MCPTaskHandle.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.root.glob("*.json"), reverse=True)
        ]
        if server_id is not None:
            handles = [handle for handle in handles if handle.server_id == server_id]
        return handles

    def record_remote(
        self,
        *,
        server_id: str,
        operation: str,
        remote: dict[str, Any],
        name: str | None = None,
    ) -> MCPTaskHandle:
        task_id = remote.get("taskId") or remote.get("task_id")
        status = remote.get("status")
        if not isinstance(task_id, str) or not task_id:
            raise MCPTaskError("MCP task response is missing taskId.")
        if status not in {
            "working",
            "input_required",
            "completed",
            "failed",
            "cancelled",
        }:
            raise MCPTaskError(f"MCP task response has invalid status: {status!r}")
        _validate_detailed_task(status, remote)
        return self.save(
            MCPTaskHandle(
                server_id=server_id,
                task_id=task_id,
                status=status,
                operation=operation,
                name=name,
                remote=remote,
            )
        )

    def mark_answered(
        self, server_id: str, task_id: str, response_keys: list[str]
    ) -> MCPTaskHandle:
        handle = self.get(server_id, task_id)
        self.validate_response_keys(handle, response_keys)
        handle.answered_input_keys.extend(response_keys)
        return self.save(handle)

    def validate_response_keys(self, handle: MCPTaskHandle, response_keys: list[str]) -> None:
        if not response_keys:
            raise MCPTaskError("MCP task input response cannot be empty.")
        duplicate = sorted(set(response_keys) & set(handle.answered_input_keys))
        if duplicate:
            raise MCPTaskError("MCP task input keys were already answered: " + ", ".join(duplicate))
        outstanding = handle.remote.get("inputRequests", {})
        unknown = sorted(key for key in response_keys if key not in outstanding)
        if unknown:
            raise MCPTaskError("MCP task input keys are not outstanding: " + ", ".join(unknown))

    def mark_cancellation_requested(self, server_id: str, task_id: str) -> MCPTaskHandle:
        handle = self.get(server_id, task_id)
        handle.cancellation_requested_at = datetime.now(UTC).isoformat()
        return self.save(handle)

    def _path(self, server_id: str, task_id: str) -> Path:
        digest = hashlib.sha256(f"{server_id}\0{task_id}".encode()).hexdigest()
        return self.root / f"{digest}.json"


def create_sdk_tasks_extension(
    on_task: Any, extension_settings: dict[str, Any] | None = None
) -> Any:
    sdk = _sdk_types()
    ClientExtension = sdk["ClientExtension"]
    ResultClaim = sdk["ResultClaim"]
    CreateTaskResult = sdk["CreateTaskResult"]
    types = sdk["types"]

    class TasksExtension(ClientExtension):
        identifier = "io.modelcontextprotocol/tasks"

        def settings(self) -> dict[str, Any]:
            return dict(extension_settings or {})

        def claims(self) -> list[Any]:
            async def resolve(result: Any, context: Any) -> Any:
                payload = _model_payload(result)
                stored = on_task(payload, context.tool_name)
                if inspect.isawaitable(stored):
                    await stored
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            type="text",
                            text=(
                                "MCP task created: "
                                f"{payload['taskId']} ({payload['status']}). "
                                "Use the Loro MCP task commands to resume it."
                            ),
                        )
                    ],
                    is_error=False,
                )

            return [
                ResultClaim(
                    result_type="task",
                    model=CreateTaskResult,
                    resolve=resolve,
                    protocol_versions=frozenset({"2026-07-28"}),
                )
            ]

    return TasksExtension()


async def sdk_start_task(client: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.session.call_tool(
        name,
        arguments,
        allow_claimed=True,
    )
    return _model_payload(result)


async def sdk_get_task(client: Any, task_id: str) -> dict[str, Any]:
    sdk = _sdk_types()
    request = sdk["GetTaskRequest"](params=sdk["TaskIdParams"](task_id=task_id))
    result = await client.session.send_request(request, sdk["DetailedTaskResult"])
    return _model_payload(result)


async def sdk_update_task(
    client: Any, task_id: str, input_responses: dict[str, Any]
) -> dict[str, Any]:
    sdk = _sdk_types()
    request = sdk["UpdateTaskRequest"](
        params=sdk["UpdateTaskParams"](
            task_id=task_id,
            input_responses=input_responses,
        )
    )
    result = await client.session.send_request(request, sdk["types"].EmptyResult)
    return _model_payload(result)


async def sdk_cancel_task(client: Any, task_id: str) -> dict[str, Any]:
    sdk = _sdk_types()
    request = sdk["CancelTaskRequest"](params=sdk["TaskIdParams"](task_id=task_id))
    result = await client.session.send_request(request, sdk["types"].EmptyResult)
    return _model_payload(result)


_SDK_TYPE_CACHE: dict[str, Any] | None = None


def _sdk_types() -> dict[str, Any]:
    global _SDK_TYPE_CACHE
    if _SDK_TYPE_CACHE is not None:
        return _SDK_TYPE_CACHE

    from typing import Literal as TypingLiteral

    from mcp import types
    from mcp.client.extension import ClientExtension, ResultClaim
    from pydantic import ConfigDict
    from pydantic import model_validator as sdk_model_validator

    class TaskFields(types.Result):
        # inputRequests is reserved by the SDK core result surface. Keeping
        # extension extras preserves it without declaring a colliding claim field.
        model_config = ConfigDict(extra="allow")

        task_id: str
        status: TaskStatus
        status_message: str | None = None
        created_at: str
        last_updated_at: str
        ttl_ms: float | None
        poll_interval_ms: float | None = None
        result: dict[str, Any] | None = None
        error: dict[str, Any] | None = None

        @sdk_model_validator(mode="after")
        def validate_status_payload(self) -> Any:
            _validate_detailed_task(self.status, self.model_dump(by_alias=True))
            return self

    class CreateTaskResult(TaskFields):
        result_type: TypingLiteral["task"] = "task"

    class DetailedTaskResult(TaskFields):
        result_type: TypingLiteral["complete"] = "complete"

    class TaskIdParams(types.RequestParams):
        task_id: str

    class UpdateTaskParams(TaskIdParams):
        input_responses: dict[str, Any]

    class GetTaskRequest(types.Request[TaskIdParams, TypingLiteral["tasks/get"]]):
        method: TypingLiteral["tasks/get"] = "tasks/get"
        params: TaskIdParams
        name_param = "taskId"

    class UpdateTaskRequest(types.Request[UpdateTaskParams, TypingLiteral["tasks/update"]]):
        method: TypingLiteral["tasks/update"] = "tasks/update"
        params: UpdateTaskParams
        name_param = "taskId"

    class CancelTaskRequest(types.Request[TaskIdParams, TypingLiteral["tasks/cancel"]]):
        method: TypingLiteral["tasks/cancel"] = "tasks/cancel"
        params: TaskIdParams
        name_param = "taskId"

    _SDK_TYPE_CACHE = {
        "types": types,
        "ClientExtension": ClientExtension,
        "ResultClaim": ResultClaim,
        "CreateTaskResult": CreateTaskResult,
        "DetailedTaskResult": DetailedTaskResult,
        "TaskIdParams": TaskIdParams,
        "UpdateTaskParams": UpdateTaskParams,
        "GetTaskRequest": GetTaskRequest,
        "UpdateTaskRequest": UpdateTaskRequest,
        "CancelTaskRequest": CancelTaskRequest,
    }
    return _SDK_TYPE_CACHE


def _validate_detailed_task(status: str, payload: dict[str, Any]) -> None:
    if status == "input_required" and not isinstance(
        payload.get("inputRequests") or payload.get("input_requests"), dict
    ):
        raise MCPTaskError("input_required MCP task is missing inputRequests.")
    if status == "completed" and not isinstance(payload.get("result"), dict):
        raise MCPTaskError("completed MCP task is missing result.")
    if status == "failed" and not isinstance(payload.get("error"), dict):
        raise MCPTaskError("failed MCP task is missing error.")


def _model_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json", exclude_none=True)
    if isinstance(value, dict):
        return value
    raise MCPTaskError(f"Unsupported MCP task result type: {type(value).__name__}")
