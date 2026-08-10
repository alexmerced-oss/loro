from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loro.cli import app
from loro.config import LoroConfig, SkillsConfig
from loro.skill_compat import (
    SkillCompatibilityError,
    apply_mcp_import,
    import_compatible_skills,
    inspect_compatibility,
)
from loro.skills import SkillRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "compat"


def _config(tmp_path: Path) -> SkillsConfig:
    return SkillsConfig(
        managed_paths=[],
        user_paths=[],
        project_paths=[str(tmp_path / "installed")],
        state_path=str(tmp_path / "state.json"),
        proposal_path=str(tmp_path / "proposals"),
    )


def test_claude_plugin_report_separates_compatible_and_host_components(tmp_path: Path) -> None:
    source = FIXTURES / "claude-plugin"
    report = inspect_compatibility(source, "claude", _config(tmp_path))

    assert report.importable is True
    assert [skill.name for skill in report.skills] == ["reviewer"]
    assert "Missing name will be normalized to 'reviewer'." in report.skills[0].warnings
    assert {"agents", "bin", "hooks"} <= set(report.unsupported_components)
    assert {server.name for server in report.mcp_servers} == {"review-api", "review-tools"}
    assert all(server.compatible for server in report.mcp_servers)


def test_claude_import_normalizes_metadata_paths_and_mcp(tmp_path: Path) -> None:
    source = FIXTURES / "claude-plugin"
    skills_config = _config(tmp_path)
    report = inspect_compatibility(source, "claude", skills_config)
    installed = import_compatible_skills(
        report,
        SkillRegistry(skills_config),
        expected_digest=report.digest,
        scope="project",
    )

    assert [skill.name for skill in installed] == ["reviewer"]
    registry = SkillRegistry(skills_config)
    loaded = registry.load("reviewer")
    assert str(installed[0].path / "CHECKLIST.md") in loaded.instructions
    assert registry.read_supporting_file("reviewer", "CHECKLIST.md").startswith("Check boundaries")

    config = LoroConfig(skills=skills_config)
    assert apply_mcp_import(config, report) == ["review-api", "review-tools"]
    assert config.mcp.enabled is True
    assert config.mcp.servers["review-api"].credential_profile == "review-api-imported"
    assert config.mcp.credential_profiles["review-api-imported"].token_env == "REVIEW_API_TOKEN"
    assert config.mcp.servers["review-tools"].env_allowlist == ["REVIEW_TOKEN"]
    assert config.mcp.servers["review-tools"].command == "review-mcp-server"


def test_pi_package_imports_bare_skill_but_not_extension(tmp_path: Path) -> None:
    source = FIXTURES / "pi-package"
    skills_config = _config(tmp_path)
    report = inspect_compatibility(source, "pi", skills_config)

    assert report.importable is True
    assert report.skills[0].name == "pi-review"
    assert "extensions" in report.unsupported_components
    installed = import_compatible_skills(
        report,
        SkillRegistry(skills_config),
        expected_digest=report.digest,
        scope="project",
    )
    loaded = SkillRegistry(skills_config).load("pi-review")
    assert str(installed[0].path / "guide.md") in loaded.instructions


def test_compatibility_import_requires_reviewed_source_digest(tmp_path: Path) -> None:
    skills_config = _config(tmp_path)
    report = inspect_compatibility(FIXTURES / "pi-package", "pi", skills_config)

    with pytest.raises(SkillCompatibilityError, match="digest"):
        import_compatible_skills(
            report,
            SkillRegistry(skills_config),
            expected_digest="sha256:not-reviewed",
            scope="project",
        )


def test_compatibility_import_rechecks_source_after_preview(tmp_path: Path) -> None:
    source = tmp_path / "pi-package"
    shutil.copytree(FIXTURES / "pi-package", source)
    skills_config = _config(tmp_path)
    report = inspect_compatibility(source, "pi", skills_config)
    (source / "skills" / "pi-review" / "guide.md").write_text(
        "Changed after review.\n", encoding="utf-8"
    )

    with pytest.raises(SkillCompatibilityError, match="changed after review"):
        import_compatible_skills(
            report,
            SkillRegistry(skills_config),
            expected_digest=report.digest,
            scope="project",
        )


