"""`loro docs|slides|sheets|brief|artifacts`: governed artifact generation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from loro.artifacts.common import verify_provenance
from loro.artifacts.documents import create_document_artifact
from loro.artifacts.presentations import create_presentation_artifact
from loro.artifacts.spreadsheets import create_spreadsheet_artifact
from loro.cli._common import (
    DEFAULT_ARTIFACT_DIR,
    _create_and_print_artifact,
    _create_and_print_brief,
    console,
)

docs_app = typer.Typer(help="Create and transform documents.")


slides_app = typer.Typer(help="Create and transform presentations.")


sheets_app = typer.Typer(help="Create and transform spreadsheets.")


brief_app = typer.Typer(help="Create enterprise briefs.")


artifacts_app = typer.Typer(help="Verify generated artifact provenance and integrity.")


@docs_app.command("create")
def docs_create(
    prompt: Annotated[str, typer.Argument(help="Document prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="Explicitly create an offline scaffold without AI.")
    ] = False,
) -> None:
    _create_and_print_artifact(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        context="artifact.document",
        kind="document",
        use_ai=not no_ai,
        factory=create_document_artifact,
    )


@artifacts_app.command("verify")
def artifacts_verify(
    provenance: Annotated[Path, typer.Argument(help="Artifact provenance JSON path.")],
) -> None:
    """Verify every file digest and byte count in an artifact provenance record."""

    report = verify_provenance(provenance)
    console.print_json(data=report.to_payload())
    raise typer.Exit(code=0 if report.ok else 1)


@slides_app.command("create")
def slides_create(
    prompt: Annotated[str, typer.Argument(help="Presentation prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="Explicitly create an offline scaffold without AI.")
    ] = False,
) -> None:
    _create_and_print_artifact(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        context="artifact.presentation",
        kind="presentation",
        use_ai=not no_ai,
        factory=create_presentation_artifact,
    )


@sheets_app.command("analyze")
def sheets_analyze(
    prompt: Annotated[str, typer.Argument(help="Spreadsheet prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="Explicitly create an offline scaffold without AI.")
    ] = False,
) -> None:
    _create_and_print_artifact(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        context="artifact.spreadsheet",
        kind="spreadsheet",
        use_ai=not no_ai,
        factory=create_spreadsheet_artifact,
    )


@sheets_app.command("create")
def sheets_create(
    prompt: Annotated[str, typer.Argument(help="Spreadsheet prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="Explicitly create an offline scaffold without AI.")
    ] = False,
) -> None:
    _create_and_print_artifact(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        context="artifact.spreadsheet",
        kind="spreadsheet",
        use_ai=not no_ai,
        factory=create_spreadsheet_artifact,
    )


@brief_app.command("meeting")
def brief_meeting(
    prompt: Annotated[str, typer.Argument(help="Meeting brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="Explicitly create an offline scaffold without AI.")
    ] = False,
) -> None:
    _create_and_print_brief(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        brief_type="meeting",
        use_ai=not no_ai,
    )


@brief_app.command("project")
def brief_project(
    prompt: Annotated[str, typer.Argument(help="Project brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="Explicitly create an offline scaffold without AI.")
    ] = False,
) -> None:
    _create_and_print_brief(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        brief_type="project",
        use_ai=not no_ai,
    )


@brief_app.command("incident")
def brief_incident(
    prompt: Annotated[str, typer.Argument(help="Incident brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="Explicitly create an offline scaffold without AI.")
    ] = False,
) -> None:
    _create_and_print_brief(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        brief_type="incident",
        use_ai=not no_ai,
    )


@brief_app.command("executive")
def brief_executive(
    prompt: Annotated[str, typer.Argument(help="Executive brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="Explicitly create an offline scaffold without AI.")
    ] = False,
) -> None:
    _create_and_print_brief(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        brief_type="executive",
        use_ai=not no_ai,
    )
