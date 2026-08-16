import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, TypeAlias

import typer
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

from loro.config import LoroConfig

PARROT = r"""
        __
      /`  `\
     / 6  6 \__
     \   >  /  `-.
      `-v--'      )
       /  .----.-'
      /  /    / /
     /__/    /_/
       /\    /\
""".strip("\n")

WIDE_REPL_PANEL = 68
MAX_REPL_PANEL_WIDTH = 96


class ReplResult(Protocol):
    summary: str
    response: str
    session_id: str
    stop_reason: str
    steps: int
    usage: dict[str, int | float]


TokenHandler: TypeAlias = Callable[[str], None]
EventHandler: TypeAlias = Callable[[str, Mapping[str, Any]], None]
TaskRunner: TypeAlias = Callable[
    [str, str | None, str | None, TokenHandler, EventHandler], ReplResult
]


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
            _render_header(config, console, session_id=session_id, agent_name=agent_name)
            continue
        if prompt == "/new":
            session_id = None
            _render_header(config, console, session_id=session_id, agent_name=agent_name)
            continue
        if prompt.startswith("/resume "):
            session_id = prompt.removeprefix("/resume ").strip() or None
            _render_header(config, console, session_id=session_id, agent_name=agent_name)
            continue
        if prompt.startswith("/agent "):
            agent_name = prompt.removeprefix("/agent ").strip() or None
            session_id = None
            _render_header(config, console, session_id=session_id, agent_name=agent_name)
            continue
        if prompt.startswith("/"):
            console.print("Unknown REPL command. Use /help.")
            continue
        renderer = ReplTurnRenderer(console)
        try:
            result = task_runner(
                prompt,
                session_id,
                agent_name,
                renderer.on_token,
                renderer.on_event,
            )
        except (OSError, RuntimeError, PermissionError, ValueError) as error:
            renderer.stop()
            console.print(f"Error: {error}")
            continue
        session_id = result.session_id
        renderer.finish(result)


