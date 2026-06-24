from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileSearchMatch:
    path: Path
    line_number: int
    line: str


class FileTools:
    def read_text(self, path: Path, limit: int = 20000) -> str:
        text = path.expanduser().read_text(encoding="utf-8")
        return text[:limit]

    def search(self, root: Path, query: str, limit: int = 50) -> list[FileSearchMatch]:
        root = root.expanduser()
        matches: list[FileSearchMatch] = []
        for path in root.rglob("*"):
            if len(matches) >= limit:
                break
            if not path.is_file() or self._should_skip(path):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for index, line in enumerate(lines, start=1):
                if query.casefold() in line.casefold():
                    matches.append(FileSearchMatch(path=path, line_number=index, line=line.strip()))
                    if len(matches) >= limit:
                        break
        return matches

    def _should_skip(self, path: Path) -> bool:
        skipped_parts = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
        return any(part in skipped_parts for part in path.parts)
