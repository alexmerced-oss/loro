from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from loro.agent_profiles import (
    AgentProfileRegistry,
    AgentStateDelta,
    ProfileProposalStore,
    apply_delta,
    build_effective_profile,
    load_path,
)
from loro.audit import AuditLogger
from loro.config import load_config, write_config_sections
from loro.fileio import atomic_write_text
from loro.identity import resolve_identity
from loro.skills import SkillRegistry
from loro.tool_schemas import BUILTIN_TOOL_SCHEMAS

agents_app = typer.Typer(help="Discover, explain, and govern Open Agent Profiles.")
console = Console()

_PROFILE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CAPABILITY_PRESETS: dict[str, tuple[str, ...] | None] = {
    "chat": (),
    "read": ("file.read", "file.search", "git.status", "git.diff", "memory.search"),
    "web": (
        "file.read",
        "file.search",
        "memory.search",
        "shell.run",
    ),
    "coding": tuple(schema.name for schema in BUILTIN_TOOL_SCHEMAS),
    "inherit": None,
}


def _registry() -> AgentProfileRegistry:
    config = load_config()
    return AgentProfileRegistry(config.agent_profiles, safety=config.safety)


def _audit(config):
    return AuditLogger(config.audit, resolve_identity(config.identity), safety_config=config.safety)


@agents_app.command("list")
def list_profiles() -> None:
    """List resolved profile names and discovery trust."""
    for item in _registry().discover():
        console.print(
            json.dumps(
                {
                    "name": item.name,
                    "revision": item.revision,
                    "trust": item.trust,
                    "source": str(item.source_path),
                    "shadowed": [str(path) for path in item.shadowed],
                },
                sort_keys=True,
            )
        )


@agents_app.command("show")
def show(name: str) -> None:
    """Show one resolved profile with root-derived trust."""
    item = _registry().load(name)
    payload = item.document.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["metadata"]["effectiveTrust"] = item.trust
    console.print(json.dumps(payload, indent=2, ensure_ascii=False))


@agents_app.command("explain")
def explain(name: str) -> None:
    """Explain the effective profile after managed-policy narrowing."""
    config = load_config()
    effective = build_effective_profile(
        AgentProfileRegistry(config.agent_profiles, safety=config.safety).load(name), config
    )
    console.print(
        json.dumps(
            {
                "name": name,
                "revision": effective.resolved.document.metadata.revision,
                "trust": effective.resolved.trust,
                "spec_digest": effective.resolved.spec_digest,
                "lineage": list(effective.resolved.lineage),
                "model": {"provider": effective.model.provider, "model": effective.model.model},
                "tools": sorted(effective.tools),
                "mcp_servers": sorted(effective.mcp_servers),
                "skills": sorted(effective.skills),
                "subagents": sorted(effective.subagents),
                "max_subagent_depth": effective.max_subagent_depth,
                "memory_stores": sorted(effective.memory_stores),
                "memory_scopes": sorted(effective.memory_scopes),
                "permissions": effective.permissions.model_dump(mode="json"),
                "runtime": effective.runtime.model_dump(mode="json"),
                "writeback": effective.writeback,
                "adjustments": [item.to_payload() for item in effective.adjustments],
            },
            indent=2,
        )
    )


@agents_app.command("validate")
def validate(path: Path) -> None:
    """Validate an OAP YAML, JSON, or Markdown document."""
    document = load_path(path)
    console.print(
        json.dumps(
            {"ok": True, "name": document.metadata.name, "revision": document.metadata.revision}
        )
    )


@agents_app.command("create")
def create(
    name: str,
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path(".loro/agents"),
    instructions: Annotated[str, typer.Option("--instructions")] = "Be helpful and precise.",
) -> None:
    """Create a minimal project profile without granting new authority."""
    path = output_dir / f"{name}.agent.yaml"
    if path.exists():
        raise typer.BadParameter(f"Profile already exists: {path}")
    payload = {
        "apiVersion": "oap/v1",
        "kind": "AgentProfile",
        "metadata": {"name": name, "revision": 1},
        "spec": {
            "role": {"instructions": instructions},
            "tools": {"policy": "inherit"},
            "writeback": "propose",
        },
        "state": [],
        "history": [],
    }
    load_path_from_payload = yaml.safe_dump(payload, sort_keys=False)
    atomic_write_text(path, load_path_from_payload)
    load_path(path)
    console.print(str(path))


