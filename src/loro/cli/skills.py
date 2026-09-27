"""`loro skills`: Agent Skills discovery and governance."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import typer

from loro.cli._common import _audit, console
from loro.config import (
    load_config,
    write_config_sections,
)
from loro.skill_compat import (
    SkillCompatibilityError,
    apply_mcp_import,
    import_compatible_skills,
    inspect_compatibility,
)
from loro.skills import SkillError, SkillRegistry

skills_app = typer.Typer(help="Discover and govern portable Agent Skills packages.")


@skills_app.command("list")
def skills_list() -> None:
    """List validated skill metadata without loading instruction bodies."""
    try:
        skills = SkillRegistry(load_config().skills).discover()
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=[skill.to_payload() for skill in skills])


@skills_app.command("show")
def skills_show(name: Annotated[str, typer.Argument(help="Skill name.")]) -> None:
    """Load one enabled skill after validation and show its provenance."""
    try:
        skill = SkillRegistry(load_config().skills).load(name)
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data={**skill.metadata.to_payload(), "instructions": skill.instructions})


@skills_app.command("validate")
def skills_validate(
    source: Annotated[Path, typer.Argument(help="Skill package directory.")],
) -> None:
    """Validate a skill package and print its review digest."""
    config = load_config().skills.model_copy(
        update={"project_paths": [str(source.parent)], "allow_user": False}
    )
    try:
        skill = next(
            item
            for item in SkillRegistry(config).discover()
            if item.path.resolve() == source.resolve()
        )
    except (SkillError, StopIteration) as error:
        raise typer.BadParameter(str(error) or f"Skill package not found: {source}") from error
    console.print_json(data=skill.to_payload())


def _skills_import_compatibility(
    source: Path,
    *,
    kind: str,
    expected_digest: str | None,
    scope: str,
    include_mcp: bool,
    execute: bool,
    output: Path,
) -> None:
    if scope not in {"user", "project"}:
        raise typer.BadParameter("Skill scope must be user or project.")
    config = load_config()
    try:
        report = inspect_compatibility(source, kind, config.skills)  # type: ignore[arg-type]
    except SkillCompatibilityError as error:
        raise typer.BadParameter(str(error)) from error
    if not execute:
        console.print_json(data=report.to_payload())
        return
    if not expected_digest:
        raise typer.BadParameter("Compatibility import requires --expected-digest with --execute.")
    resolved_config = config.model_copy(deep=True)
    installed = []
    try:
        mcp_servers = apply_mcp_import(resolved_config, report) if include_mcp else []
        installed = import_compatible_skills(
            report,
            SkillRegistry(config.skills),
            expected_digest=expected_digest,
            scope=scope,  # type: ignore[arg-type]
        )
        if mcp_servers:
            write_config_sections(output, resolved_config, ["mcp"])
    except (OSError, SkillError) as error:
        for skill in installed:
            shutil.rmtree(skill.path, ignore_errors=True)
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "skill.compatibility_imported",
        kind=kind,
        source=str(source),
        source_digest=report.digest,
        skills=[skill.name for skill in installed],
        mcp_servers=mcp_servers,
    )
    console.print_json(
        data={
            "kind": kind,
            "source_digest": report.digest,
            "installed_skills": [skill.to_payload() for skill in installed],
            "imported_mcp_servers": mcp_servers,
            "unsupported_components": list(report.unsupported_components),
        }
    )


@skills_app.command("import-claude")
def skills_import_claude(
    source: Annotated[Path, typer.Argument(help="Claude skill or plugin directory.")],
    expected_digest: Annotated[
        str | None,
        typer.Option("--expected-digest", help="Digest from the preview report."),
    ] = None,
    scope: Annotated[str, typer.Option(help="Install scope: user or project.")] = "project",
    include_mcp: Annotated[
        bool,
        typer.Option("--include-mcp", help="Import compatible reviewed MCP definitions."),
    ] = False,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Install after digest review; preview is the default."),
    ] = False,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file updated when MCP import is enabled."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Preview or import compatible skills from a Claude skill or plugin."""
    _skills_import_compatibility(
        source,
        kind="claude",
        expected_digest=expected_digest,
        scope=scope,
        include_mcp=include_mcp,
        execute=execute,
        output=output,
    )