def test_claude_mcp_literal_environment_value_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "plugin"
    skill = source / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code.\n---\nReview.\n",
        encoding="utf-8",
    )
    (source / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "unsafe": {
                        "command": "server",
                        "env": {"TOKEN": "literal-secret"},  # pragma: allowlist secret
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    report = inspect_compatibility(source, "claude", _config(tmp_path))

    assert report.mcp_servers[0].compatible is False
    with pytest.raises(SkillCompatibilityError, match="incompatible servers"):
        apply_mcp_import(LoroConfig(), report)


def test_claude_plugin_local_mcp_executable_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "plugin"
    skill = source / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code.\n---\nReview.\n",
        encoding="utf-8",
    )
    (source / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {"command": "${CLAUDE_PLUGIN_ROOT}/bin/server"}
                }
            }
        ),
        encoding="utf-8",
    )

    report = inspect_compatibility(source, "claude", _config(tmp_path))

    assert report.mcp_servers[0].compatible is False
    assert "Plugin-local MCP executables" in report.mcp_servers[0].errors[0]


def test_claude_mcp_names_cannot_collide_after_normalization(tmp_path: Path) -> None:
    source = tmp_path / "plugin"
    skill = source / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code.\n---\nReview.\n",
        encoding="utf-8",
    )
    (source / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "Review API": {"url": "https://one.example.test/mcp", "type": "http"},
                    "review-api": {"url": "https://two.example.test/mcp", "type": "http"},
                }
            }
        ),
        encoding="utf-8",
    )

    report = inspect_compatibility(source, "claude", _config(tmp_path))

    assert "collide after normalization" in report.errors[0]
    with pytest.raises(SkillCompatibilityError, match="report is blocked"):
        apply_mcp_import(LoroConfig(), report)


def test_cli_compatibility_preview_and_digest_pinned_import(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "installed"
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[audit]\nenabled = false\n"
        "[skills]\nmanaged_paths = []\nuser_paths = []\n"
        f'project_paths = ["{destination}"]\n'
        f'state_path = "{tmp_path / "state.json"}"\n'
        f'proposal_path = "{tmp_path / "proposals"}"\n',
    )
    runner = CliRunner()
    preview = runner.invoke(app, ["skills", "import-pi", str(FIXTURES / "pi-package")])
    assert preview.exit_code == 0, preview.stdout
    digest = json.loads(preview.stdout)["digest"]

    imported = runner.invoke(
        app,
        [
            "skills",
            "import-pi",
            str(FIXTURES / "pi-package"),
            "--expected-digest",
            digest,
            "--execute",
        ],
    )

    assert imported.exit_code == 0, imported.stdout
    assert (destination / "pi-review" / "SKILL.md").is_file()


def test_cli_claude_import_requires_explicit_mcp_gate(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "installed"
    output = tmp_path / "config.local.toml"
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        "[audit]\nenabled = false\n"
        "[skills]\nmanaged_paths = []\nuser_paths = []\n"
        f'project_paths = ["{destination}"]\n'
        f'state_path = "{tmp_path / "state.json"}"\n'
        f'proposal_path = "{tmp_path / "proposals"}"\n',
    )
    runner = CliRunner()
    source = FIXTURES / "claude-plugin"
    preview = runner.invoke(app, ["skills", "import-claude", str(source)])
    assert preview.exit_code == 0, preview.stdout
    digest = json.loads(preview.stdout)["digest"]

    imported = runner.invoke(
        app,
        [
            "skills",
            "import-claude",
            str(source),
            "--expected-digest",
            digest,
            "--execute",
            "--include-mcp",
            "--output",
            str(output),
        ],
    )

    assert imported.exit_code == 0, imported.stdout
    payload = json.loads(imported.stdout)
    assert payload["imported_mcp_servers"] == ["review-api", "review-tools"]
    written = output.read_text(encoding="utf-8")
    assert "REVIEW_API_TOKEN" in written
    assert "Bearer ${REVIEW_API_TOKEN}" not in written
