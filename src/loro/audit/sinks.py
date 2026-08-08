from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from time import sleep
from typing import Any, Protocol

import httpx


class AuditSinkError(RuntimeError):
    """Raised when an audit sink cannot accept an event."""


class AuditBufferFullError(AuditSinkError):
    """Raised when a bounded audit buffer cannot retain another event."""


class AuditSink(Protocol):
    name: str

    def deliver(self, payload: dict[str, Any]) -> None: ...


class JsonlAuditSink:
    name = "jsonl"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def deliver(self, payload: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, default=str) + "\n")
        except OSError as error:
            raise AuditSinkError(f"JSONL audit delivery failed: {error}") from error


class HttpAuditSink:
    name = "http"

    def __init__(
        self,
        *,
        url: str,
        token_env: str | None = None,
        timeout_seconds: float = 10,
        max_retries: int = 2,
        backoff_seconds: float = 0.25,
        client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.url = url
        self.token_env = token_env
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.client = client or httpx.Client()
        self.sleep_fn = sleep_fn

    def deliver(self, payload: dict[str, Any]) -> None:
        if not self.url.strip():
            raise AuditSinkError("HTTP audit sink requires a non-empty URL.")
        headers = {"Content-Type": "application/json"}
        if self.token_env:
            token = os.environ.get(self.token_env)
            if not token:
                raise AuditSinkError(
                    f"HTTP audit token environment variable is not set: {self.token_env}"
                )
            headers["Authorization"] = f"Bearer {token}"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.post(
                    self.url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                return
            except (httpx.HTTPError, OSError) as error:
                last_error = error
                if attempt < self.max_retries:
                    self.sleep_fn(self.backoff_seconds * (2**attempt))
        raise AuditSinkError(f"HTTP audit delivery failed: {last_error}") from last_error


class AuditBuffer:
    def __init__(self, path: str | Path, max_events: int) -> None:
        self.path = Path(path).expanduser()
        self.max_events = max_events

    def append(self, payload: dict[str, Any]) -> None:
        events = self.load()
        if len(events) >= self.max_events:
            raise AuditBufferFullError(
                f"Audit buffer is full ({self.max_events} events): {self.path}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, default=str) + "\n")

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise AuditSinkError(
                    f"Invalid audit buffer JSON at line {line_number}: {self.path}"
                ) from error
            if not isinstance(payload, dict):
                raise AuditSinkError(
                    f"Invalid audit buffer event at line {line_number}: {self.path}"
                )
            events.append(payload)
        return events

    def replace(self, events: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            "".join(json.dumps(event, default=str) + "\n" for event in events),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def count(self) -> int:
        return len(self.load())
