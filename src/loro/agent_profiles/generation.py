from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from loro.agent_profiles.registry import AgentProfileRegistry, load_path
from loro.config import LoroConfig
from loro.fileio import atomic_write_text
from loro.skills import SkillRegistry
from loro.tool_schemas import tool_catalog

GENERATION_CONTRACT = "loro.oap-profile-generation.v1"


class ProfileDraft(BaseModel):
    """The deliberately small model-authored surface compiled into canonical OAP."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=1, max_length=500)
    instructions: str = Field(min_length=1, max_length=20_000)
    objectives: list[str] = Field(default_factory=list, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    tools: list[str] = Field(default_factory=list, max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=32)
    mcp_servers: list[str] = Field(default_factory=list, max_length=16)
    extends: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    default_permission: Literal["allow", "ask", "deny"] = "ask"
    shell_permission: Literal["allow", "ask", "deny"] = "ask"
    edit_permission: Literal["allow", "ask", "deny"] = "ask"
    network_permission: Literal["allow", "ask", "deny"] = "deny"


def profile_generation_prompt(
    request: str,
    config: LoroConfig,
    *,
    project_root: Path,
    preferred_name: str | None = None,
    extends: str | None = None,
    feedback: str | None = None,
) -> str:
    tools = [item.name for item in tool_catalog(config)]
    try:
        skills = [
            item.name for item in SkillRegistry(config.skills).discover() if item.state == "enabled"
        ]
    except Exception:
        skills = []
    profiles = [
        item.name
        for item in AgentProfileRegistry(
            config.agent_profiles, cwd=project_root, safety=config.safety
        ).discover()
    ]
    correction = (
        "\nThe previous draft was rejected. Return a complete corrected replacement. "
        f"Feedback: {feedback[:2000]}"
        if feedback
        else ""
    )
    return (
        "Design one portable Open Agent Profile 1.0. Return exactly one JSON object and no "
        "markdown. Use exactly these fields: name, description, instructions, objectives, "
        "constraints, tools, skills, mcp_servers, extends, default_permission, "
        "shell_permission, edit_permission, network_permission. Permission values are allow, "
        "ask, or deny. Prefer least authority: include only capabilities necessary for the "
        "request, never invent tools, skills, MCP servers, credentials, or filesystem paths. "
        "Loro will compile and validate the final OAP document.\n"
        f"Available tools: {json.dumps(tools)}\n"
        f"Available skills: {json.dumps(skills)}\n"
        f"Configured MCP servers: {json.dumps(sorted(config.mcp.servers))}\n"
        f"Available parent profiles: {json.dumps(profiles)}\n"
        f"Preferred name: {preferred_name or 'choose a descriptive kebab-case name'}\n"
        f"Requested parent: {extends or 'none'}\n"
        f"PROFILE REQUEST:\n{request.strip()}" + correction
    )


def generate_profile_proposal(
    request: str,
    config: LoroConfig,
    project_root: Path,
    author: Callable[[str], str],
    *,
    preferred_name: str | None = None,
    extends: str | None = None,
    autonomous: bool = False,
) -> dict[str, Any]:
    if not request.strip():
        raise ValueError("Describe the profile to generate.")
    if config.model.provider == "mock":
        raise ValueError(
            "Profile generation requires a configured model provider. Run `loro configure`, "
            "then retry."
        )
    feedback: str | None = None
    for _attempt in range(3):
        response = author(
            profile_generation_prompt(
                request,
                config,
                project_root=project_root,
                preferred_name=preferred_name,
                extends=extends,
                feedback=feedback,
            )
        )
        try:
            draft = _parse_draft(response)
            if preferred_name:
                draft = draft.model_copy(update={"name": preferred_name})
            if extends:
                draft = draft.model_copy(update={"extends": extends})
            return build_profile_proposal(
                draft.model_dump(mode="json"),
                config,
                project_root,
                request=request,
                autonomous=autonomous,
            )
        except (ValueError, ValidationError) as error:
            feedback = str(error)
    raise ValueError(
        f"The model could not produce a valid portable profile after two corrections: {feedback}"
    )


def build_profile_proposal(
    draft_payload: Mapping[str, Any],
    config: LoroConfig,
    project_root: Path,
    *,
    request: str = "Agent-authored profile",
    autonomous: bool = True,
) -> dict[str, Any]:
    draft = ProfileDraft.model_validate(dict(draft_payload))
    document, warnings = _compile(draft, config, project_root.resolve())
    # Exercise the exact canonical OAP boundary used for imported and persisted profiles.
    staged = project_root.resolve() / ".loro" / f".profile-review-{uuid4().hex}.agent.yaml"
    atomic_write_text(staged, yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
    try:
        load_path(staged)
    finally:
        staged.unlink(missing_ok=True)
    return {
        "contract": GENERATION_CONTRACT,
        "status": "proposed",
        "autonomous": autonomous,
        "document": document,
        "warnings": warnings,
        "request_digest": "sha256:" + hashlib.sha256(request.encode("utf-8")).hexdigest(),
    }


def store_profile_proposal(
    proposal: Mapping[str, Any], config: LoroConfig, project_root: Path
) -> Path:
    root = project_root.resolve() / config.agent_profiles.generation_proposal_path
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{uuid4()}.json"
    atomic_write_text(path, json.dumps(dict(proposal), indent=2, sort_keys=True) + "\n")
    return path


def save_generated_profile(
    document: Mapping[str, Any],
    config: LoroConfig,
    project_root: Path,
    *,
    scope: Literal["project", "portable", "user", "universal"] = "project",
) -> Path:
    root = project_root.resolve()
    parsed = dict(document)
    name = str((parsed.get("metadata") or {}).get("name") or "")
    if not name:
        raise ValueError("Generated profile has no metadata.name.")
    existing = {
        item.name
        for item in AgentProfileRegistry(
            config.agent_profiles, cwd=root, safety=config.safety
        ).discover()
    }
    if name in existing:
        raise ValueError(f"Agent profile already exists: {name}")
    if scope == "project":
        output_root = (root / config.agent_profiles.project_paths[-1]).resolve()
        output_root.relative_to(root)
    elif scope == "portable":
        output_root = (root / ".agents").resolve()
        output_root.relative_to(root)
    elif scope == "universal":
        output_root = Path("~/.agentprofiles").expanduser().resolve()
    else:
        # The native user root intentionally has precedence over the universal root.
        output_root = Path(config.agent_profiles.user_paths[-1]).expanduser().resolve()
    output = output_root / f"{name}.agent.yaml"
    atomic_write_text(output, yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True))
    try:
        load_path(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return output


def _parse_draft(content: str) -> ProfileDraft:
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return ProfileDraft.model_validate(candidate)
    raise ValueError("The model did not return one JSON profile draft.")


def _compile(
    draft: ProfileDraft, config: LoroConfig, project_root: Path
) -> tuple[dict[str, Any], list[str]]:
    available_tools = {item.name for item in tool_catalog(config)}
    invalid_tools = sorted(set(draft.tools) - available_tools)
    if invalid_tools:
        raise ValueError("Unknown or unavailable tools: " + ", ".join(invalid_tools))
    try:
        available_skills = {
            item.name for item in SkillRegistry(config.skills).discover() if item.state == "enabled"
        }
    except Exception:
        available_skills = set()
    invalid_skills = sorted(set(draft.skills) - available_skills)
    if invalid_skills:
        raise ValueError("Unknown or disabled skills: " + ", ".join(invalid_skills))
    invalid_servers = sorted(set(draft.mcp_servers) - set(config.mcp.servers))
    if invalid_servers:
        raise ValueError("Unknown MCP servers: " + ", ".join(invalid_servers))
    profiles = {
        item.name
        for item in AgentProfileRegistry(
            config.agent_profiles, cwd=project_root, safety=config.safety
        ).discover()
    }
    if draft.extends and draft.extends not in profiles:
        raise ValueError(f"Unknown parent profile: {draft.extends}")
    tools: dict[str, Any] = {
        "policy": "allowlist",
        "allow": list(dict.fromkeys(draft.tools)),
    }
    if draft.skills:
        tools["skills"] = [{"name": item} for item in dict.fromkeys(draft.skills)]
    if draft.mcp_servers:
        tools["mcp_servers"] = [
            _mcp_reference(name, config) for name in dict.fromkeys(draft.mcp_servers)
        ]
    document: dict[str, Any] = {
        "oap": "1.0",
        "kind": "AgentProfile",
        "metadata": {
            "name": draft.name,
            "revision": 1,
            "description": draft.description,
        },
        "spec": {
            "role": {
                "instructions": draft.instructions,
                **({"objectives": draft.objectives} if draft.objectives else {}),
                **({"constraints": draft.constraints} if draft.constraints else {}),
            },
            "tools": tools,
            "permissions": {
                "default": draft.default_permission,
                "shell": draft.shell_permission,
                "edit": draft.edit_permission,
                "network": draft.network_permission,
            },
            "lifecycle": {"writeback": "propose"},
        },
        "state": {"revision": 1, "facts": [], "preferences": []},
        "history": [],
    }
    if draft.extends:
        document["extends"] = [{"name": draft.extends}]
    return document, []


def _mcp_reference(name: str, config: LoroConfig) -> dict[str, Any]:
    server = config.mcp.servers[name]
    if server.transport == "stdio":
        return {
            "name": name,
            "transport": "stdio",
            "command": server.command,
            **({"args": server.args} if server.args else {}),
            **(
                {"env": {key: "${" + key + "}" for key in server.env_allowlist}}
                if server.env_allowlist
                else {}
            ),
        }
    return {"name": name, "transport": "http", "url": server.url}
