from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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


@dataclass(frozen=True)
class AuditVerificationResult:
    ok: bool
    events: int
    final_hash: str | None
    legacy_events: int = 0
    issue: str | None = None
    line: int | None = None


class JsonlAuditSink:
    name = "jsonl"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def deliver(self, payload: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _file_lock(self.path):
                previous_hash, legacy_prefix_hash = _chain_state(self.path)
                chained = _chain_payload(payload, previous_hash, legacy_prefix_hash)
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(chained, default=str, sort_keys=True) + "\n")
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
        self._post(payload)

    def deliver_batch(self, payloads: list[dict[str, Any]]) -> None:
        """Deliver several buffered events in one request.

        Flushing a large buffer one HTTP round-trip per event is the dominant cost of
        recovering from a collector outage.
        """

        if not payloads:
            return
        if len(payloads) == 1:
            self.deliver(payloads[0])
            return
        self._post({"events": payloads})

    def _post(self, payload: dict[str, Any]) -> None:
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


@dataclass(frozen=True)
class AuditDrainResult:
    attempted: int
    delivered: int
    remaining: int


class AuditBuffer:
    def __init__(self, path: str | Path, max_events: int) -> None:
        self.path = Path(path).expanduser()
        self.max_events = max_events
        self.stats_path = self.path.with_suffix(self.path.suffix + ".stats.json")

    def append(self, payload: dict[str, Any]) -> int:
        """Append one event, evicting the oldest events when the buffer is full.

        Returns the number of evicted events. Refusing the *newest* event while retaining
        stale ones loses the most relevant evidence, so the buffer rotates instead.
        """

        if self.max_events <= 0:
            raise AuditBufferFullError(f"Audit buffer capacity is zero: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(self.path):
            line = _canonical_json(payload) + "\n"
            count = self._count_unlocked()
            if count < self.max_events:
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(line)
                self._write_stats_unlocked(events=count + 1)
                return 0
            # Rotate: keep the newest max_events entries including the incoming one.
            retained = self._raw_lines_unlocked()
            evicted = len(retained) + 1 - self.max_events
            retained = retained[evicted:]
            retained.append(line.encode("utf-8"))
            self._write_raw_unlocked(retained)
            self._write_stats_unlocked(events=len(retained), evicted_delta=evicted)
            return evicted

    def drain(
        self,
        deliver: Callable[[list[dict[str, Any]]], None],
        *,
        batch_size: int = 1,
    ) -> AuditDrainResult:
        """Deliver buffered events and rewrite the remainder without releasing the lock.

        Loading and truncating under separate locks let a concurrent append land between
        the two windows and be deleted by the truncation.
        """

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(self.path):
            events = self._load_unlocked()
            delivered = 0
            for index in range(0, len(events), max(1, batch_size)):
                batch = events[index : index + max(1, batch_size)]
                try:
                    deliver(batch)
                except AuditSinkError:
                    remaining = events[index:]
                    self._replace_unlocked(remaining)
                    return AuditDrainResult(
                        attempted=index + len(batch),
                        delivered=delivered,
                        remaining=len(remaining),
                    )
                delivered += len(batch)
            self._replace_unlocked([])
            return AuditDrainResult(attempted=len(events), delivered=delivered, remaining=0)

    def evicted_events(self) -> int:
        return int(self._read_stats().get("evicted_events", 0))

    def load(self) -> list[dict[str, Any]]:
        with _file_lock(self.path):
            return self._load_unlocked()

    def _load_unlocked(self) -> list[dict[str, Any]]:
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
        with _file_lock(self.path):
            self._replace_unlocked(events)

    def count(self) -> int:
        with _file_lock(self.path):
            return self._count_unlocked()

    def _replace_unlocked(self, events: list[dict[str, Any]]) -> None:
        self._write_raw_unlocked(
            [(_canonical_json(event) + "\n").encode("utf-8") for event in events]
        )
        self._write_stats_unlocked(events=len(events))

    def _write_raw_unlocked(self, lines: list[bytes]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        with temporary.open("wb") as file:
            file.write(b"".join(lines))
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self.path)

    def _raw_lines_unlocked(self) -> list[bytes]:
        if not self.path.exists():
            return []
        return [line for line in self.path.read_bytes().splitlines(keepends=True) if line.strip()]

    def _count_unlocked(self) -> int:
        """Event count without re-parsing every buffered event.

        Appending used to load and JSON-decode the whole buffer on every write, making a
        full buffer quadratic. The sidecar caches the count and is only rebuilt when the
        buffer file size no longer matches what produced it.
        """

        try:
            size = self.path.stat().st_size
        except OSError:
            return 0
        stats = self._read_stats()
        if stats.get("size") == size and isinstance(stats.get("events"), int):
            return int(stats["events"])
        return len(self._raw_lines_unlocked())

    def _read_stats(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.stats_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_stats_unlocked(self, *, events: int, evicted_delta: int = 0) -> None:
        stats = self._read_stats()
        stats["events"] = events
        stats["evicted_events"] = int(stats.get("evicted_events", 0)) + evicted_delta
        try:
            stats["size"] = self.path.stat().st_size
        except OSError:
            stats["size"] = None
        try:
            self.stats_path.write_text(_canonical_json(stats) + "\n", encoding="utf-8")
        except OSError:
            # The sidecar is an optimization and a diagnostic; losing it must never lose
            # an audit event.
            pass


def verify_jsonl_audit(
    path: str | Path, *, expected_final_hash: str | None = None
) -> AuditVerificationResult:
    audit_path = Path(path).expanduser()
    with _file_lock(audit_path):
        if not audit_path.exists():
            return AuditVerificationResult(
                ok=expected_final_hash is None,
                events=0,
                final_hash=None,
                issue="Audit file does not exist." if expected_final_hash else None,
            )
        previous_hash: str | None = None
        events = 0
        legacy_events = 0
        legacy_prefix = b""
        chain_started = False
        raw_lines = audit_path.read_bytes().splitlines(keepends=True)
        for line_number, raw_line in enumerate(raw_lines, 1):
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return AuditVerificationResult(
                    ok=False,
                    events=events,
                    final_hash=previous_hash,
                    legacy_events=legacy_events,
                    issue="Invalid JSON.",
                    line=line_number,
                )
            if not isinstance(payload, dict):
                return AuditVerificationResult(
                    ok=False,
                    events=events,
                    final_hash=previous_hash,
                    legacy_events=legacy_events,
                    issue="Audit event is not an object.",
                    line=line_number,
                )
            integrity = payload.get("integrity")
            if not isinstance(integrity, dict):
                if chain_started:
                    return AuditVerificationResult(
                        ok=False,
                        events=events,
                        final_hash=previous_hash,
                        legacy_events=legacy_events,
                        issue="Unchained event appears after the integrity chain started.",
                        line=line_number,
                    )
                legacy_prefix += raw_line
                legacy_events += 1
                continue
            if not chain_started:
                expected_legacy_hash = _bytes_hash(legacy_prefix) if legacy_prefix else None
                if integrity.get("legacy_prefix_hash") != expected_legacy_hash:
                    return AuditVerificationResult(
                        ok=False,
                        events=events,
                        final_hash=previous_hash,
                        legacy_events=legacy_events,
                        issue="Legacy audit prefix hash does not match.",
                        line=line_number,
                    )
                chain_started = True
            if integrity.get("previous_hash") != previous_hash:
                return AuditVerificationResult(
                    ok=False,
                    events=events,
                    final_hash=previous_hash,
                    legacy_events=legacy_events,
                    issue="Previous hash does not match.",
                    line=line_number,
                )
            expected = _event_hash(
                payload,
                previous_hash,
                integrity.get("legacy_prefix_hash"),
            )
            if integrity.get("event_hash") != expected:
                return AuditVerificationResult(
                    ok=False,
                    events=events,
                    final_hash=previous_hash,
                    legacy_events=legacy_events,
                    issue="Event hash does not match content.",
                    line=line_number,
                )
            previous_hash = expected
            events += 1
        if expected_final_hash is not None and previous_hash != expected_final_hash:
            return AuditVerificationResult(
                ok=False,
                events=events,
                final_hash=previous_hash,
                legacy_events=legacy_events,
                issue="Final hash does not match the external anchor.",
            )
        return AuditVerificationResult(
            ok=True,
            events=events,
            final_hash=previous_hash,
            legacy_events=legacy_events,
        )


def _chain_payload(
    payload: dict[str, Any],
    previous_hash: str | None,
    legacy_prefix_hash: str | None = None,
) -> dict[str, Any]:
    chained = dict(payload)
    chained["integrity"] = {
        "algorithm": "sha256",
        "previous_hash": previous_hash,
        "legacy_prefix_hash": legacy_prefix_hash,
        "event_hash": _event_hash(chained, previous_hash, legacy_prefix_hash),
    }
    return chained


def _event_hash(
    payload: dict[str, Any],
    previous_hash: str | None,
    legacy_prefix_hash: Any = None,
) -> str:
    content = dict(payload)
    content.pop("integrity", None)
    bound = {
        "previous_hash": previous_hash,
        "legacy_prefix_hash": legacy_prefix_hash,
        "event": content,
    }
    return "sha256:" + hashlib.sha256(_canonical_json(bound).encode("utf-8")).hexdigest()


def _chain_state(path: Path) -> tuple[str | None, str | None]:
    if not path.exists():
        return None, None
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None, None
    try:
        payload = json.loads(lines[-1])
        integrity = payload.get("integrity") if isinstance(payload, dict) else None
        event_hash = integrity.get("event_hash") if isinstance(integrity, dict) else None
    except json.JSONDecodeError as error:
        raise AuditSinkError(f"Invalid existing audit JSON: {path}") from error
    if not isinstance(event_hash, str):
        # Must hash exactly the byte stream verify_jsonl_audit rebuilds — non-blank raw
        # lines only. Hashing whole-file bytes made any blank line (including a trailing
        # "\n\n") report tampering on an untouched file.
        return None, _bytes_hash(_legacy_prefix_bytes(path.read_bytes().splitlines(keepends=True)))
    return event_hash, None


def _legacy_prefix_bytes(raw_lines: list[bytes]) -> bytes:
    return b"".join(line for line in raw_lines if line.strip())


def _bytes_hash(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI when available.
            import msvcrt

            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
