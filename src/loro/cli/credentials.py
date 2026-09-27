from __future__ import annotations

import os
from typing import Annotated

import typer
from rich.console import Console

from loro.config import load_config
from loro.credentials import CredentialError, CredentialReference, CredentialVault

credentials_app = typer.Typer(help="Store named secrets in the operating system credential vault.")
console = Console()


def _vault() -> CredentialVault:
    return CredentialVault(load_config().credentials)


@credentials_app.command("set")
def credential_set(
    ref: Annotated[str, typer.Argument(help="vault://namespace/profile/key reference.")],
    from_env: Annotated[
        str | None,
        typer.Option("--from-env", help="Import from this environment variable."),
    ] = None,
) -> None:
    """Create or replace a credential without putting it in command history."""
    try:
        reference = CredentialReference.parse(ref)
        value = (
            os.environ.get(from_env, "")
            if from_env
            else typer.prompt("Secret value", hide_input=True, confirmation_prompt=True)
        )
        if from_env and not value:
            raise typer.BadParameter(f"environment variable is missing or empty: {from_env}")
        _vault().set(reference.uri, value)
    except CredentialError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"Stored {reference.uri} in the system credential vault.")


@credentials_app.command("list")
def credential_list() -> None:
    """List non-secret credential metadata."""
    try:
        entries = _vault().list()
    except CredentialError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(
        data=[
            {"ref": entry.ref, "created_at": entry.created_at, "updated_at": entry.updated_at}
            for entry in entries
        ]
    )


@credentials_app.command("delete")
def credential_delete(
    ref: Annotated[str, typer.Argument(help="vault://namespace/profile/key reference.")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm deletion non-interactively.")] = False,
) -> None:
    """Delete a named credential from the system credential vault."""
    if not yes and not typer.confirm(f"Delete {ref}?"):
        raise typer.Abort()
    try:
        _vault().delete(ref)
    except CredentialError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"Deleted {ref}.")


@credentials_app.command("doctor")
def credential_doctor() -> None:
    """Report whether a secure operating-system credential backend is available."""
    report = _vault().diagnose()
    console.print_json(data=report)
    if not report["ok"]:
        raise typer.Exit(code=1)
