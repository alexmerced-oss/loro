from pathlib import Path

import pytest

from loro.config import SkillsConfig
from loro.skills import SkillError, SkillRegistry


def write_skill(root: Path, name: str, *, description: str = "Review Python code safely.") -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        "allowed-tools: skill.read\n---\n\nFollow the review checklist.\n",
        encoding="utf-8",
    )
    references = package / "references"
    references.mkdir()
    (references / "CHECKLIST.md").write_text("Check boundaries.\n", encoding="utf-8")
    return package


def skill_config(tmp_path: Path, root: Path) -> SkillsConfig:
    return SkillsConfig(
        managed_paths=[],
        user_paths=[],
        project_paths=[str(root)],
        state_path=str(tmp_path / "state.json"),
        proposal_path=str(tmp_path / "proposals"),
    )


def test_skill_discovery_progressive_loading_and_supporting_files(tmp_path) -> None:
    root = tmp_path / "skills"
    write_skill(root, "python-review")
    registry = SkillRegistry(skill_config(tmp_path, root))

    metadata = registry.discover()[0]
    assert metadata.name == "python-review"
    assert metadata.digest.startswith("sha256:")
    assert registry.select("Please review this Python module")[0].instructions.startswith(
        "Follow the review checklist"
    )
    assert registry.read_supporting_file("python-review", "references/CHECKLIST.md") == (
        "Check boundaries.\n"
    )


def test_skill_state_is_bound_to_digest(tmp_path) -> None:
    root = tmp_path / "skills"
    package = write_skill(root, "python-review")
    config = skill_config(tmp_path, root)
    registry = SkillRegistry(config)
    assert registry.set_state("python-review", "quarantined").state == "quarantined"
    with pytest.raises(SkillError, match="quarantined"):
        registry.load("python-review")

    with (package / "SKILL.md").open("a", encoding="utf-8") as file:
        file.write("Updated content.\n")
    changed = SkillRegistry(config)
    assert changed.get("python-review").state == "quarantined"
    assert changed.set_state("python-review", "enabled").state == "enabled"


def test_skill_install_requires_reviewed_digest(tmp_path) -> None:
    source_root = tmp_path / "source"
    source = write_skill(source_root, "python-review")
    destination = tmp_path / "installed"
    config = skill_config(tmp_path, destination)
    source_config = skill_config(tmp_path, source_root)
    digest = SkillRegistry(source_config).get("python-review").digest

    with pytest.raises(SkillError, match="digest"):
        SkillRegistry(config).install(source, expected_digest="sha256:wrong")
    installed = SkillRegistry(config).install(source, expected_digest=digest)
    assert installed.path == destination / "python-review"


def test_skill_proposal_requires_explicit_single_review(tmp_path) -> None:
    source_root = tmp_path / "source"
    source = write_skill(source_root, "python-review")
    destination = tmp_path / "installed"
    registry = SkillRegistry(skill_config(tmp_path, destination))

    proposal = registry.propose(source)
    result = registry.review(proposal.name, accept=True)
    assert result["status"] == "accepted"
    assert (destination / "python-review" / "SKILL.md").exists()
    with pytest.raises(SkillError, match="already been reviewed"):
        registry.review(proposal.name, accept=True)


def test_skill_packages_fail_closed_on_collisions_symlinks_and_name_mismatch(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_skill(first, "same-name")
    write_skill(second, "same-name")
    config = skill_config(tmp_path, first).model_copy(
        update={"project_paths": [str(first), str(second)]}
    )
    with pytest.raises(SkillError, match="collision"):
        SkillRegistry(config).discover()

    bad = write_skill(tmp_path / "bad-root", "actual-name")
    (bad / "SKILL.md").write_text(
        "---\nname: different-name\ndescription: mismatch\n---\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(SkillError, match="parent directory"):
        SkillRegistry(skill_config(tmp_path, bad.parent)).discover()

    linked = write_skill(tmp_path / "link-root", "linked-skill")
    (linked / "references" / "escape").symlink_to(tmp_path)
    with pytest.raises(SkillError, match="symlink"):
        SkillRegistry(skill_config(tmp_path, linked.parent)).discover()

    real_root = tmp_path / "real-root"
    write_skill(real_root, "root-link")
    linked_root = tmp_path / "linked-root"
    linked_root.mkdir()
    (linked_root / "root-link").symlink_to(real_root / "root-link", target_is_directory=True)
    with pytest.raises(SkillError, match="roots cannot be symlinks"):
        SkillRegistry(skill_config(tmp_path, linked_root)).discover()


def test_skill_install_removes_copy_if_digest_changes_during_copy(tmp_path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source = write_skill(source_root, "python-review")
    destination = tmp_path / "installed"
    expected = SkillRegistry(skill_config(tmp_path, source_root)).get("python-review").digest
    from loro.skills import _package_digest

    def changed_digest(package: Path) -> str:
        digest = _package_digest(package)
        return "sha256:changed" if package.parent == destination else digest

    monkeypatch.setattr("loro.skills._package_digest", changed_digest)
    with pytest.raises(SkillError, match="changed during package copy"):
        SkillRegistry(skill_config(tmp_path, destination)).install(source, expected_digest=expected)
    assert not (destination / "python-review").exists()


@pytest.mark.parametrize(
    ("frontmatter", "message"),
    [
        (
            "name: invalid-tools\ndescription: invalid tools\nallowed-tools: []\n",
            "space-separated string",
        ),
        (
            "name: aliased\ndescription: &description repeated\ncompatibility: *description\n",
            "YAML aliases",
        ),
    ],
)
def test_skill_frontmatter_rejects_nonstandard_or_amplified_values(
    tmp_path, frontmatter: str, message: str
) -> None:
    root = tmp_path / "skills"
    name = "invalid-tools" if "invalid-tools" in frontmatter else "aliased"
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        f"---\n{frontmatter}---\n\nInstructions.\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillError, match=message):
        SkillRegistry(skill_config(tmp_path, root)).discover()


def test_skill_fixture_passes_reference_validator() -> None:
    skills_ref = pytest.importorskip("skills_ref")
    package = Path(__file__).parent / "fixtures" / "skills" / "python-review"

    assert skills_ref.validate(package) == []
