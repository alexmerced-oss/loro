from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console

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

agents_app = typer.Typer(help="Discover, explain, and govern Open Agent Profiles.")
console = Console()


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
