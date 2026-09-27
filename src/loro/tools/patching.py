"""Apply unified diffs safely: parse, locate hunks, report conflicts, write atomically.

A patch is all-or-nothing. Every hunk of every file is located first; if any hunk does not
match, nothing is written and the result lists each conflict with the line it expected and
what the file actually contains. Hunks may drift a little from their stated line numbers
(offset search within ``MAX_OFFSET`` lines), which is how ``patch`` and ``git apply`` treat
edits made to a file after the diff was produced; context lines are otherwise matched exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from loro.fileio import atomic_write_text

MAX_OFFSET = 200
MAX_PATCH_BYTES = 2_000_000
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(ValueError):
    """The patch text itself is malformed or unsafe."""


@dataclass
class Hunk:
    old_start: int
    lines: list[str] = field(default_factory=list)  # prefixed with " ", "-", "+"

    @property
    def before(self) -> list[str]:
        return [line[1:] for line in self.lines if line[:1] in {" ", "-"}]

    @property
    def after(self) -> list[str]:
        return [line[1:] for line in self.lines if line[:1] in {" ", "+"}]


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def action(self) -> str:
        if self.old_path is None:
            return "create"
        if self.new_path is None:
            return "delete"
        return "modify" if self.old_path == self.new_path else "rename"

    @property
    def target(self) -> str:
        return str(self.new_path or self.old_path)


@dataclass
class Conflict:
    path: str
    hunk: int
    line: int
    reason: str
    expected: str = ""
    actual: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "path": self.path,
            "hunk": self.hunk,
            "line": self.line,
            "reason": self.reason,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass
class PatchResult:
    applied: bool
    dry_run: bool
    files: list[dict[str, object]]
    conflicts: list[Conflict]

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def summary(self) -> str:
        verb = "Would apply" if self.dry_run else "Applied"
        lines = []
        if self.ok:
            lines.append(f"{verb} {len(self.files)} file change(s):")
            for item in self.files:
                lines.append(
                    f"  {item['action']} {item['path']} (+{item['added']} -{item['removed']})"
                )
        else:
            lines.append(f"Patch not applied: {len(self.conflicts)} conflict(s).")
            for conflict in self.conflicts:
                detail = f"  {conflict.path} hunk {conflict.hunk} near line {conflict.line}: "
                detail += conflict.reason
                if conflict.expected or conflict.actual:
                    detail += (
                        f"\n    expected: {conflict.expected!r}\n    found:    {conflict.actual!r}"
                    )
                lines.append(detail)
        return "\n".join(lines)


def _strip_prefix(raw: str) -> str | None:
    path = raw.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def safe_relative(path: str) -> str:
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts or "\\" in path or "\x00" in path:
        raise PatchError(f"Patch paths must be relative and stay inside the workspace: {path!r}")
    return str(pure)


def parse_unified_diff(text: str) -> list[FilePatch]:
    if len(text.encode("utf-8")) > MAX_PATCH_BYTES:
        raise PatchError(f"Patch exceeds {MAX_PATCH_BYTES} bytes.")
    lines = text.splitlines()
    patches: list[FilePatch] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            line.startswith("--- ")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("+++ ")
        ):
            old = _strip_prefix(line[4:])
            new = _strip_prefix(lines[index + 1][4:])
            if old is None and new is None:
                raise PatchError("A file header names /dev/null on both sides.")
            current = FilePatch(
                old_path=safe_relative(old) if old else None,
                new_path=safe_relative(new) if new else None,
            )
            patches.append(current)
            index += 2
            while index < len(lines) and lines[index].startswith("@@"):
                match = _HUNK.match(lines[index])
                if match is None:
                    raise PatchError(f"Malformed hunk header: {lines[index]!r}")
                old_count = int(match.group(2) or "1")
                new_count = int(match.group(4) or "1")
                hunk = Hunk(old_start=int(match.group(1)))
                index += 1
                seen_old = seen_new = 0
                while index < len(lines) and (seen_old < old_count or seen_new < new_count):
                    body = lines[index]
                    if body.startswith("\\"):  # "\ No newline at end of file"
                        index += 1
                        continue
                    marker = body[:1] if body else " "
                    if marker not in {" ", "-", "+"}:
                        raise PatchError(f"Unexpected line in hunk: {body!r}")
                    hunk.lines.append(body if body else " ")
                    seen_old += marker in {" ", "-"}
                    seen_new += marker in {" ", "+"}
                    index += 1
                while index < len(lines) and lines[index].startswith("\\"):
                    index += 1
                if seen_old != old_count or seen_new != new_count:
                    raise PatchError(f"Hunk in {current.target} is shorter than its header says.")
                current.hunks.append(hunk)
            continue
        index += 1
    if not patches:
        raise PatchError("No unified diff file headers (--- / +++) were found.")
    return patches


def _locate(content: list[str], hunk: Hunk, hint: int) -> int | None:
    before = hunk.before
    if not before:
        return min(max(hint, 0), len(content))
    for offset in range(0, MAX_OFFSET + 1):
        for start in {hint + offset, hint - offset}:
            if (
                0 <= start <= len(content) - len(before)
                and content[start : start + len(before)] == before
            ):
                return start
    return None


def _first_mismatch(content: list[str], hunk: Hunk, start: int) -> tuple[int, str, str]:
    for position, expected in enumerate(hunk.before):
        index = start + position
        actual = content[index] if 0 <= index < len(content) else "<end of file>"
        if actual != expected:
            return index + 1, expected, actual
    return start + 1, "", ""


def apply_patch(text: str, root: Path, *, dry_run: bool = False) -> PatchResult:
    """Apply ``text`` under ``root``; with ``dry_run`` only report what would change."""

    root = root.resolve()
    patches = parse_unified_diff(text)
    conflicts: list[Conflict] = []
    planned: list[tuple[FilePatch, Path, str | None, int, int]] = []
    for patch in patches:
        path = (root / patch.target).resolve()
        if root not in path.parents and path != root:
            raise PatchError(f"Patch path leaves the workspace: {patch.target}")
        source = (root / patch.old_path).resolve() if patch.old_path else None
        # The source of a rename is read too; a symlink must not pull an outside file in.
        if source is not None and root not in source.parents:
            raise PatchError(f"Patch path leaves the workspace: {patch.old_path}")
        if patch.action == "create":
            if path.exists():
                conflicts.append(Conflict(patch.target, 1, 1, "file already exists"))
                continue
            content: list[str] = []
            trailing_newline = True
        else:
            assert source is not None
            if not source.is_file():
                conflicts.append(Conflict(str(patch.old_path), 1, 1, "file does not exist"))
                continue
            raw = source.read_text(encoding="utf-8")
            trailing_newline = raw.endswith("\n")
            content = raw.splitlines()
        added = removed = 0
        drift = 0
        failed = False
        for number, hunk in enumerate(patch.hunks, 1):
            hint = max(hunk.old_start - 1, 0) + drift
            start = _locate(content, hunk, hint)
            if start is None:
                line, expected, actual = _first_mismatch(content, hunk, min(hint, len(content)))
                conflicts.append(
                    Conflict(
                        patch.target,
                        number,
                        line,
                        "context does not match the file",
                        expected,
                        actual,
                    )
                )
                failed = True
                break
            content[start : start + len(hunk.before)] = hunk.after
            drift += len(hunk.after) - len(hunk.before)
            added += sum(1 for line in hunk.lines if line.startswith("+"))
            removed += sum(1 for line in hunk.lines if line.startswith("-"))
        if failed:
            continue
        if patch.action == "delete":
            if content:
                conflicts.append(
                    Conflict(patch.target, len(patch.hunks), 1, "deleted file still has content")
                )
                continue
            new_text: str | None = None
        else:
            new_text = "\n".join(content) + ("\n" if content and trailing_newline else "")
        planned.append((patch, path, new_text, added, removed))

    files: list[dict[str, object]] = [
        {
            "path": patch.target,
            "action": patch.action,
            "added": added,
            "removed": removed,
            **({"from": patch.old_path} if patch.action == "rename" else {}),
        }
        for patch, _path, _text, added, removed in planned
    ]
    if conflicts or dry_run:
        return PatchResult(applied=False, dry_run=dry_run, files=files, conflicts=conflicts)
    for patch, path, new_text, _added, _removed in planned:
        if new_text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, new_text)
        if patch.action == "rename" and patch.old_path:
            (root / patch.old_path).unlink(missing_ok=True)
    return PatchResult(applied=True, dry_run=False, files=files, conflicts=[])


def touched_paths(text: str) -> list[str]:
    return sorted(
        {
            path
            for patch in parse_unified_diff(text)
            for path in (patch.old_path, patch.new_path)
            if path
        }
    )
