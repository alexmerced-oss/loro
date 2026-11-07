"""Loro's license metadata agrees everywhere it is stated (Apache-2.0, like AGS)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_license_file_pyproject_and_classifier_agree_on_apache() -> None:
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").lstrip().startswith("Apache License")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == {"text": "Apache-2.0"}
    licenses = [item for item in project["classifiers"] if item.startswith("License ::")]
    assert licenses == ["License :: OSI Approved :: Apache Software License"]


def test_upstream_notes_name_the_ags_license_correctly() -> None:
    upstream = (ROOT / "src" / "loro" / "agraph" / "UPSTREAM.md").read_text(encoding="utf-8")
    assert "Apache License 2.0" in upstream
    assert not re.search(r"\bMIT\b", upstream)


def test_bundled_skills_carry_the_project_license() -> None:
    for name in ("agentic-graph", "oap-profile-authoring"):
        text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        front_matter = text.split("---", 2)[1]
        assert re.search(r"^license: Apache-2.0$", front_matter, re.MULTILINE), name
