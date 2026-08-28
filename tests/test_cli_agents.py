import tomllib

import yaml
from typer.testing import CliRunner

from loro.agent_profiles import AgentProfileRegistry, build_effective_profile
from loro.cli import _runtime, app
from loro.config import load_config


def _configure_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'[permissions]\nworkspace_roots = ["{tmp_path}"]\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n',
    )


def test_profile_wizard_creates_compliant_default_coding_profile(tmp_path, monkeypatch) -> None:
    _configure_environment(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        app,
        ["setup", "profile", "--name", "project-coder"],
        input="\n" * 8,
    )

    assert result.exit_code == 0, result.stdout
    profile_path = tmp_path / ".loro/agents/project-coder.agent.yaml"
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    local_config = tomllib.loads((tmp_path / ".loro/config.local.toml").read_text())
    assert payload["oap"] == "1.0"
    assert payload["kind"] == "AgentProfile"
    assert payload["metadata"]["name"] == "project-coder"
    assert payload["spec"]["model"] == {"provider": "mock", "id": "mock-agent"}
    assert payload["spec"]["tools"]["policy"] == "allowlist"
    assert "file.write" in payload["spec"]["tools"]["allow"]
    assert payload["spec"]["permissions"]["shell"] == "ask"
    assert payload["spec"]["permissions"]["network"] == "deny"
    assert local_config["agent_profiles"]["default_profile"] == "project-coder"

    config = load_config()
    effective = build_effective_profile(
        AgentProfileRegistry(config.agent_profiles, safety=config.safety).load("project-coder"),
        config,
    )
    assert effective.model.provider == "mock"
    assert effective.model.model == "mock-agent"
    assert "file.write" in effective.tools
    assert _runtime().profile.resolved.document.metadata.name == "project-coder"


def test_profile_wizard_can_prepare_governed_web_research_ceiling(tmp_path, monkeypatch) -> None:
    _configure_environment(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        app,
        ["agents", "configure", "--name", "web-researcher"],
        input="\n\n\n3\n\n\n\ny\nn\n",
    )

    assert result.exit_code == 0, result.stdout
    profile = yaml.safe_load(
        (tmp_path / ".loro/agents/web-researcher.agent.yaml").read_text(encoding="utf-8")
    )
    config = tomllib.loads((tmp_path / ".loro/config.local.toml").read_text())
    assert profile["spec"]["permissions"]["network"] == "ask"
    assert profile["spec"]["permissions"]["shell"] == "deny"
    assert "shell.run" in profile["spec"]["tools"]["allow"]
    assert config["permissions"]["web"] == "ask"
    shell_profile = config["sandbox"]["profiles"]["controlled-shell"]
    assert "curl" in shell_profile["allowed_executables"]
    assert shell_profile["network"] == "inherit"
    assert config["agent_profiles"].get("default_profile") is None

    resolved = load_config()
    effective = build_effective_profile(
        AgentProfileRegistry(resolved.agent_profiles, safety=resolved.safety).load(
            "web-researcher"
        ),
        resolved,
    )
    assert effective.permissions.shell == "deny"
    assert effective.permissions.web == "ask"
    assert "shell.run" in effective.tools


def test_profile_wizard_explains_routes_and_separates_custom_web_from_shell(
    tmp_path, monkeypatch
) -> None:
    _configure_environment(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        app,
        ["setup", "profile", "--name", "web-reader"],
        input="\n\n\n6\n5\nn\ny\n\n\n\ny\nn\n",
    )

    assert result.exit_code == 0, result.stdout
    output = " ".join(result.stdout.split())
    assert "Run plain `loro configure`" in output
    assert "small appear separately only when they differ" in output
    assert "Web retrieval needs all three gates" in output
    profile = yaml.safe_load(
        (tmp_path / ".loro/agents/web-reader.agent.yaml").read_text(encoding="utf-8")
    )
    assert profile["spec"]["permissions"]["shell"] == "deny"
    assert profile["spec"]["permissions"]["network"] == "ask"
    assert profile["spec"]["tools"]["allow"] == ["shell.run"]
    config = tomllib.loads((tmp_path / ".loro/config.local.toml").read_text())
    assert config["permissions"]["web"] == "ask"
    assert "curl" in config["sandbox"]["profiles"]["controlled-shell"]["allowed_executables"]


def test_profile_wizard_rejects_non_spec_profile_name(tmp_path, monkeypatch) -> None:
    _configure_environment(tmp_path, monkeypatch)

    result = CliRunner().invoke(app, ["setup", "profile", "--name", "Bad Name"])

    assert result.exit_code == 2
    assert "lowercase words" in result.stderr
    assert not (tmp_path / ".loro/agents/Bad Name.agent.yaml").exists()
