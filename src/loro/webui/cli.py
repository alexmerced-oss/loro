from __future__ import annotations

import importlib.util
import ipaddress
import os
import secrets
import threading
import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

web_app = typer.Typer(
    help="Run Loro's optional local Web UI.",
    invoke_without_command=True,
    no_args_is_help=False,
)
console = Console()


@web_app.callback()
def serve(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Address to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port to bind.")] = 8765,
    no_open: Annotated[
        bool, typer.Option("--no-open", help="Do not open a browser.")
    ] = False,
    database: Annotated[
        Path | None, typer.Option(help="Override the Web UI SQLite database path.")
    ] = None,
    auth_token_env: Annotated[
        str | None,
        typer.Option(help="Environment variable containing a bearer token for non-loopback use."),
    ] = None,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise typer.BadParameter("--host must be an explicit IP address.") from error
    auth_token = os.environ.get(auth_token_env, "") if auth_token_env else None
    if not address.is_loopback and not auth_token:
        raise typer.BadParameter(
            "Non-loopback Web UI binding requires --auth-token-env with a non-empty token."
        )
    # A loopback bind used to be unauthenticated. Origin and CSRF checks stop a
    # hostile web page, but any other local process or user on a shared machine
    # could read the API directly. Mint a per-launch token so the browser is
    # admitted by the URL and nothing else is.
    minted_token = False
    if not auth_token:
        auth_token = secrets.token_urlsafe(32)
        minted_token = True
    try:
        import uvicorn
    except ImportError as error:
        raise typer.BadParameter(
            'Install Web UI support with `pip install "loro-agent[webui]"`.'
        ) from error
    from loro.webui.server import create_app

    url = f"http://{host}:{port}"
    launch_url = f"{url}/?token={auth_token}" if minted_token else url
    if not no_open:
        threading.Timer(0.7, lambda: webbrowser.open(launch_url)).start()
    # soft_wrap keeps the token on one line; a wrapped URL cannot be copied.
    console.print(f"Loro Web UI: {launch_url}", soft_wrap=True, highlight=False)
    uvicorn.run(
        create_app(
            project_root=Path.cwd(), database_path=database, auth_token=auth_token
        ),
        host=host,
        port=port,
        access_log=False,
    )


@web_app.command("doctor")
def doctor() -> None:
    missing = [name for name in ("fastapi", "uvicorn") if importlib.util.find_spec(name) is None]
    if missing:
        console.print(
            "Web UI dependencies missing: " + ", ".join(missing) + ". Install loro-agent[webui]."
        )
        raise typer.Exit(code=1)
    from loro.webui.conversations import ConversationStore
    from loro.webui.services import default_database_path

    path = default_database_path(Path.cwd())
    ConversationStore(path)
    console.print(f"Web UI ready. Database: {path}")


__all__ = ["web_app"]
