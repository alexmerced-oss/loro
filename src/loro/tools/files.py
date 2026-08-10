from dataclasses import dataclass
from pathlib import Path

MAX_SEARCH_FILE_BYTES = 2_000_000
BINARY_SNIFF_BYTES = 8192


@dataclass(frozen=True)
class FileSearchMatch:
    path: Path
    line_number: int
    line: str


class FileTools:
    def read_text(self, path: Path, limit: int = 20000) -> str:
        text = path.expanduser().read_text(encoding="utf-8")
        return text[:limit]

    def write_text(self, path: Path, content: str, *, append: bool = False) -> Path:
        path = path.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as file:
            file.write(content)
        return path

    def replace_text(self, path: Path, old: str, new: str, *, count: int = -1) -> int:
        path = path.expanduser()
        text = path.read_text(encoding="utf-8")
        occurrences = text.count(old)
        if occurrences == 0:
            return 0
        updated = text.replace(old, new, count)
        path.write_text(updated, encoding="utf-8")
        return occurrences if count < 0 else min(occurrences, count)

    def search(
        self,
        root: Path,
        query: str,
        limit: int = 50,
        *,
        max_file_bytes: int = MAX_SEARCH_FILE_BYTES,
    ) -> list[FileSearchMatch]:
        root = root.expanduser()
        resolved_root = root.resolve(strict=False)
        matches: list[FileSearchMatch] = []
        needle = query.casefold()
        for path in root.rglob("*"):
            if len(matches) >= limit:
                break
            if self._should_skip(path) or not self._is_searchable(
                path, resolved_root, max_file_bytes
            ):
                continue
            try:
                with path.open("r", encoding="utf-8") as file:
                    for index, line in enumerate(file, start=1):
                        if needle in line.casefold():
                            matches.append(
                                FileSearchMatch(
                                    path=path,
                                    line_number=index,
                                    line=line.strip(),
                                )
                            )
                            if len(matches) >= limit:
                                break
            except (UnicodeDecodeError, OSError):
                continue
        return matches

    def _is_searchable(self, path: Path, resolved_root: Path, max_file_bytes: int) -> bool:
        try:
            # Follows-symlink checks first: a symlink inside the workspace pointing out of
            # it used to have its contents returned under an in-workspace path.
            if path.is_symlink() or not path.is_file():
                return False
            resolved = path.resolve(strict=True)
            if resolved != resolved_root and resolved_root not in resolved.parents:
                return False
            if path.stat().st_size > max_file_bytes:
                return False
            # Binary files are not searchable text and reading them wastes the budget.
            with path.open("rb") as file:
                if b"\x00" in file.read(BINARY_SNIFF_BYTES):
                    return False
        except OSError:
            return False
        return True

    def _should_skip(self, path: Path) -> bool:
        skipped_parts = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
        return any(part in skipped_parts for part in path.parts)
