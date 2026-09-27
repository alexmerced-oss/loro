"""`loro file` and `loro shell`: permission-gated file and shell access."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from loro.cli._common import _audit, _authorize_cli_action, _permissions, console
from loro.config import (
    load_config,
)
from loro.permissions import PermissionRequest
from loro.resources import (
    filesystem_resource,
    shell_resource,
)
from loro.tools.files import FileTools
from loro.tools.shell import ShellTools

file_app = typer.Typer(help="Read and search local files.")


shell_app = typer.Typer(help="Run permission-gated shell commands.")


@file_app.command("read")
def file_read(
    path: Annotated[Path, typer.Argument(help="File path to read.")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum characters to print.")] = 20000,
) -> None:
    """Read a text file."""
    config = load_config()
    resource = filesystem_resource(
        path,
        operation="read",
        workspace_roots=config.permissions.workspace_roots,
    )
    path = Path(str(resource.fields["path"]))
    _permissions().require_allowed(
        PermissionRequest(
            tool="edit",
            action="read file",
            target=str(path),
            resource=resource,
        ),
        approved=True,
    )
    text = FileTools().read_text(path, limit=limit)
    _audit().write("file.read", path=str(path), limit=limit)
    console.print(text)


@file_app.command("search")
def file_search(
    query: Annotated[str, typer.Argument(help="Text to search for.")],
    root: Annotated[Path, typer.Option("--root", "-r", help="Directory to search.")] = Path("."),
    limit: Annotated[int, typer.Option("--limit", help="Maximum matches to print.")] = 50,
) -> None:
    """Search local text files."""
    config = load_config()
    resource = filesystem_resource(
        root,
        operation="search",
        workspace_roots=config.permissions.workspace_roots,
    )
    root = Path(str(resource.fields["path"]))
    _permissions().require_allowed(
        PermissionRequest(
            tool="edit",
            action="search files",
            target=str(root),
            resource=resource,
        ),
        approved=True,
    )
    matches = FileTools().search(root=root, query=query, limit=limit)
    _audit().write("file.search", query=query, root=str(root), match_count=len(matches))
    if not matches:
        console.print("No matches.")
        return
    for match in matches:
        console.print(f"{match.path}:{match.line_number}: {match.line}")


@shell_app.command("run")
def shell_run(
    args: Annotated[list[str], typer.Argument(help="Command and arguments to execute.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Approve ask-gated shell execution."),
    ] = False,
    timeout: Annotated[int, typer.Option("--timeout", help="Timeout in seconds.")] = 120,
) -> None:
    """Run a shell command without invoking a shell interpreter."""
    if not args:
        raise typer.BadParameter("Provide a command to execute.")
    resource = shell_resource(args)
    _authorize_cli_action(
        tool="shell",
        action="run command",
        target=args[0],
        arguments={"args": args, "timeout": timeout},
        risk_reason="Execute a subprocess with the displayed arguments.",
        non_interactive_approved=yes,
        resource=resource,
    )
    config = load_config()
    result = ShellTools(
        config.sandbox,
        workspace_roots=config.permissions.workspace_roots,
    ).run(args, timeout=timeout)
    _audit().write(
        "shell.executed",
        args=result.args,
        returncode=result.returncode,
        timeout=timeout,
        sandbox_profile=result.profile,
        sandbox_os_enforced=result.os_enforced,
        output_truncated=result.output_truncated,
    )
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)
    raise typer.Exit(code=result.returncode)
