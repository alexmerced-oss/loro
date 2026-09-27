"""A Postgres backend for the AAIS approval authority, for multi-user and multi-host servers.

``aais.store.ApprovalAuthority`` owns every AAIS rule (sequences, decisions, replay, owner
liveness, retention, validation, recovery). ``PostgresBackend`` implements only the
``aais.backends`` storage protocols: the state for one key is a JSONB row, and each
transaction holds that row's lock (``SELECT ... FOR UPDATE``) for its whole read-modify-write
cycle, so several Web UI processes on several hosts share one approval queue. ``version``
is a row counter bumped on every commit that changes the row, and a quarantine moves the
damaged document to ``loro_aais_quarantine`` and records the recovery condition in the
``recovery`` column until an operator acknowledges it.

The backend is checked with ``aais.testing.BackendConformance`` in
``tests/integration/test_aais_postgres_integration.py`` against Postgres 16.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Hashable, Iterator
from datetime import UTC, datetime
from typing import Any

from aais.backends import BackendTransaction, LockTimeout, RecoveryRequired, StoreError
from aais.store import ApprovalAuthority, RetentionPolicy

SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS loro_aais_state (
        stream text PRIMARY KEY,
        state jsonb NOT NULL,
        version bigint NOT NULL DEFAULT 0,
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE loro_aais_state ADD COLUMN IF NOT EXISTS recovery jsonb",
    """
    CREATE TABLE IF NOT EXISTS loro_aais_quarantine (
        id bigserial PRIMARY KEY,
        stream text NOT NULL,
        state jsonb,
        reason text NOT NULL,
        detected_at text NOT NULL,
        sequence_hint bigint NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
)

# (dsn, key) pairs with an open transaction on the current thread, across every handle: a
# second handle to the same row on the same thread would otherwise wait on its own lock.
_HELD = threading.local()


def _held() -> set[tuple[str, str]]:
    held: set[tuple[str, str]] | None = getattr(_HELD, "keys", None)
    if held is None:
        held = set()
        _HELD.keys = held
    return held


def _describe(dsn: str, key: str) -> str:
    """A location for messages that never includes credentials."""

    try:
        from psycopg.conninfo import conninfo_to_dict

        parts = conninfo_to_dict(dsn)
    except Exception:  # noqa: BLE001 - psycopg missing or an unparsable DSN
        return f"postgres approval row {key}"
    host = parts.get("host") or parts.get("hostaddr") or "localhost"
    port = parts.get("port") or "5432"
    return f"postgres://{host}:{port}/{parts.get('dbname') or ''} (approval row {key})"


def _why(error: Exception) -> str:
    """The first line of a database error (libpq messages name hosts and users, not secrets)."""

    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


def _condition(description: str, payload: Any) -> RecoveryRequired | None:
    if not isinstance(payload, dict):
        return None
    return RecoveryRequired(
        description,
        reason=str(payload.get("reason", "unknown")),
        quarantined_to=payload.get("quarantined_to"),
        detected_at=str(payload.get("detected_at", "")),
        sequence_hint=int(payload.get("sequence_hint", 0)),
    )


class _PostgresTransaction:
    """The locked row for one transaction; writes commit with the database transaction."""

    def __init__(self, backend: PostgresBackend, connection: Any, row: tuple[Any, ...]) -> None:
        self._backend = backend
        self._connection = connection
        self._state = row[0]
        self._recovery = row[1]

    def recovery_marker(self) -> RecoveryRequired | None:
        return _condition(self._backend.description, self._recovery)

    def load(self) -> Any:
        return self._state

    def save(self, state: dict[str, Any]) -> None:
        text = json.dumps(state, sort_keys=True, separators=(",", ":"))
        self._update("state = %s::jsonb", (text,))
        self._state = json.loads(text)

    def quarantine(
        self, *, reason: str, detected_at: datetime, sequence_hint: int
    ) -> RecoveryRequired:
        stamp = detected_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        row = self._connection.execute(
            "INSERT INTO loro_aais_quarantine (stream, state, reason, detected_at, sequence_hint) "
            "VALUES (%s, %s::jsonb, %s, %s, %s) RETURNING id",
            (self._backend.key, json.dumps(self._state), reason, stamp, sequence_hint),
        ).fetchone()
        payload = {
            "reason": reason,
            "quarantined_to": f"loro_aais_quarantine id {row[0]}",
            "detected_at": stamp,
            "sequence_hint": sequence_hint,
        }
        self._update("state = 'null'::jsonb, recovery = %s::jsonb", (json.dumps(payload),))
        self._state = None
        self._recovery = payload
        condition = _condition(self._backend.description, payload)
        assert condition is not None
        return condition

    def clear_recovery(self) -> None:
        self._update("recovery = NULL", ())
        self._recovery = None

    def _update(self, assignments: str, params: tuple[Any, ...]) -> None:
        self._connection.execute(
            f"UPDATE loro_aais_state SET {assignments}, version = version + 1, "  # noqa: S608
            "updated_at = now() WHERE stream = %s",
            (*params, self._backend.key),
        )