class ReplTurnRenderer:
    """Render one streamed model turn and its tool activity."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.status: Status | None = None
        self.model_has_tokens = False
        self.streamed_tokens = False
        self.last_chunk_had_newline = True

    def on_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if event_type == "model.started":
            self._start_status(f"Thinking · step {int(payload.get('step', 1))}")
            self.model_has_tokens = False
            return
        if event_type == "model.completed":
            if self.model_has_tokens:
                self._end_stream_line()
            return
        if event_type == "tool.started":
            self.stop()
            self._end_stream_line()
            tool = str(payload.get("tool", "unknown"))
            detail = _tool_detail(payload.get("args"))
            suffix = f" [dim]{detail}[/dim]" if detail else ""
            self.console.print(f"[bold cyan]tool[/bold cyan] {tool}{suffix}")
            return
        if event_type == "tool.completed":
            tool = str(payload.get("tool", "unknown"))
            ok = bool(payload.get("ok"))
            label = "done" if ok else "failed"
            style = "bold green" if ok else "bold red"
            latency = float(payload.get("latency_ms", 0))
            self.console.print(f"[{style}]{label}[/{style}] {tool} [dim]{latency:.0f} ms[/dim]")

    def on_token(self, chunk: str) -> None:
        if not chunk:
            return
        self.stop()
        if not self.model_has_tokens:
            self.console.print("[bold bright_green]assistant[/bold bright_green]")
        self.console.print(chunk, end="", markup=False, soft_wrap=True)
        self.model_has_tokens = True
        self.streamed_tokens = True
        self.last_chunk_had_newline = chunk.endswith("\n")

    def finish(self, result: ReplResult) -> None:
        self.stop()
        self._end_stream_line()
        if not self.streamed_tokens and result.response:
            self.console.print("[bold bright_green]assistant[/bold bright_green]")
            self.console.print(result.response, markup=False)

        steps = f"{result.steps} step" + ("s" if result.steps != 1 else "")
        tool_count = int(result.usage.get("tool_calls", 0))
        tools = f"{tool_count} tool" + ("s" if tool_count != 1 else "")
        input_tokens = int(result.usage.get("input_tokens", 0))
        output_tokens = int(result.usage.get("output_tokens", 0))
        total_tokens = input_tokens + output_tokens
        details = [steps, tools]
        if total_tokens:
            details.append(f"{total_tokens} tokens")
        if result.stop_reason != "completed":
            details.append(result.stop_reason)
        details.append(f"session {result.session_id[:8]}")
        self.console.print("[dim]" + " · ".join(details) + "[/dim]")

    def stop(self) -> None:
        if self.status is not None:
            self.status.stop()
            self.status = None

    def _start_status(self, message: str) -> None:
        self.stop()
        self.status = self.console.status(f"[dim]{message}[/dim]", spinner="dots")
        self.status.start()

    def _end_stream_line(self) -> None:
        if self.model_has_tokens and not self.last_chunk_had_newline:
            self.console.print()
        self.last_chunk_had_newline = True


def _tool_detail(raw_args: object) -> str:
    if not isinstance(raw_args, Mapping):
        return ""
    safe_keys = ("path", "query", "cwd", "server_id", "name", "command", "args")
    details: list[str] = []
    for key in safe_keys:
        if key not in raw_args:
            continue
        value = raw_args[key]
        if isinstance(value, str):
            rendered = value
        elif isinstance(value, list):
            rendered = " ".join(str(item) for item in value[:6])
        else:
            rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
        rendered = " ".join(rendered.split())
        if len(rendered) > 60:
            rendered = rendered[:57] + "..."
        details.append(f"{key}={rendered}")
        if len(details) == 2:
            break
    return "  ".join(details)


def _render_header(
    config: LoroConfig,
    console: Console,
    *,
    session_id: str | None,
    agent_name: str | None,
) -> None:
    metadata = _metadata_table(config, session_id=session_id, agent_name=agent_name)
    parrot = Text(PARROT, style="bold bright_green", overflow="crop")
    if console.width >= WIDE_REPL_PANEL:
        body = Table.grid(expand=True, padding=(0, 2))
        body.add_column(width=19, no_wrap=True)
        body.add_column(ratio=1)
        body.add_row(Align.center(parrot, vertical="middle"), metadata)
    else:
        body = Group(Align.center(parrot), metadata)

    console.print(
        Panel(
            body,
            title="[bold cyan]Loro[/bold cyan] [dim]| interactive workspace[/dim]",
            subtitle=_panel_subtitle(session_id),
            border_style="cyan",
            padding=(0, 1),
            width=min(console.width, MAX_REPL_PANEL_WIDTH),
        )
    )


def _metadata_table(
    config: LoroConfig,
    *,
    session_id: str | None,
    agent_name: str | None,
) -> Table:
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(width=9, style="bold cyan", no_wrap=True)
    table.add_column(ratio=1, overflow="fold")
    table.add_row("Status", _status_markers(config, session_id=session_id))
    table.add_row("Folder", str(Path.cwd().resolve()))
    table.add_row("Provider", config.model.provider)
    table.add_row("Model", config.model.model)
    table.add_row("Agent", agent_name or "default")
    table.add_row("Session", session_id or "new")
    return table


def _status_markers(config: LoroConfig, *, session_id: str | None) -> Text:
    markers = Text()
    _append_marker(markers, "SESSION", "ACTIVE" if session_id else "NEW", bool(session_id))

    local_memory = config.memory.local.enabled
    shared_memory = config.memory.shared.enabled
    if local_memory and shared_memory:
        memory_state = f"LOCAL+{config.memory.shared.backend.upper()}"
    elif local_memory:
        memory_state = "LOCAL"
    elif shared_memory:
        memory_state = config.memory.shared.backend.upper()
    else:
        memory_state = "OFF"
    _append_marker(markers, "MEM", memory_state, local_memory or shared_memory)
    _append_marker(
        markers,
        "SANDBOX",
        "ON" if config.sandbox.enabled else "OFF",
        config.sandbox.enabled,
    )
    _append_marker(
        markers,
        "AUDIT",
        config.audit.sink.upper() if config.audit.enabled else "OFF",
        config.audit.enabled,
    )
    return markers


def _append_marker(markers: Text, label: str, value: str, enabled: bool) -> None:
    if markers:
        markers.append("  ", style="dim")
    markers.append(f"{label} ", style="dim")
    markers.append(value, style="bold green" if enabled else "bold yellow")


def _panel_subtitle(session_id: str | None) -> Text:
    subtitle = Text(" READY ", style="bold green")
    if session_id:
        subtitle.append(" session active ", style="dim")
    else:
        subtitle.append(" new session ", style="dim")
    return subtitle
