from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID

from loro.audit.schema import AUDIT_SCHEMA_VERSION

EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class AuditCollectorError(RuntimeError):
    pass


class AuditCollectorAuthError(AuditCollectorError):
    pass


@dataclass(frozen=True)
class AuditCollectorResult:
    accepted: int
    duplicates: int
    final_hash: str | None


@dataclass(frozen=True)
class AuditCollectorVerification:
    ok: bool
    events: int
    final_hash: str | None
    issue: str | None = None


class AuditCollector:
    """Authenticated, deduplicating, hash-chained SQLite audit collector."""

    def __init__(self, path: str | Path, token: str, *, max_body_bytes: int = 5_000_000) -> None:
        if not token:
            raise ValueError("Audit collector token must be non-empty.")
        self.path = Path(path).expanduser()
        self.token = token
        self.max_body_bytes = max_body_bytes
        self._initialize()

    def accept(self, authorization: str | None, body: bytes) -> AuditCollectorResult:
        expected = f"Bearer {self.token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise AuditCollectorAuthError("Invalid audit collector bearer token.")
        if len(body) > self.max_body_bytes:
            raise AuditCollectorError("Audit collector request exceeds max_body_bytes.")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuditCollectorError("Audit collector request is not valid JSON.") from error
        raw_events = payload.get("events") if isinstance(payload, dict) else None
        events = raw_events if isinstance(raw_events, list) else [payload]
        if not events or any(not isinstance(event, dict) for event in events):
            raise AuditCollectorError("Audit collector request contains invalid events.")
        validated = [_validated_event(event) for event in events]
        accepted = 0
        duplicates = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            final_hash = _metadata(connection, "final_hash")
            for event in validated:
                canonical = _canonical_json(event)
                existing = connection.execute(
                    "SELECT payload_json FROM audit_events WHERE event_id = ?",
                    (event["event_id"],),
                ).fetchone()
                if existing is not None:
                    if not hmac.compare_digest(str(existing[0]), canonical):
                        raise AuditCollectorError(
                            "Audit event ID is already bound to different content."
                        )
                    duplicates += 1
                    continue
                event_hash = _event_hash(canonical, final_hash)
                connection.execute(
                    """
                    INSERT INTO audit_events (
                      event_id, received_at, payload_json, previous_hash, event_hash
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"],
                        datetime.now(UTC).isoformat(),
                        canonical,
                        final_hash,
                        event_hash,
                    ),
                )
                final_hash = event_hash
                accepted += 1
            connection.execute(
                "INSERT OR REPLACE INTO collector_metadata (key, value) VALUES ('final_hash', ?)",
                (final_hash,),
            )
            connection.commit()
        return AuditCollectorResult(
            accepted=accepted,
            duplicates=duplicates,
            final_hash=final_hash,
        )

    def verify(self) -> AuditCollectorVerification:
        previous: str | None = None
        events = 0
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT payload_json, previous_hash, event_hash FROM audit_events ORDER BY sequence"
            )
            for payload, recorded_previous, recorded_hash in rows:
                if recorded_previous != previous:
                    return AuditCollectorVerification(
                        False, events, previous, "Previous hash does not match."
                    )
                expected = _event_hash(str(payload), previous)
                if not hmac.compare_digest(str(recorded_hash), expected):
                    return AuditCollectorVerification(
                        False, events, previous, "Event hash does not match."
                    )
                previous = expected
                events += 1
            anchor = _metadata(connection, "final_hash")
        if anchor != previous:
            return AuditCollectorVerification(False, events, previous, "Anchor does not match.")
        return AuditCollectorVerification(True, events, previous)

    def prometheus(self) -> str:
        verification = self.verify()
        return (
            "# HELP loro_audit_collector_events Persisted deduplicated audit events.\n"
            "# TYPE loro_audit_collector_events gauge\n"
            f"loro_audit_collector_events {verification.events}\n"
            "# HELP loro_audit_collector_chain_ok Audit hash-chain verification status.\n"
            "# TYPE loro_audit_collector_chain_ok gauge\n"
            f"loro_audit_collector_chain_ok {1 if verification.ok else 0}\n"
        )

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_id TEXT NOT NULL UNIQUE,
                  received_at TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  previous_hash TEXT,
                  event_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS collector_metadata (
                  key TEXT PRIMARY KEY,
                  value TEXT
                );
                """
            )
        self.path.chmod(0o600)


def serve_audit_collector(
    collector: AuditCollector,
    *,
    host: str = "127.0.0.1",
    port: int = 8788,
) -> None:
    server = create_audit_collector_server(collector, host=host, port=port)
    server.serve_forever()


def create_audit_collector_server(
    collector: AuditCollector,
    *,
    host: str = "127.0.0.1",
    port: int = 8788,
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/events":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > collector.max_body_bytes:
                    raise AuditCollectorError("Invalid audit collector Content-Length.")
                result = collector.accept(
                    self.headers.get("Authorization"),
                    self.rfile.read(length),
                )
                self._json(HTTPStatus.OK, result.__dict__)
            except AuditCollectorAuthError as error:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
            except AuditCollectorError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                verification = collector.verify()
                self._json(
                    HTTPStatus.OK if verification.ok else HTTPStatus.SERVICE_UNAVAILABLE,
                    verification.__dict__,
                )
                return
            if self.path == "/metrics":
                content = collector.prometheus().encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            content = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return ThreadingHTTPServer((host, port), Handler)


def token_from_environment(name: str) -> str:
    token = os.environ.get(name)
    if not token:
        raise AuditCollectorError(f"Audit collector token environment variable is missing: {name}")
    return token


def _validated_event(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("schema_version") != AUDIT_SCHEMA_VERSION:
        raise AuditCollectorError("Unsupported audit event schema version.")
    event_id = str(event.get("event_id") or "")
    try:
        UUID(event_id)
    except ValueError as error:
        raise AuditCollectorError("Audit event_id must be a UUID.") from error
    event_type = str(event.get("event_type") or "")
    if EVENT_PATTERN.fullmatch(event_type) is None:
        raise AuditCollectorError("Audit event_type is invalid.")
    if not isinstance(event.get("timestamp") or event.get("created_at"), str):
        raise AuditCollectorError("Audit event timestamp is required.")
    return event


def _metadata(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM collector_metadata WHERE key = ?", (key,)
    ).fetchone()
    return str(row[0]) if row is not None and row[0] is not None else None


def _event_hash(payload: str, previous_hash: str | None) -> str:
    bound = _canonical_json({"previous_hash": previous_hash, "event": json.loads(payload)})
    return "sha256:" + hashlib.sha256(bound.encode("utf-8")).hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