@agents_app.command("digest")
def digest(name: str) -> None:
    """Print whole-profile and authority-stable spec digests."""
    profile = _registry().load(name)
    console.print(
        json.dumps(
            {"profile_digest": profile.profile_digest, "spec_digest": profile.spec_digest}, indent=2
        )
    )


@agents_app.command("history")
def history(name: str) -> None:
    """Show the profile revision history."""
    console.print(
        json.dumps(
            [item.model_dump(mode="json") for item in _registry().load(name).document.history],
            indent=2,
        )
    )


@agents_app.command("state")
def state(name: str) -> None:
    """Show profile state as data, never as authority."""
    console.print(
        json.dumps(
            [item.model_dump(mode="json") for item in _registry().load(name).document.state],
            indent=2,
        )
    )


@agents_app.command("forget")
def forget(
    name: str,
    entry_id: str,
    approve: Annotated[bool, typer.Option("--approve")] = False,
) -> None:
    """Remove one state entry after explicit local approval."""
    if not approve:
        raise typer.BadParameter("State removal requires --approve.")
    config = load_config()
    profile = AgentProfileRegistry(config.agent_profiles, safety=config.safety).load(name)
    delta = AgentStateDelta(
        profile=name,
        base_revision=profile.document.metadata.revision,
        spec_digest=profile.spec_digest,
        operations=[{"op": "remove", "path": f"/state/{entry_id}"}],
    )
    audit = _audit(config)
    result = apply_delta(
        profile.source_path,
        delta,
        config.agent_profiles,
        config.safety,
        event_handler=lambda event, payload: audit.write(event, **payload),
    )
    console.print(f"Applied revision {result.metadata.revision}")


@agents_app.command("apply")
def apply(
    name: str,
    delta_path: Path,
    approve: Annotated[bool, typer.Option("--approve")] = False,
) -> None:
    """Apply an explicitly approved, digest-bound state delta."""
    if not approve:
        raise typer.BadParameter("Delta application requires --approve.")
    config = load_config()
    profile = AgentProfileRegistry(config.agent_profiles, safety=config.safety).load(name)
    delta = AgentStateDelta.model_validate_json(delta_path.read_text(encoding="utf-8"))
    audit = _audit(config)
    result = apply_delta(
        profile.source_path,
        delta,
        config.agent_profiles,
        config.safety,
        event_handler=lambda event, payload: audit.write(event, **payload),
    )
    console.print(f"Applied revision {result.metadata.revision}")


@agents_app.command("proposals")
def proposals() -> None:
    """List pending capability proposals without applying them."""
    config = load_config()
    records = ProfileProposalStore(config.agent_profiles, config.safety).list()
    console.print(json.dumps([item.to_payload() for item in records], indent=2))


