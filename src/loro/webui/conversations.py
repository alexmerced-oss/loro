from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 2
_STATUSES = {"active", "archived"}
_ROLES = {"user", "assistant", "tool", "system-event"}


class ConversationStore:
    """Versioned SQLite storage for Web UI conversations and append-only messages."""

    def __init__(self, path: Path, *, synchronous: str = "FULL") -> None:
        self.path = path.expanduser().resolve()
        normalized = synchronous.upper()
        if normalized not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
            raise ValueError(f"Unsupported SQLite synchronous mode: {synchronous}")
        self.synchronous = normalized
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute(f"PRAGMA synchronous = {self.synchronous}")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS webui_schema (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'archived')),
                    workspace TEXT NOT NULL,
                    profile_name TEXT,
                    profile_revision INTEGER,
                    profile_spec_digest TEXT,
                    participants TEXT,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant','tool','system-event')),
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_conversation_created
                    ON messages(conversation_id, created_at, id);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    stop_reason TEXT,
                    provider TEXT,
                    model TEXT,
                    usage_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )
            row = connection.execute("SELECT version FROM webui_schema LIMIT 1").fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO webui_schema(version) VALUES (?)", (SCHEMA_VERSION,)
                )
                return

            version = int(row["version"])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    "Unsupported Web UI database schema "
                    f"{version}; expected {SCHEMA_VERSION}. This database was written by a newer "
                    "Loro. Upgrade, or point --database at a different file."
                )

            # v1 -> v2: group conversations. `participants` holds a JSON list of
            # profile names; a single-profile conversation leaves it NULL and
            # keeps using profile_name, so existing rows stay valid untouched.
            if version < 2:
                columns = {
                    str(item["name"])
                    for item in connection.execute("PRAGMA table_info(conversations)")
                }
                if "participants" not in columns:
                    connection.execute("ALTER TABLE conversations ADD COLUMN participants TEXT")
                connection.execute("UPDATE webui_schema SET version = ?", (SCHEMA_VERSION,))

    def create_conversation(
        self,
        *,
        title: str = "New conversation",
        workspace: str,
        profile_name: str | None = None,
        profile_revision: int | None = None,
        profile_spec_digest: str | None = None,
        participants: list[str] | None = None,
    ) -> dict[str, Any]:
        conversation_id = str(uuid4())
        now = _now()
        normalized_title = _bounded_text(title.strip() or "New conversation", 200)
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO conversations(
                    id,title,status,workspace,profile_name,profile_revision,profile_spec_digest,
                    participants,session_id,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    conversation_id,
                    normalized_title,
                    "active",
                    workspace,
                    profile_name,
                    profile_revision,
                    profile_spec_digest,
                    json.dumps(participants) if participants else None,
                    conversation_id,
                    now,
                    now,
                ),
            )
        return self.get_conversation(conversation_id)

    def list_conversations(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        where = "" if include_archived else "WHERE status = 'active'"
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM conversations {where} ORDER BY updated_at DESC"  # nosec B608  # noqa: S608
            ).fetchall()
        return [_row(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Conversation not found: {conversation_id}")
        return _row(row)

    def update_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in _STATUSES:
            raise ValueError(f"Invalid conversation status: {status}")
        current = self.get_conversation(conversation_id)
        next_title = current["title"] if title is None else _bounded_text(title.strip(), 200)
        if not next_title:
            raise ValueError("Conversation title cannot be empty.")
        next_status = status or current["status"]
        with self._connection() as connection:
            connection.execute(
                "UPDATE conversations SET title = ?, status = ?, updated_at = ? WHERE id = ?",
                (next_title, next_status, _now(), conversation_id),
            )
        return self.get_conversation(conversation_id)

    def set_session_id(self, conversation_id: str, session_id: str) -> None:
        self.get_conversation(conversation_id)
        with self._connection() as connection:
            connection.execute(
                "UPDATE conversations SET session_id = ?, updated_at = ? WHERE id = ?",
                (session_id, _now(), conversation_id),
            )

    def delete_conversation(self, conversation_id: str) -> None:
        with self._connection() as connection:
            result = connection.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
        if result.rowcount == 0:
            raise KeyError(f"Conversation not found: {conversation_id}")

    def add_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        status: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if role not in _ROLES:
            raise ValueError(f"Invalid message role: {role}")
        self.get_conversation(conversation_id)
        message_id = str(uuid4())
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO messages(
                    id,conversation_id,role,content,status,metadata_json,created_at
                ) VALUES (?,?,?,?,?,?,?)""",
                (
                    message_id,
                    conversation_id,
                    role,
                    _bounded_text(content, 1_000_000),
                    status,
                    json.dumps(metadata or {}, sort_keys=True),
                    now,
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
            )
        return self.get_message(message_id)

    def get_message(self, message_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Message not found: {message_id}")
        return _message_row(row)

    def list_messages(self, conversation_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id)
        bounded_limit = max(1, min(limit, 1000))
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM (
                    SELECT * FROM messages WHERE conversation_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                ) ORDER BY created_at, id""",
                (conversation_id, bounded_limit),
            ).fetchall()
        return [_message_row(row) for row in rows]

    def create_run(self, conversation_id: str, run_id: str) -> dict[str, Any]:
        self.get_conversation(conversation_id)
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO runs(
                    id,conversation_id,status,usage_json,created_at
                ) VALUES (?,?,?,?,?)""",
                (run_id, conversation_id, "running", "{}", _now()),
            )
        return self.get_run(run_id)

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        stop_reason: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            result = connection.execute(
                """UPDATE runs SET status=?,stop_reason=?,provider=?,model=?,usage_json=?,
                   error=?,completed_at=? WHERE id=?""",
                (
                    status,
                    stop_reason,
                    provider,
                    model,
                    json.dumps(usage or {}, sort_keys=True),
                    error,
                    _now(),
                    run_id,
                ),
            )
        if result.rowcount == 0:
            raise KeyError(f"Run not found: {run_id}")
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Run not found: {run_id}")
        payload = _row(row)
        payload["usage"] = json.loads(payload.pop("usage_json"))
        return payload


def _row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    # `participants` is stored as JSON so a group conversation keeps its roster
    # in one column; readers always see a list.
    raw = payload.get("participants")
    if raw:
        try:
            parsed = json.loads(raw)
            payload["participants"] = (
                [str(item) for item in parsed] if isinstance(parsed, list) else []
            )
        except (TypeError, ValueError):
            payload["participants"] = []
    elif "participants" in payload:
        payload["participants"] = []
    return payload


def _message_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = _row(row)
    payload["metadata"] = json.loads(payload.pop("metadata_json"))
    return payload


def _bounded_text(value: str, maximum: int) -> str:
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"Text exceeds the limit of {maximum} bytes.")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()
