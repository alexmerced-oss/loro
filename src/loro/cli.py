from typing import Annotated

import typer
from rich.console import Console

from loro import __version__
from loro.config import load_config
from loro.memory.local import LocalMemoryStore
from loro.runtime import AgentRuntime

app = typer.Typer(
    name="loro",
    help="Enterprise agent harness for coding, governed data, and productivity work.",
    no_args_is_help=True,
    invoke_without_command=True,
)
memory_app = typer.Typer(help="Inspect and write Loro memories.")
docs_app = typer.Typer(help="Create and transform documents.")
slides_app = typer.Typer(help="Create and transform presentations.")
sheets_app = typer.Typer(help="Create and transform spreadsheets.")
brief_app = typer.Typer(help="Create enterprise briefs.")
data_app = typer.Typer(help="Discover governed enterprise data.")

app.add_typer(memory_app, name="memory")
app.add_typer(docs_app, name="docs")
app.add_typer(slides_app, name="slides")
app.add_typer(sheets_app, name="sheets")
app.add_typer(brief_app, name="brief")
app.add_typer(data_app, name="data")

console = Console()


def _runtime() -> AgentRuntime:
    return AgentRuntime(load_config())


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", help="Show Loro version.")] = False,
) -> None:
    if version:
        console.print(f"loro {__version__}")
        raise typer.Exit()


@app.command()
def run(prompt: Annotated[str, typer.Argument(help="Task prompt for Loro.")]) -> None:
    """Run a one-shot agent task."""
    result = _runtime().run(prompt, mode="run")
    console.print(result.summary)


@app.command()
def plan(prompt: Annotated[str, typer.Argument(help="Planning prompt for Loro.")]) -> None:
    """Run a read-only planning task."""
    result = _runtime().run(prompt, mode="plan")
    console.print(result.summary)


@app.command("config")
def show_config() -> None:
    """Show resolved configuration."""
    console.print_json(load_config().model_dump_json(indent=2))


@app.command()
def doctor() -> None:
    """Validate provider, permission, memory, Polaris, and artifact configuration."""
    config = load_config()
    console.print("[bold green]Loro doctor[/bold green]")
    console.print(f"Model provider: {config.model.provider}")
    console.print(f"Default permission: {config.permissions.default}")
    console.print(f"Local memory: {'enabled' if config.memory.local.enabled else 'disabled'}")
    console.print(f"Shared memory: {'enabled' if config.memory.shared.enabled else 'disabled'}")
    console.print(f"Polaris: {'enabled' if config.polaris.enabled else 'disabled'}")


@memory_app.command("list")
def memory_list() -> None:
    """List local memories."""
    store = LocalMemoryStore.from_config(load_config().memory.local)
    memories = store.list()
    if not memories:
        console.print("No local memories yet.")
        return
    for memory in memories:
        console.print(f"- [bold]{memory.memory_id}[/bold]: {memory.content}")


@memory_app.command("remember")
def remember_local(content: Annotated[str, typer.Argument(help="Memory content.")]) -> None:
    """Explicitly write a local memory."""
    store = LocalMemoryStore.from_config(load_config().memory.local)
    memory = store.remember(content)
    console.print(f"Saved local memory: {memory.memory_id}")


@app.command("remember")
def remember(
    content: Annotated[str, typer.Argument(help="Memory content.")],
    local: Annotated[bool, typer.Option("--local", help="Write local memory.")] = False,
    shared: Annotated[
        bool, typer.Option("--shared", help="Write shared enterprise memory.")
    ] = False,
) -> None:
    """Explicitly write a local or shared memory."""
    if shared:
        console.print(
            "[yellow]Shared memory backends are scaffolded but not implemented yet. "
            "The explicit write gate is reserved here.[/yellow]"
        )
        return
    if local or not shared:
        store = LocalMemoryStore.from_config(load_config().memory.local)
        memory = store.remember(content)
        console.print(f"Saved local memory: {memory.memory_id}")


@docs_app.command("create")
def docs_create(prompt: Annotated[str, typer.Argument(help="Document prompt.")]) -> None:
    result = _runtime().run(prompt, mode="document")
    console.print(result.summary)


@slides_app.command("create")
def slides_create(prompt: Annotated[str, typer.Argument(help="Presentation prompt.")]) -> None:
    result = _runtime().run(prompt, mode="presentation")
    console.print(result.summary)


@sheets_app.command("analyze")
def sheets_analyze(prompt: Annotated[str, typer.Argument(help="Spreadsheet prompt.")]) -> None:
    result = _runtime().run(prompt, mode="spreadsheet")
    console.print(result.summary)


@brief_app.command("meeting")
def brief_meeting(prompt: Annotated[str, typer.Argument(help="Meeting brief prompt.")]) -> None:
    result = _runtime().run(prompt, mode="briefing")
    console.print(result.summary)


@data_app.command("catalogs")
def data_catalogs() -> None:
    console.print("Polaris catalog discovery is scaffolded. Enable [polaris] to connect.")
