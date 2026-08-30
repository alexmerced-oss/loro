from __future__ import annotations

import json
from pathlib import Path

from loro.agent_profiles.generation import (
    generate_profile_proposal,
    save_generated_profile,
)
from loro.config import AgentProfilesConfig, LoroConfig, ModelConfig
from loro.tool_runtime import ToolCall, ToolRegistry


def _draft(**updates: object) -> str:
    value = {
        "name": "release-reviewer",
        "description": "Reviews releases for concrete evidence and bounded risk.",
        "instructions": "Review release evidence and do not modify files.",
        "objectives": ["Find release-blocking defects."],
        "constraints": ["Do not edit files."],
        "tools": ["file.read"],
        "skills": [],
        "mcp_servers": [],
        "extends": None,
        "default_permission": "ask",
        "shell_permission": "deny",
        "edit_permission": "deny",
        "network_permission": "deny",
    }
    value.update(updates)
    return json.dumps(value)


def _config(tmp_path: Path) -> LoroConfig:
    return LoroConfig(
        model=ModelConfig(provider="openai", model="fixture"),
        agent_profiles=AgentProfilesConfig(
            managed_paths=[],
            user_paths=[],
            project_paths=[".loro/agents"],
            allow_user=False,
        ),
    )


def test_generation_repairs_and_saves_only_after_review(tmp_path: Path) -> None:
    config = _config(tmp_path)
    responses = iter(["not json", _draft()])
    proposal = generate_profile_proposal(
        "Create a cautious release reviewer.",
        config,
        tmp_path,
        lambda _prompt: next(responses),
    )

    assert proposal["status"] == "proposed"
    assert proposal["document"]["spec"]["tools"]["allow"] == ["file.read"]
    path = save_generated_profile(proposal["document"], config, tmp_path)
    assert path == tmp_path / ".loro" / "agents" / "release-reviewer.agent.yaml"


def test_session_tool_persists_autonomous_proposal_without_profile(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = ToolRegistry(_config(tmp_path)).execute(
        ToolCall(
            name="profile.create",
            origin="model",
            args={
                "name": "research-helper",
                "description": "Researches one bounded question.",
                "instructions": "Research the delegated question and cite findings.",
                "tools": ["file.read"],
            },
        )
    )

    payload = json.loads(result.output)
    assert result.ok is True
    assert payload["status"] == "proposed"
    assert Path(payload["proposal_path"]).is_file()
    assert not (tmp_path / ".loro" / "agents" / "research-helper.agent.yaml").exists()


def test_universal_profile_root_is_lower_precedence_than_native_user_root() -> None:
    config = AgentProfilesConfig()
    assert config.user_paths == ["~/.agentprofiles", "~/.config/loro/agents"]
