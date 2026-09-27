"""An AAIS approval authority stored in Postgres, for multi-user and multi-host servers.

``PostgresApprovalStore`` keeps the exact semantics of ``aais.store.FileApprovalStore`` (it
reuses its transaction, decision, snapshot, replay, owner-liveness and retention logic) and
replaces only persistence and locking: the state for one authority stream is a JSONB row,
and each transaction holds that row's lock (``SELECT ... FOR UPDATE``) for its whole
read-modify-write cycle, so several Web UI processes on several hosts share one approval
queue. Owners on another host report ``UNKNOWN`` liveness and are never treated as stopped.

This subclasses internals of agent-approval-interchange 0.2 (pinned ``<0.3``). The tests in
``tests/integration/test_aais_postgres_integration.py`` exercise the full API against Postgres 16
so an incompatible library change fails loudly.
"""

from __future__ import annotations

import contextlib
import copy
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from aais.store import FileApprovalStore, RetentionPolicy, StoreError

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS loro_aais_state (
    stream text PRIMARY KEY,
    state jsonb NOT NULL,
    version bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
)
"""


def _store_internals() -> tuple[Any, Any]:
    from aais import store as module

    return module._empty_state, module._check_state


class PostgresApprovalStore(FileApprovalStore):
    """``FileApprovalStore`` semantics with Postgres row locking and storage."""

    def __init__(
        self,
        dsn: str,
        *,
        stream: str,
        presenter_stream: str | None = None,
        retention: RetentionPolicy | None = None,
        lock_timeout: float = 30.0,
    ) -> None:
        super().__init__(
            Path(f"postgres/{stream}"),  # never touched on disk; used only in messages
            stream=stream,
            presenter_stream=presenter_stream,
            retention=retention,
            lock_timeout=lock_timeout,
            max_bytes=None,
        )
        self.dsn = dsn
        self._local = threading.local()
        self._schema_ready = False

    # ------------------------------------------------------------ connection

    def _connect(self) -> Any:
        try:
            import psycopg
        except ImportError as error:  # pragma: no cover - depends on the extra
            raise StoreError(
                "The Postgres approval authority needs psycopg: pip install 'loro-agent[data]'."
            ) from error
        try:
            return psycopg.connect(
                self.dsn,
                connect_timeout=10,
                options=f"-c lock_timeout={int(self.lock_timeout * 1000)}",
            )
        except psycopg.Error as error:
            raise StoreError(f"Could not connect to the approval database: {error}") from error

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._connect() as connection:
            connection.execute(SCHEMA_SQL)
        self._schema_ready = True

    # --------------------------------------------------------------- locking

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        if getattr(self._local, "connection", None) is not None:
            raise StoreError("nested approval-store transaction; reuse the open transaction")
        self.ensure_schema()
        import psycopg

        connection = self._connect()
        try:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO loro_aais_state (stream, state) VALUES (%s, %s::jsonb) "
                    "ON CONFLICT (stream) DO NOTHING",
                    (self.stream, json.dumps(None)),
                )
                row = connection.execute(
                    "SELECT state, version FROM loro_aais_state WHERE stream = %s FOR UPDATE",
                    (self.stream,),
                ).fetchone()
                self._local.connection = connection
                self._local.row = row
                try:
                    yield
                finally:
                    self._local.connection = None
                    self._local.row = None
        except psycopg.errors.LockNotAvailable as error:
            raise StoreError(
                f"timed out after {self.lock_timeout:g}s waiting for the approval row lock"
            ) from error
        except psycopg.Error as error:
            raise StoreError(f"Approval database error: {error}") from error
        finally:
            connection.close()

    # -------------------------------------------------------------- storage

    def _raise_if_marked(self) -> None:
        return None

    def _load(self, *, mutable: bool = True) -> dict[str, Any]:
        empty_state, check_state = _store_internals()
        row = getattr(self._local, "row", None)
        value = row[0] if row else None
        if value is None:
            return empty_state()
        reason = check_state(value)
        if reason is not None:
            raise StoreError(f"Approval state for stream {self.stream} is invalid: {reason}")
        return copy.deepcopy(value) if mutable else value

    def _save(self, state: dict[str, Any]) -> None:
        import uuid

        if not state["store_id"]:
            state["store_id"] = uuid.uuid4().hex
        connection = self._local.connection
        connection.execute(
            "UPDATE loro_aais_state SET state = %s::jsonb, version = version + 1, "
            "updated_at = now() WHERE stream = %s",
            (json.dumps(state, sort_keys=True, separators=(",", ":")), self.stream),
        )

    def _signature(self) -> tuple[int, int, int, int] | None:
        """A cheap change marker for ``wait_for_resolution``: the row version."""

        self.ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT version FROM loro_aais_state WHERE stream = %s", (self.stream,)
            ).fetchone()
        return None if row is None else (int(row[0]), 0, 0, 0)

    def _read(self) -> dict[str, Any]:
        with self._locked():
            return self._load(mutable=True)

    def wait_for_resolution(  # type: ignore[override]
        self,
        request_id: str,
        *,
        timeout: float,
        poll_interval: float = 0.25,
        cancelled: threading.Event | None = None,
        refresh_interval: float = 2.0,
    ) -> dict[str, Any] | None:
        """Poll the row version and re-read only when it changed."""

        from aais.store import UnknownRequestError

        deadline = time.monotonic() + max(0.0, timeout)
        last: tuple[int, int, int, int] | None | bool = False
        while True:
            signature = self._signature()
            if signature != last:
                last = signature
                state = self._read()
                resolution = state["resolutions"].get(request_id)
                if resolution is not None:
                    return dict(copy.deepcopy(resolution))
                if request_id not in state["pending"]:
                    raise UnknownRequestError(f"unknown approval request: {request_id}")
            if cancelled is not None and cancelled.is_set():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            pause = min(max(poll_interval, 0.01), remaining)
            if cancelled is not None:
                cancelled.wait(pause)
            else:
                time.sleep(pause)

    # ------------------------------------------------------------- recovery

    def recovery_status(self) -> Any:
        return None  # Postgres guarantees a well-formed row; there is no quarantine state.

    def acknowledge_recovery(self, *, start_sequence: int | None = None) -> None:
        return None

    def exists(self) -> bool:
        self.ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state IS NOT NULL AND state <> 'null'::jsonb FROM loro_aais_state "
                "WHERE stream = %s",
                (self.stream,),
            ).fetchone()
        return bool(row and row[0])
