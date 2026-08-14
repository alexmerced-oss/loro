"""Shared primitives for durable, concurrency-safe state files.

Loro persists governance-relevant state (audit buffers, MCP task handles, memory
proposals and drafts, gateway replay ledgers) as plain files that more than one process
may touch. Every such write goes through the same two primitives so a crash or a
concurrent writer cannot leave a partial file or silently drop another writer's update:

* :func:`file_lock` — an advisory exclusive lock on a sidecar ``.lock`` file, held across
  a whole read-modify-write.
* :func:`atomic_write_text` / :func:`atomic_write_bytes` — write to a unique temporary
  file in the destination directory, fsync it, then ``replace()`` it into place.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["atomic_write_bytes", "atomic_write_text", "file_lock"]


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock for the duration of the block."""

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


def atomic_write_bytes(path: Path, content: bytes, *, mode: int | None = None) -> None:
    """Replace ``path`` with ``content`` atomically.

    The temporary file name is unique per call: a deterministic, shared temporary path
    lets two concurrent writers of the same target clobber each other's staging file.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        temporary.replace(path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    atomic_write_bytes(path, content.encode(encoding), mode=mode)