@agents_app.command("review")
def review(
    proposal_id: str,
    accept: Annotated[bool, typer.Option("--accept")] = False,
    reject: Annotated[bool, typer.Option("--reject")] = False,
) -> None:
    """Record a human decision; accepted capability proposals are not auto-applied."""
    if accept == reject:
        raise typer.BadParameter("Choose exactly one of --accept or --reject.")
    config = load_config()
    store = ProfileProposalStore(config.agent_profiles, config.safety)
    try:
        proposal = store.get(proposal_id)
    except FileNotFoundError as error:
        raise typer.BadParameter(f"Proposal not found: {proposal_id}") from error
    if proposal.status != "pending":
        raise typer.BadParameter(f"Proposal is already {proposal.status}.")
    if reject:
        status = store.set_status(proposal, "rejected").status
        _audit(config).write(
            "agent_profile.proposal_reviewed",
            proposal_id=proposal_id,
            profile=proposal.profile,
            decision="reject",
            status=status,
        )
        console.print(status)
        return
    if proposal.kind != "state-delta" or proposal.delta is None:
        status = store.set_status(proposal, "accepted-not-applied").status
        _audit(config).write(
            "agent_profile.proposal_reviewed",
            proposal_id=proposal_id,
            profile=proposal.profile,
            decision="accept",
            status=status,
        )
        console.print(status)
        return
    profile = AgentProfileRegistry(config.agent_profiles, safety=config.safety).load(
        proposal.profile
    )
    audit = _audit(config)
    apply_delta(
        profile.source_path,
        proposal.delta,
        config.agent_profiles,
        config.safety,
        event_handler=lambda event, payload: audit.write(event, **payload),
    )
    status = store.set_status(proposal, "applied").status
    audit.write(
        "agent_profile.proposal_reviewed",
        proposal_id=proposal_id,
        profile=proposal.profile,
        decision="accept",
        status=status,
    )
    console.print(status)


def setup_agents(
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(".loro/config.local.toml"),
) -> None:
    """Configure profile discovery and the managed writeback ceiling."""
    config = load_config()
    config.agent_profiles.enabled = typer.confirm(
        "Enable Open Agent Profiles?", default=config.agent_profiles.enabled
    )
    config.agent_profiles.allow_project = typer.confirm(
        "Allow project profiles?", default=config.agent_profiles.allow_project
    )
    value = typer.prompt(
        "Writeback ceiling (off/propose/auto)", default=config.agent_profiles.writeback
    ).strip()
    if value not in {"off", "propose", "auto"}:
        raise typer.BadParameter("Writeback must be off, propose, or auto.")
    config.agent_profiles.writeback = value  # type: ignore[assignment]
    written = write_config_sections(output, config, ["agent_profiles"])
    console.print(f"Wrote agent profile config: {written}")


