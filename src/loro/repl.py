from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeAlias

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from loro.config import LoroConfig

PARROT = r"""
       .--.
      / 6_6\
      \  = /
     .-`--'-.
    /  .--.  \
   /__/    \__\
      \    /
       `--'
""".strip("\n")


class ReplResult(Protocol):
    summary: str
    session_id: str
    stop_reason: str
    steps: int
    usage: dict[str, int | float]


TaskRunner: TypeAlias = Callable[[str, str | None, str | None], ReplResult]


def run_repl(
    config: LoroConfig,
    task_runner: TaskRunner,
    *,
    console: Console,
    session_id: str | None = None,
    agent_name: str | None = None,
) -> None:
    """Run a durable, folder-oriented interactive session."""

    _render_header(config, console, session_id=session_id, agent_name=agent_name)
    while True:
        try:
            prompt = typer.prompt("loro", prompt_suffix="> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nSession closed.")
            return
        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            console.print("Session closed.")
            return
        if prompt == "/help":
            console.print(
                "/status  session metadata    /new  new session    "
                "/resume ID  resume session    /agent NAME  select agent    /exit  close"
            )
            continue
        if prompt == "/status":
            _render_metadata(config, console, session_id=session_id, agent_name=agent_name)
            continue
        if prompt == "/new":
            session_id = None
            console.print("Started a new session context.")
            continue
        if prompt.startswith("/resume "):
            session_id = prompt.removeprefix("/resume ").strip() or None
            console.print(f"Session: {session_id or 'new'}")
            continue
        if prompt.startswith("/agent "):
            agent_name = prompt.removeprefix("/agent ").strip() or None
            session_id = None
            console.print(f"Agent: {agent_name or 'default'}; new session context.")
            continue
        if prompt.startswith("/"):
            console.print("Unknown REPL command. Use /help.")
            continue
        try:
            result = task_runner(prompt, session_id, agent_name)
        except (OSError, RuntimeError, PermissionError, ValueError) as error:
            console.print(f"Error: {error}")
            continue
        session_id = result.session_id
        console.print(result.summary)
        console.print(
            f"[dim]session {session_id} | {result.steps} step(s) | {result.stop_reason} | "
            f"tokens {int(result.usage.get('total_tokens', 0))}[/dim]"
        )


def _render_header(
    config: LoroConfig,
    console: Console,
    *,
    session_id: str | None,
    agent_name: str | None,
) -> None:
    console.print(Panel(PARROT, title=f"Loro parrot | {config.model.provider}", width=28))
    _render_metadata(config, console, session_id=session_id, agent_name=agent_name)


def _render_metadata(
    config: LoroConfig,
    console: Console,
    *,
    session_id: str | None,
    agent_name: str | None,
) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold")
    table.add_row("Folder", str(Path.cwd().resolve()))
    table.add_row("Provider", config.model.provider)
    table.add_row("Model", config.model.model)
    table.add_row("Agent", agent_name or "default")
    table.add_row("Session", session_id or "new")
    table.add_row("Memory", "on" if config.memory.local.enabled else "off")
    console.print(table)
