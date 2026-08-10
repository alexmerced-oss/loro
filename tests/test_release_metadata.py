from __future__ import annotations

import tomllib
from pathlib import Path

from loro import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_package_versions_are_synchronized() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == __version__


def test_current_release_notes_exist() -> None:
    assert (ROOT / "docs" / "releases" / f"{__version__}.md").is_file()