def profile_wizard(
    name: Annotated[
        str | None,
        typer.Option("--name", help="Profile name. Omit to be prompted."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Project profile directory."),
    ] = Path(".loro/agents"),
    config_output: Annotated[
        Path,
        typer.Option("--config-output", help="Local config file to update."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Create a complete, spec-compliant Open Agent Profile interactively."""

    config = load_config()
    _wizard_note(
        "Profile setup",
        "A profile defines an assistant's instructions and narrows the model, tools, and "
        "permissions already authorized by this project. Managed and project policy always "
        "remain the final ceiling.",
    )
    profile_name = (name or typer.prompt("Profile name")).strip()
    if _PROFILE_NAME.fullmatch(profile_name) is None:
        raise typer.BadParameter(
            "Profile names must use lowercase words separated by single hyphens."
        )
    profile_path = output_dir / f"{profile_name}.agent.yaml"
    if profile_path.exists():
        raise typer.BadParameter(f"Profile already exists: {profile_path}")

    _wizard_note(
        "Identity",
        "The description helps people recognize the profile. System instructions tell the "
        "model how to behave; they do not grant tools or bypass permissions.",
    )
    description = typer.prompt("Description", default=f"{profile_name} Loro profile").strip()
    instructions = typer.prompt(
        "System instructions",
        default="Be helpful, precise, and transparent about tool use.",
    ).strip()
    model_spec = _choose_model_spec(config)
    _wizard_note(
        "Capabilities",
        "Choose the smallest preset that fits the work. Tool availability and permission "
        "decisions are separate: a listed tool may still be denied or require approval.",
    )
    preset = _choose_one(
        "Capability preset",
        [
            ("chat", "Chat only - model responses, no tools"),
            ("read", "Read workspace - files, Git inspection, and local memory"),
            ("web", "Web retrieval - reads plus approved curl; no general shell"),
            ("coding", "Coding harness - tools available with approval gates"),
            ("inherit", "Inherit - use the resolved project's available tools"),
            ("custom", "Custom - choose tools and shell/web permissions separately"),
        ],
        default="coding",
    )
    tools = _tools_for_preset(preset)
    shell_enabled = preset not in {"chat", "read", "web"}
    web_enabled = preset == "web"
    if preset == "custom" and tools is not None and "shell.run" in tools:
        _wizard_note(
            "Shell and web",
            "shell.run is the transport for both local commands and governed HTTP retrieval, "
            "but Loro authorizes them independently. Web retrieval permits approved curl "
            "requests; it is not a built-in search-engine API.",
        )
        shell_enabled = typer.confirm(
            "Allow general sandboxed shell commands (approval required)?", default=True
        )
        web_enabled = typer.confirm(
            "Allow governed web retrieval with curl (approval required)?", default=False
        )
    _wizard_note(
        "Extensions",
        "Skills and MCP servers appear only when already enabled in this project. Selecting "
        "one adds it to the profile allowlist; its runtime permission still applies.",
    )
    skills = _choose_skills(config)
    mcp_servers = _choose_mcp_servers(config)
    permissions = _permission_spec(
        tools,
        skills=skills,
        mcp_servers=mcp_servers,
        shell_enabled=shell_enabled,
        web_enabled=web_enabled,
    )
    _wizard_note(
        "Memory",
        "Profile state is always available. Local memory is private to this installation; "
        "shared memory is governed by the configured enterprise identity and tenant scope.",
    )
    stores = ["oap-state"]
    if config.memory.local.enabled and typer.confirm("Allow local memory?", default=True):
        stores.append("local")
    if config.memory.shared.enabled and typer.confirm(
        "Allow governed shared memory?", default=False
    ):
        stores.append("shared")
    _wizard_note(
        "Workspace",
        "The workspace root limits where this profile may use file and shell tools. Use '.' "
        "to keep it inside the current project.",
    )
    workspace_root = typer.prompt("Workspace root", default=".").strip()
    _wizard_note(
        "Learning",
        "Learning controls profile state updates, not model training. 'Propose' requires review; "
        "'auto' is still bounded by the project's writeback ceiling.",
    )
    writeback = _choose_one(
        "Profile learning",
        [
            ("off", "Off"),
            ("propose", "Propose reviewed state updates"),
            ("auto", "Apply ordinary state updates within the configured ceiling"),
        ],
        default="propose",
    )

    payload = _profile_payload(
        name=profile_name,
        description=description,
        instructions=instructions,
        model=model_spec,
        tools=tools,
        skills=skills,
        mcp_servers=mcp_servers,
        permissions=permissions,
        workspace_root=workspace_root,
        memory_stores=stores,
        writeback=writeback,
    )
    document = load_path_from_payload(payload)

    config.agent_profiles.enabled = True
    config.agent_profiles.allow_project = True
    if web_enabled:
        _configure_web_ceiling(config)
    _wizard_note(
        "Default profile",
        "The default is selected automatically by plain `loro`, `loro run`, and other agent "
        "commands unless an explicit --agent option overrides it.",
    )
    make_default = typer.confirm("Make this the default profile?", default=True)
    config.agent_profiles.default_profile = (
        profile_name if make_default else config.agent_profiles.default_profile
    )

    atomic_write_text(profile_path, yaml.safe_dump(document, sort_keys=False))
    try:
        load_path(profile_path)
        effective = build_effective_profile(
            AgentProfileRegistry(config.agent_profiles, safety=config.safety).load(profile_name),
            config,
        )
        sections = ["agent_profiles"]
        if web_enabled:
            sections.extend(["permissions", "sandbox"])
        written = write_config_sections(config_output, config, sections)
    except Exception:
        profile_path.unlink(missing_ok=True)
        raise

    _audit(config).write(
        "agent_profile.created",
        profile=profile_name,
        source=str(profile_path),
        default=make_default,
        spec_digest=effective.resolved.spec_digest,
        tool_count=len(effective.tools),
        skill_count=len(effective.skills),
    )
    console.print(f"Created profile: {profile_path}")
    console.print(f"Updated config: {written}")
    console.print(
        f"Effective route: {effective.model.provider}/{effective.model.model} | "
        f"tools {len(effective.tools)} | skills {len(effective.skills)} | "
        f"adjustments {len(effective.adjustments)}"
    )
    if effective.adjustments:
        console.print("Run `loro agents explain " + profile_name + "` to review policy narrowing.")


@agents_app.command("configure")
def configure_profile(
    name: Annotated[
        str | None,
        typer.Option("--name", help="Profile name. Omit to be prompted."),
    ] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path(".loro/agents"),
    config_output: Annotated[Path, typer.Option("--config-output")] = Path(
        ".loro/config.local.toml"
    ),
) -> None:
    """Run the complete profile configuration wizard."""
    profile_wizard(name=name, output_dir=output_dir, config_output=config_output)


def load_path_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a concise wizard payload through the normative profile model."""
    from loro.agent_profiles.models import AgentProfileModel

    AgentProfileModel.model_validate(payload)
    return payload


def _choose_model_spec(config) -> dict[str, Any]:
    routes: list[tuple[str, str, str]] = [
        ("inherit", "Inherit configured default", ""),
        (
            f"{config.model.provider}/{config.model.model}",
            f"Primary: {config.model.provider}/{config.model.model}",
            "primary",
        ),
    ]
    if config.model.small_model != config.model.model:
        routes.append(
            (
                f"{config.model.provider}/{config.model.small_model}",
                f"Small: {config.model.provider}/{config.model.small_model}",
                "small",
            )
        )
    for tier, route in config.model.tiers.items():
        routes.append(
            (
                f"{route.provider}/{route.model}",
                f"{tier.title()}: {route.provider}/{route.model}",
                tier,
            )
        )
    configured_count = len(routes) - 1
    _wizard_note(
        "Model route",
        f"Found {configured_count} configured route{'s' if configured_count != 1 else ''}. "
        "Profiles may select only routes already authorized by Loro configuration. Run plain "
        "`loro configure` to choose a provider and dynamically discover its primary and small "
        "models; primary and small appear separately only when they differ, and configured tier "
        "routes also appear here. Re-run this wizard afterward.",
    )
    selected = _choose_one(
        "Model route",
        [(value, display) for value, display, _kind in routes],
        default=f"{config.model.provider}/{config.model.model}",
    )
    if selected == "inherit":
        return {}
    provider, model = selected.split("/", 1)
    return {"provider": provider, "id": model}


def _tools_for_preset(preset: str) -> tuple[str, ...] | None:
    if preset != "custom":
        return _CAPABILITY_PRESETS[preset]
    available = [
        (schema.name, f"{schema.name} - {schema.description}") for schema in BUILTIN_TOOL_SCHEMAS
    ]
    return tuple(_choose_many("Tools", available))


def _choose_skills(config) -> list[str]:
    discovered = [
        (item.name, f"{item.name} ({item.state})")
        for item in SkillRegistry(config.skills).discover()
        if item.state == "enabled"
    ]
    if not discovered:
        return []
    return _choose_many("Skills", discovered)


def _choose_mcp_servers(config) -> list[str]:
    servers = [
        (name, name)
        for name, server in sorted(config.mcp.servers.items())
        if config.mcp.enabled and server.enabled
    ]
    return _choose_many("MCP servers", servers) if servers else []


def _permission_spec(
    tools: tuple[str, ...] | None,
    *,
    skills: list[str],
    mcp_servers: list[str],
    shell_enabled: bool,
    web_enabled: bool,
) -> dict[str, str]:
    if tools is None:
        return {}
    selected = set(tools)
    if not selected:
        return {"default": "deny"}
    return {
        "default": "ask",
        "shell": "ask" if "shell.run" in selected and shell_enabled else "deny",
        "edit": "ask" if any(name.startswith("file.") for name in selected) else "deny",
        "artifact": "ask" if "artifact.create" in selected else "deny",
        "shared_memory": "ask" if "memory.shared_search" in selected else "deny",
        "governed_data": "ask" if "polaris.readonly" in selected else "deny",
        "mcp": "ask" if mcp_servers else "deny",
        "skills": "ask" if skills else "deny",
        "session_message": "deny",
        "web": "ask" if web_enabled else "deny",
    }


def _profile_payload(
    *,
    name: str,
    description: str,
    instructions: str,
    model: dict[str, Any],
    tools: tuple[str, ...] | None,
    skills: list[str],
    mcp_servers: list[str],
    permissions: dict[str, str],
    workspace_root: str,
    memory_stores: list[str],
    writeback: str,
) -> dict[str, Any]:
    tool_spec: dict[str, Any] = {
        "policy": "inherit" if tools is None else "allowlist",
        "mcp_servers": mcp_servers,
        "skills": skills,
    }
    if tools is not None:
        tool_spec["allow"] = list(tools)
    permission_spec: dict[str, Any] = dict(permissions)
    if workspace_root:
        permission_spec["workspace_roots"] = [workspace_root]
    return {
        "apiVersion": "oap/v1",
        "kind": "AgentProfile",
        "metadata": {"name": name, "revision": 1, "description": description},
        "spec": {
            "role": {"instructions": instructions},
            "model": model,
            "tools": tool_spec,
            "permissions": permission_spec,
            "memory": {"stores": memory_stores},
            "writeback": writeback,
        },
        "state": [],
        "history": [],
    }


def _configure_web_ceiling(config) -> None:
    _wizard_note(
        "Web access",
        "Web retrieval needs all three gates: shell.run in the profile, web permission in the "
        "project, and curl plus inherited networking in the shell sandbox. Generic shell access "
        "is controlled separately. Every request remains approval-gated when web=ask.",
    )
    if not typer.confirm(
        "Enable the project web ceiling now (web=ask, curl allowed, network inherited)?",
        default=config.permissions.web != "deny",
    ):
        console.print(
            "[yellow]Web remains subject to the current project ceiling and may be narrowed "
            "to deny. Re-run this wizard to enable it later.[/yellow]"
        )
        return
    config.permissions.web = "ask"
    profile = config.sandbox.profiles[config.sandbox.shell_profile]
    if "curl" not in profile.allowed_executables:
        profile.allowed_executables.append("curl")
    profile.network = "inherit"


def _wizard_note(title: str, message: str) -> None:
    console.print(Panel(message, title=title, border_style="cyan", expand=False))


def _choose_one(label: str, choices: list[tuple[str, str]], *, default: str) -> str:
    table = Table(title=label)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Choice")
    for index, (_value, display) in enumerate(choices, start=1):
        table.add_row(str(index), display)
    console.print(table)
    default_index = next(
        (index for index, (value, _display) in enumerate(choices, start=1) if value == default),
        1,
    )
    while True:
        selected = typer.prompt(f"Select {label.lower()}", default=default_index)
        try:
            return choices[int(selected) - 1][0]
        except (ValueError, IndexError):
            console.print(f"Choose a number from 1 to {len(choices)}.")


def _choose_many(label: str, choices: list[tuple[str, str]]) -> list[str]:
    table = Table(title=label)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Choice")
    for index, (_value, display) in enumerate(choices, start=1):
        table.add_row(str(index), display)
    console.print(table)
    while True:
        raw = typer.prompt(f"Select {label.lower()} (comma numbers, all, or none)", default="none")
        normalized = raw.strip().casefold()
        if normalized == "none":
            return []
        if normalized == "all":
            return [value for value, _display in choices]
        try:
            indexes = [int(item.strip()) for item in raw.split(",") if item.strip()]
            if not indexes or any(index < 1 or index > len(choices) for index in indexes):
                raise ValueError
        except ValueError:
            console.print(f"Choose comma-separated numbers from 1 to {len(choices)}.")
            continue
        return list(dict.fromkeys(choices[index - 1][0] for index in indexes))
