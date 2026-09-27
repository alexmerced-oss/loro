"""`loro plugins`: installed plugins, enabled hooks, and what they contribute."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.table import Table

from loro.cli._common import console
from loro.config import load_config

plugins_app = typer.Typer(help="Inspect plugins and tool hooks.", no_args_is_help=True)


@plugins_app.command("list")
def plugins_list(
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List installed plugins, whether they are enabled, and configured command hooks.

    Example: loro plugins list --json

    Installing a package never enables it: add its name to [plugins] enabled.
    """

    from loro.plugins import discover

    config = load_config().plugins
    installed = [item.to_payload() for item in discover(config)]
    hooks = [hook.model_dump() for hook in config.hooks]
    if json_output:
        typer.echo(json.dumps({"plugins": installed, "hooks": hooks}, indent=2))
        return
    if not installed and not hooks:
        console.print(
            "No plugins installed and no hooks configured. Plugins register a "
            "'loro.plugins' entry point; hooks go in [[plugins.hooks]].",
            markup=False,
        )
        return
    if installed:
        table = Table(title="Plugins", box=None, pad_edge=False, header_style="bold")
        for column in ("name", "status", "version", "tools", "hooks"):
            table.add_column(column, overflow="fold")
        for item in installed:
            status = (
                f"error: {item['error']}"
                if item["error"]
                else ("enabled" if item["enabled"] else "installed, not enabled")
            )
            table.add_row(
                item["name"],
                status,
                item["version"] or "",
                ", ".join(item["tools"]) or "-",
                f"pre {item['hooks']['pre_tool']} / post {item['hooks']['post_tool']}",
            )
        console.print(table)
    if hooks:
        table = Table(title="Command hooks", box=None, pad_edge=False, header_style="bold")
        for column in ("name", "event", "match", "command"):
            table.add_column(column, overflow="fold")
        for hook in hooks:
            table.add_row(
                hook["name"], hook["event"], ", ".join(hook["match"]), " ".join(hook["command"])
            )
        console.print(table)


@plugins_app.command("doctor")
def plugins_doctor() -> None:
    """Exit 1 if an enabled plugin failed to load or a hook's sandbox profile is missing."""

    from loro.plugins import discover

    config = load_config()
    problems = [
        f"plugin {item.name}: {item.error}"
        for item in discover(config.plugins)
        if item.enabled and item.error
    ]
    for hook in config.plugins.hooks:
        profile = hook.sandbox_profile or config.sandbox.hook_profile
        if profile not in config.sandbox.profiles:
            problems.append(f"hook {hook.name}: sandbox profile {profile!r} does not exist")
        elif "LORO_HOOK_EVENT" not in config.sandbox.profiles[profile].environment_allowlist:
            problems.append(f"hook {hook.name}: profile {profile!r} must allow LORO_HOOK_EVENT")
    if problems:
        for problem in problems:
            console.print(f"[bold red]Problem:[/bold red] {problem}", highlight=False)
        raise typer.Exit(code=1)
    console.print("Plugins and hooks OK.")