class PostgresBackend:
    """``aais.backends.ApprovalStateBackend`` over one row of ``loro_aais_state``."""

    def __init__(self, dsn: str, key: str, *, lock_timeout: float = 30.0) -> None:
        self.dsn = dsn
        self.key = key
        self.lock_timeout = float(lock_timeout)
        self.description = _describe(dsn, key)
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
                options=f"-c lock_timeout={max(1, int(self.lock_timeout * 1000))}",
            )
        except psycopg.Error as error:
            raise StoreError(
                f"Could not connect to the approval database at {self.description}: {_why(error)}"
            ) from error

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        import psycopg

        try:
            with self._connect() as connection:
                # Serialize concurrent first starts: CREATE ... IF NOT EXISTS can still race.
                connection.execute("SELECT pg_advisory_xact_lock(hashtext('loro_aais_schema'))")
                for statement in SCHEMA_SQL:
                    connection.execute(statement)
        except psycopg.Error as error:
            raise StoreError(f"Approval database error: {_why(error)}") from error
        self._schema_ready = True

    def _scalar(self, query: str) -> Any:
        import psycopg

        self.ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(query, (self.key,)).fetchone()
        except psycopg.Error as error:
            raise StoreError(f"Approval database error: {_why(error)}") from error

    # -------------------------------------------------------------- protocol

    @contextlib.contextmanager
    def transaction(self) -> Iterator[BackendTransaction]:
        marker = (self.dsn, self.key)
        held = _held()
        if marker in held:
            raise StoreError(
                f"nested transaction on {self.description}; reuse the open transaction instead"
            )
        self.ensure_schema()
        import psycopg

        held.add(marker)
        try:
            connection = self._connect()
            try:
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO loro_aais_state (stream, state) VALUES (%s, 'null'::jsonb) "
                        "ON CONFLICT (stream) DO NOTHING",
                        (self.key,),
                    )
                    row = connection.execute(
                        "SELECT state, recovery FROM loro_aais_state WHERE stream = %s FOR UPDATE",
                        (self.key,),
                    ).fetchone()
                    yield _PostgresTransaction(self, connection, row)
            except psycopg.errors.LockNotAvailable as error:
                raise LockTimeout(
                    f"timed out after {self.lock_timeout:g}s waiting for {self.description}"
                ) from error
            except psycopg.Error as error:
                raise StoreError(f"Approval database error: {_why(error)}") from error
            finally:
                connection.close()
        finally:
            held.discard(marker)

    def version(self) -> Hashable | None:
        row = self._scalar("SELECT version FROM loro_aais_state WHERE stream = %s")
        return None if row is None else int(row[0])

    def invalidate(self) -> None:
        return None  # nothing is cached; every transaction reads the locked row

    def exists(self) -> bool:
        row = self._scalar("SELECT state <> 'null'::jsonb FROM loro_aais_state WHERE stream = %s")
        return bool(row and row[0])

    def recovery_status(self) -> RecoveryRequired | None:
        row = self._scalar("SELECT recovery FROM loro_aais_state WHERE stream = %s")
        return None if row is None else _condition(self.description, row[0])


def postgres_authority(
    dsn: str,
    *,
    stream: str,
    presenter_stream: str | None = None,
    retention: RetentionPolicy | None = None,
    lock_timeout: float = 30.0,
) -> ApprovalAuthority:
    """An approval authority whose state lives in Postgres, keyed by ``stream``."""

    return ApprovalAuthority(
        PostgresBackend(dsn, key=stream, lock_timeout=lock_timeout),
        stream=stream,
        presenter_stream=presenter_stream,
        retention=retention,
    )