@skills_app.command("import-pi")
def skills_import_pi(
    source: Annotated[Path, typer.Argument(help="Pi skill or package directory.")],
    expected_digest: Annotated[
        str | None,
        typer.Option("--expected-digest", help="Digest from the preview report."),
    ] = None,
    scope: Annotated[str, typer.Option(help="Install scope: user or project.")] = "project",
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Install after digest review; preview is the default."),
    ] = False,
) -> None:
    """Preview or import compatible skills from a Pi skill or package."""
    _skills_import_compatibility(
        source,
        kind="pi",
        expected_digest=expected_digest,
        scope=scope,
        include_mcp=False,
        execute=execute,
        output=Path(".loro/config.local.toml"),
    )


def _set_skill_state(name: str, state: str) -> None:
    try:
        skill = SkillRegistry(load_config().skills).set_state(name, state)  # type: ignore[arg-type]
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("skill.state_changed", name=name, state=state, digest=skill.digest)
    console.print_json(data=skill.to_payload())


@skills_app.command("enable")
def skills_enable(name: Annotated[str, typer.Argument(help="Skill name.")]) -> None:
    """Enable a validated skill package."""
    _set_skill_state(name, "enabled")


@skills_app.command("disable")
def skills_disable(name: Annotated[str, typer.Argument(help="Skill name.")]) -> None:
    """Disable a skill without removing its package."""
    _set_skill_state(name, "disabled")


@skills_app.command("quarantine")
def skills_quarantine(name: Annotated[str, typer.Argument(help="Skill name.")]) -> None:
    """Quarantine a skill until its digest is reviewed again."""
    _set_skill_state(name, "quarantined")


@skills_app.command("install")
def skills_install(
    source: Annotated[Path, typer.Argument(help="Reviewed local skill package.")],
    expected_digest: Annotated[
        str, typer.Option("--expected-digest", help="Digest printed by skills validate.")
    ],
    scope: Annotated[str, typer.Option(help="Install scope: user or project.")] = "project",
) -> None:
    """Install a local package only when its reviewed digest matches."""
    if scope not in {"user", "project"}:
        raise typer.BadParameter("Skill scope must be user or project.")
    try:
        skill = SkillRegistry(load_config().skills).install(
            source,
            expected_digest=expected_digest,
            scope=scope,  # type: ignore[arg-type]
        )
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "skill.installed", name=skill.name, scope=scope, digest=skill.digest, source=str(source)
    )
    console.print_json(data=skill.to_payload())


@skills_app.command("remove")
def skills_remove(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm package removal.")] = False,
) -> None:
    """Remove a user or project skill package."""
    if not yes:
        raise typer.BadParameter("Skill removal requires --yes.")
    try:
        removed = SkillRegistry(load_config().skills).remove(name)
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("skill.removed", name=name, path=str(removed))
    console.print(f"Removed: {removed}")


@skills_app.command("propose")
def skills_propose(
    source: Annotated[Path, typer.Argument(help="Skill package directory.")],
) -> None:
    """Stage an immutable skill proposal for explicit review."""
    try:
        proposal = SkillRegistry(load_config().skills).propose(source)
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("skill.proposed", proposal_id=proposal.name, source=str(source))
    console.print_json(data={"proposal_id": proposal.name, "path": str(proposal)})


@skills_app.command("review")
def skills_review(
    proposal_id: Annotated[str, typer.Argument(help="Proposal ID.")],
    accept: Annotated[
        bool, typer.Option("--accept", help="Install the reviewed proposal.")
    ] = False,
    reject: Annotated[bool, typer.Option("--reject", help="Reject the proposal.")] = False,
) -> None:
    """Accept or reject a staged skill proposal exactly once."""
    if accept == reject:
        raise typer.BadParameter("Choose exactly one of --accept or --reject.")
    try:
        result = SkillRegistry(load_config().skills).review(proposal_id, accept=accept)
    except SkillError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("skill.reviewed", proposal_id=proposal_id, accepted=accept)
    console.print_json(data=result)
