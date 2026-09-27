"""Top-level commands: run, plan, repl, configure, remember, create, and the guides."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from loro import __version__
from loro.artifacts.documents import create_document_artifact
from loro.artifacts.presentations import create_presentation_artifact
from loro.artifacts.spreadsheets import create_spreadsheet_artifact
from loro.audit import AuditDeliveryError, prompt_preview
from loro.cli._apps import app
from loro.cli._common import (
    DEFAULT_ARTIFACT_DIR,
    _audit,
    _create_and_print_artifact,
    _create_and_print_brief,
    _enforce_safe_content,
    _identity,
    _run_task,
    _shared_draft_store,
    _shared_memory_tenant,
    console,
)
from loro.cli.ops import (
    doctor as ops_doctor,
)
from loro.cli.runs import RunCommand
from loro.config import (
    LoroConfig,
    load_config,
    write_config_sections,
)
from loro.fileio import atomic_write_bytes
from loro.memory.local import LocalMemoryStore
from loro.memory.operations import (
    create_shared_memory_draft,
)
from loro.provider_profiles import ProviderProfile
from loro.providers import (
    ModelDiscoveryError,
    discover_provider_models,
    get_provider_profile,
    model_config_from_profile,
    provider_names,
)
from loro.repl import run_repl

app.command("doctor")(ops_doctor)


error_console = Console(stderr=True)


def _select_option(label: str, choices: list[tuple[str, str]], *, default: str) -> str:
    table = Table(title=label, show_lines=False)
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Choice", style="bold")
    for index, (_, display) in enumerate(choices, start=1):
        table.add_row(str(index), display)
    console.print(table)
    default_index = next(
        (index for index, (value, _) in enumerate(choices, start=1) if value == default), 1
    )
    while True:
        selected = typer.prompt(f"Select {label.lower()}", default=default_index)
        try:
            return choices[int(selected) - 1][0]
        except (ValueError, IndexError):
            console.print(f"Choose a number from 1 to {len(choices)}.")


def _select_model(
    label: str,
    profile: ProviderProfile,
    *,
    default: str,
    models: tuple[str, ...] | None = None,
) -> str:
    catalog = models or profile.model_choices
    filtered = catalog
    page = 0
    page_size = 30
    custom_value = "__custom__"
    search_value = "__search__"
    next_value = "__next__"
    previous_value = "__previous__"

    while True:
        page_count = max(1, (len(filtered) + page_size - 1) // page_size)
        page = min(page, page_count - 1)
        start = page * page_size
        visible = filtered[start : start + page_size]
        choices = [(name, name) for name in visible]
        if page > 0:
            choices.append((previous_value, "Previous page"))
        if page + 1 < page_count:
            choices.append((next_value, "Next page"))
        choices.extend(
            [
                (search_value, "Search models..."),
                (custom_value, "Custom model name..."),
            ]
        )
        title = label
        if len(filtered) > page_size:
            title = f"{label} ({start + 1}-{start + len(visible)} of {len(filtered)})"
        selected = _select_option(title, choices, default=default)
        if selected == custom_value:
            return typer.prompt(label, default=default)
        if selected == search_value:
            query = typer.prompt("Search model names").strip().lower()
            matches = tuple(name for name in catalog if query in name.lower())
            if not matches:
                console.print(f"No models matched {query!r}.")
                continue
            filtered = matches
            page = 0
            continue
        if selected == next_value:
            page += 1
            continue
        if selected == previous_value:
            page -= 1
            continue
        return selected


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show Loro version.")] = False,
) -> None:
    if version:
        console.print(f"loro {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        if not os.isatty(0):
            console.print(ctx.get_help())
            raise typer.Exit()
        _launch_repl()


_GET_STARTED_TOPICS: dict[str, tuple[str, tuple[str, ...]]] = {
    "setup": (
        "Set up this folder",
        (
            "Run `loro configure` to select a provider and dynamically discover models.",
            "Run `loro setup profile` to create a named assistant with explicit tools "
            "and permissions.",
            "Use `loro setup quickstart` for the full identity, approvals, audit, memory, "
            "data, and MCP sequence.",
            "Finish with `loro config check --strict` and `loro doctor`.",
        ),
    ),
    "profiles": (
        "Profiles and permissions",
        (
            "Profiles narrow configured authority; they cannot add an unconfigured model, "
            "tool, path, or credential.",
            "Create one with `loro setup profile`, inspect it with `loro agents explain "
            "NAME`, and switch in the REPL with `/agent NAME`.",
            "Tool availability and permission decisions are separate. `ask` requires "
            "trusted approval; `deny` always wins.",
            "Web retrieval needs `shell.run`, a non-denied web permission, and approved "
            "curl/network sandbox settings.",
        ),
    ),
    "repl": (
        "Daily REPL workflow",
        (
            "Run plain `loro` inside the folder you want to work on.",
            "Use `/status`, `/new`, `/resume ID`, `/agent NAME`, `/help`, and `/exit` "
            "to control the session.",
            "Loro streams assistant text and reports tool starts, completions, approvals, "
            "usage, and the durable session ID.",
            'Use `loro run "TASK"` for one-shot execution and `loro plan "GOAL"` for '
            "read-only planning.",
        ),
    ),
    "prompts": (
        "Prompts that work well",
        (
            "State the outcome, relevant folder or files, constraints, and how completion "
            "should be verified.",
            "Name required sources or ask for web research only when the profile has "
            "governed web retrieval.",
            "For consequential changes, ask Loro to inspect first, implement, run tests, "
            "and summarize evidence.",
            'Example: `loro run "Inspect this project, fix the failing tests without '
            'changing public behavior, run the focused suite, and report changed files."`',
        ),
    ),
    "artifacts": (
        "Documents and productivity artifacts",
        (
            "Use `loro docs create`, `slides create`, `sheets create`, or `brief meeting` "
            "with a substantive authoring brief.",
            "AI drafting is the default. Loro validates the model draft before rendering "
            "and writes provenance sidecars.",
            "Use `--no-ai` only when you explicitly want an offline scaffold.",
            "Verify a result with `loro artifacts verify PATH.provenance.json`.",
        ),
    ),
    "graphs": (
        "Multi-step Agentic Graphs",
        (
            'Generate with `loro graph generate "GOAL" --out workflow.agraph.yaml`.',
            "Review with `loro graph validate workflow.agraph.yaml --strict` and `loro "
            "graph plan workflow.agraph.yaml --json`.",
            "Execute with `loro graph run workflow.agraph.yaml`; Loro asks you to approve "
            "the exact digest.",
            "Inspect durable execution using `loro graph status RUN_ID` and resume eligible "
            "runs with `loro graph resume RUN_ID`.",
        ),
    ),
    "memory": (
        "Memory and durable sessions",
        (
            "Use local memory for preferences that should remain private to this installation: "
            '`loro remember --local "..."`.',
            "Shared memory is explicit and governed: propose, review drafts, then commit "
            "deliberately.",
            "Use `loro sessions list` and `loro sessions show ID` to inspect durable task history.",
            "Do not store credentials, tokens, or restricted data in prompts or memory.",
        ),
    ),
    "tools": (
        "Tools, MCP, and Skills",
        (
            "Models request typed file, Git, shell, memory, governed-data, artifact, MCP, "
            "Skill, and subagent tools.",
            "Write-like and process actions remain policy- and approval-gated; "
            "model-provided approval is never trusted.",
            "Configure MCP with `loro setup mcp`, then inspect with `loro mcp doctor` and "
            "`loro mcp tools SERVER`.",
            "Discover Skills with `loro skills list`; validate and review digests before "
            "installation or enabling scripts.",
        ),
    ),
    "governance": (
        "Safety and troubleshooting",
        (
            "Keep credentials in environment variables or the credential vault, never "
            "project TOML or prompts.",
            "Use `loro policy explain JSON` to understand a denial and `loro agents explain "
            "NAME` to see profile narrowing.",
            "Run `loro doctor`, `loro config check --strict`, `loro identity doctor`, "
            "`loro sandbox doctor`, and `loro audit doctor` when diagnosing setup.",
            "Review approvals, tool activity, generated files, graph digests, and provenance "
            "before consequential use.",
        ),
    ),
}


@app.command("capabilities")
def capabilities_cmd(json_output: bool = typer.Option(True, "--json/--no-json")) -> None:
    """Inspect installed runtime capabilities without running a model or opening a site."""
    from loro.capability_readiness import capability_report

    report = capability_report()
    if json_output:
        console.print_json(data=report)
    else:
        console.print(report)


@app.command("get-started")
def get_started(
    topic: Annotated[
        str | None,
        typer.Option(
            "--topic",
            "-t",
            help=(
                "Focused guide: setup, profiles, repl, prompts, artifacts, graphs, memory, "
                "tools, or governance."
            ),
        ),
    ] = None,
) -> None:
    """Show a context-aware guide to setting up and working effectively with Loro."""
    selected = topic.strip().casefold() if topic else None
    if selected is not None and selected not in _GET_STARTED_TOPICS:
        choices = ", ".join(_GET_STARTED_TOPICS)
        raise typer.BadParameter(f"Unknown topic {topic!r}. Choose one of: {choices}.")

    config = load_config()
    console.print(
        Panel(
            "Loro is a folder-oriented AI harness: configure a model, choose an agent profile, "
            "work in the REPL or a focused command, and let policy govern every tool action.",
            title="Loro | getting started",
            border_style="cyan",
        )
    )
    if selected is not None:
        _print_get_started_topic(selected)
        console.print("\n[dim]Return to the complete guide with `loro get-started`.[/dim]")
        return

    _print_get_started_readiness(config)
    journey = Table(title="Recommended first journey", show_lines=False)
    journey.add_column("Step", justify="right", style="cyan", no_wrap=True)
    journey.add_column("Command", style="bold")
    journey.add_column("Purpose")
    steps = [
        ("1", "loro configure", "Select a provider and discovered primary/small models."),
        (
            "2",
            "loro setup profile",
            "Choose instructions, model route, tools, memory, and permissions.",
        ),
        (
            "3",
            "loro doctor",
            "Check configuration, identity, provider, sandbox, audit, and storage.",
        ),
        ("4", "loro", "Open the streaming folder REPL and start a durable session."),
        ("5", 'loro plan "GOAL"', "Preview consequential work before running it."),
        ("6", 'loro run "TASK"', "Execute a bounded one-shot task with visible tool activity."),
    ]
    for row in steps:
        journey.add_row(*row)
    console.print(journey)

    topics = Table(title="Learn the workflows", show_lines=False)
    topics.add_column("Topic", style="cyan", no_wrap=True)
    topics.add_column("What it covers")
    topics.add_column("Open")
    for name, (title, _items) in _GET_STARTED_TOPICS.items():
        topics.add_row(name, title, f"loro get-started --topic {name}")
    console.print(topics)
    console.print(
        Panel(
            "Best default: work from the target folder, use the smallest capable profile, "
            "describe the desired outcome and verification, review approval prompts, and keep "
            "secrets out of prompts and project configuration.",
            title="Working well with Loro",
            border_style="green",
        )
    )


def _print_get_started_readiness(config: LoroConfig) -> None:
    table = Table(title=f"This folder | {Path.cwd()}", show_lines=False)
    table.add_column("Area", style="bold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Next action")
    local_config = Path(".loro/config.local.toml").is_file()
    rows = [
        (
            "Project config",
            "ready" if local_config else "not found",
            "Review `loro config summary`" if local_config else "Run `loro configure`",
        ),
        (
            "Model",
            (
                f"{config.model.provider}/{config.model.model}"
                if config.model.provider != "mock"
                else "mock (offline)"
            ),
            "Ready for model work" if config.model.provider != "mock" else "Run `loro configure`",
        ),
        (
            "Default profile",
            config.agent_profiles.default_profile or "none",
            (
                "Inspect with `loro agents explain NAME`"
                if config.agent_profiles.default_profile
                else "Run `loro setup profile`"
            ),
        ),
        (
            "Workspace",
            ", ".join(config.permissions.workspace_roots) or "not restricted",
            "Keep tool roots scoped to the project",
        ),
        (
            "Web retrieval",
            config.permissions.web,
            "Use the profile wizard's web preset when needed",
        ),
        (
            "Memory",
            "local on" if config.memory.local.enabled else "local off",
            "Optional: `loro setup memory`",
        ),
        (
            "MCP / Skills",
            f"{len(config.mcp.servers)} servers / {config.skills.max_active} max active skills",
            "Optional: `loro setup mcp` and `loro skills list`",
        ),
        (
            "Safety",
            f"sandbox {'on' if config.sandbox.enabled else 'off'} / "
            f"audit {'on' if config.audit.enabled else 'off'}",
            "Run `loro doctor`",
        ),
    ]
    for area, status, action in rows:
        table.add_row(area, status, action)
    console.print(table)


def _print_get_started_topic(topic: str) -> None:
    title, items = _GET_STARTED_TOPICS[topic]
    console.print(f"\n[bold]{title}[/bold]")
    for index, item in enumerate(items, start=1):
        console.print(f"{index}. {item}")


def _launch_repl(*, session_id: str | None = None, agent_name: str | None = None) -> None:
    config = load_config()
    selected_agent = agent_name or config.agent_profiles.default_profile
    run_repl(
        config,
        lambda prompt, active_session, active_agent, on_token, on_event: _run_task(
            prompt,
            mode="run",
            session_id=active_session,
            stream=True,
            agent_name=active_agent,
            on_token=on_token,
            on_event=on_event,
        ),
        console=console,
        session_id=session_id,
        agent_name=selected_agent,
    )


@app.command()
def repl(
    resume_session: Annotated[
        str | None, typer.Option("--resume-session", help="Resume a saved session.")
    ] = None,
    agent: Annotated[str | None, typer.Option("--agent", help="Use a named agent profile.")] = None,
) -> None:
    """Open Loro's interactive folder session."""
    _launch_repl(session_id=resume_session, agent_name=agent)


@app.command(cls=RunCommand)
def run(
    prompt: Annotated[
        str | None,
        typer.Argument(
            help="Task prompt for Loro. Omit when using --prompt-file.", show_default=False
        ),
    ] = None,
    prompt_file: Annotated[
        Path | None,
        typer.Option(
            "--prompt-file",
            help=(
                "Read the task prompt from a UTF-8 file, for prompts too large for the command "
                "line. '-' is not accepted: stdin is reserved for --approval-stdio decisions."
            ),
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print one JSON object with the result instead of a report."),
    ] = False,
    resume_session: Annotated[
        str | None,
        typer.Option("--resume-session", help="Resume a saved session and deliver its inbox."),
    ] = None,
    stream: Annotated[
        bool, typer.Option("--stream", help="Render model output token by token as it arrives.")
    ] = False,
    agent: Annotated[
        str | None, typer.Option("--agent", help="Run from a named Open Agent Profile.")
    ] = None,
    approval_stdio: Annotated[
        bool,
        typer.Option(
            "--approval-stdio",
            help="Exchange AAIS 1.0 approval envelopes as NDJSON on stdout/stdin.",
        ),
    ] = False,
) -> None:
    """Run an agent task, optionally resuming a durable session.

    Examples: loro run "Summarize the README" ; loro run --prompt-file task.md --json ;
    loro run --resume-session ID "Continue."

    Exit codes: 0 when the run finished (including budget or step limits), 1 when the provider
    failed or policy blocked the task, 2 for usage errors.

    Evidence: loro run list ; loro run export RUN_ID --out run.zip ; loro run verify run.zip.
    Use `loro run -- export` for a task whose whole prompt is one of those words.
    """
    if json_output and stream:
        raise typer.BadParameter("--json cannot be combined with --stream.")
    task = _task_prompt(prompt, prompt_file)
    try:
        provider = None
        if approval_stdio:
            from loro.aais_stdio import create_stdio_provider

            provider = create_stdio_provider(Path.cwd())

        result = _run_task(
            task,
            mode="run",
            session_id=resume_session,
            stream=stream,
            agent_name=agent,
            approval_provider=provider,
        )
    except FileNotFoundError as error:
        raise typer.BadParameter(str(error)) from error
    except ValueError as error:
        # Data-protection blocks and profile/session mismatches are policy outcomes, not bugs.
        error_console.print(f"[bold red]Error:[/bold red] {error}", highlight=False)
        raise typer.Exit(code=1) from error
    failed = result.stop_reason == "provider_error"
    if json_output:
        typer.echo(json.dumps(_run_payload(result), default=str))
    else:
        console.print(result.summary)
        if result.run_id:
            console.print(
                f"\nRun {result.run_id} (export: loro run export {result.run_id} --out run.zip)",
                style="dim",
                highlight=False,
                soft_wrap=True,
            )
        if failed:
            error_console.print(
                "The provider request failed. Check the key and endpoint with "
                "`loro providers smoke --execute` and `loro doctor`.",
                highlight=False,
                soft_wrap=True,
            )
    if failed:
        raise typer.Exit(code=1)


def _task_prompt(prompt: str | None, prompt_file: Path | None) -> str:
    """The task text from the argument or --prompt-file, with actionable usage errors."""

    if prompt is not None and prompt_file is not None:
        raise typer.BadParameter("Pass either a PROMPT or --prompt-file, not both.")
    if prompt_file is None:
        if prompt is None or not prompt.strip():
            raise typer.BadParameter(
                'Missing task prompt. Example: loro run "Summarize the README" '
                "or loro run --prompt-file task.md"
            )
        return prompt
    if str(prompt_file) == "-":
        raise typer.BadParameter(
            "--prompt-file - is not supported: stdin is reserved for --approval-stdio "
            "decisions. Write the prompt to a file and pass its path."
        )
    path = prompt_file.expanduser()
    if not path.is_file():
        raise typer.BadParameter(f"Prompt file not found: {path}")
    limit = load_config().runtime.max_model_input_bytes
    size = path.stat().st_size
    if size > limit:
        raise typer.BadParameter(
            f"Prompt file is {size} bytes; the model input limit is {limit} bytes "
            "(runtime.max_model_input_bytes)."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise typer.BadParameter(f"Prompt file is not UTF-8 text: {path}") from error
    if not text.strip():
        raise typer.BadParameter(f"Prompt file is empty: {path}")
    return text


def _run_payload(result: Any) -> dict[str, Any]:
    return {
        "ok": result.stop_reason != "provider_error",
        "run_id": result.run_id,
        "session_id": result.session_id,
        "mode": result.mode,
        "provider": result.provider,
        "model": result.model,
        "stop_reason": result.stop_reason,
        "steps": result.steps,
        "response": result.response,
        "usage": result.usage,
        "context": result.context,
        "tool_calls": [
            {"tool": execution.call.name, "ok": execution.ok}
            for execution in result.tool_executions
        ],
    }


@app.command()
def plan(
    prompt: Annotated[str, typer.Argument(help="Planning prompt for Loro.")],
    resume_session: Annotated[
        str | None,
        typer.Option("--resume-session", help="Resume a saved session and deliver its inbox."),
    ] = None,
    format: Annotated[
        str, typer.Option("--format", help="Output format: text or agraph.")
    ] = "text",
    out: Annotated[Path, typer.Option("--out", help="Path for --format agraph output.")] = Path(
        "generated.agraph.yaml"
    ),
    stream: Annotated[
        bool, typer.Option("--stream", help="Render model output token by token as it arrives.")
    ] = False,
    agent: Annotated[
        str | None, typer.Option("--agent", help="Plan from a named Open Agent Profile.")
    ] = None,
    no_ai: Annotated[
        bool,
        typer.Option(
            "--no-ai",
            help="With --format agraph, explicitly create an offline one-node skeleton.",
        ),
    ] = False,
) -> None:
    """Run a read-only planning task."""
    if format == "agraph":
        if agent is not None:
            raise typer.BadParameter("--agent is supported only with --format text.")
        from loro.agraph.generate import write_ai_generated_graph, write_generated_graph

        try:
            config = load_config()
            path = (
                write_generated_graph(prompt, out, config)
                if no_ai
                else write_ai_generated_graph(
                    prompt,
                    out,
                    config,
                    lambda request: (
                        _run_task(
                            request,
                            mode="plan",
                            session_id=None,
                            stream=False,
                            agent_name=None,
                        ).response
                    ),
                )
            )
            console.print(str(path))
        except ValueError as error:
            raise typer.BadParameter(str(error)) from error
        return
    if format != "text":
        raise typer.BadParameter("--format must be text or agraph")
    try:
        result = _run_task(
            prompt, mode="plan", session_id=resume_session, stream=stream, agent_name=agent
        )
    except FileNotFoundError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(result.summary)


@app.command()
def configure(
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name. Omit for interactive prompt."),
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Primary model name.")] = None,
    small_model: Annotated[
        str | None,
        typer.Option("--small-model", help="Small/fast model name."),
    ] = None,
    api_key_env: Annotated[
        str | None,
        typer.Option("--api-key-env", help="Environment variable containing API key."),
    ] = None,
    credential_ref: Annotated[
        str | None,
        typer.Option("--credential-ref", help="OS-keyring vault reference for the API key."),
    ] = None,
    base_url: Annotated[str | None, typer.Option("--base-url", help="Provider base URL.")] = None,
    model_discovery: Annotated[
        bool,
        typer.Option(
            "--discover-models/--no-discover-models",
            help="Load the provider's current model catalog during interactive setup.",
        ),
    ] = True,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Config file to write."),
    ] = Path(".loro/config.local.toml"),
) -> None:
    """Create a local provider configuration."""
    chosen_provider = provider
    interactive = chosen_provider is None
    if chosen_provider is None:
        choices = [
            (name, f"{get_provider_profile(name).display_name} ({name})")
            for name in provider_names()
        ]
        chosen_provider = _select_option("Provider", choices, default="mock")
    profile = get_provider_profile(chosen_provider)
    discovered_models: tuple[str, ...] | None = None
    if interactive and model_discovery:
        try:
            with console.status(f"Loading {profile.display_name} models..."):
                catalog = discover_provider_models(
                    profile.name,
                    api_key_env=api_key_env,
                    credential_ref=credential_ref,
                    base_url=base_url,
                )
        except ModelDiscoveryError as error:
            console.print(
                f"[yellow]Live model discovery unavailable: {error} Using bundled choices.[/yellow]"
            )
        else:
            discovered_models = catalog.models
            console.print(
                f"Loaded {len(catalog.models)} models from {catalog.source}.",
                markup=False,
            )
    chosen_model = model or (
        _select_model(
            "Primary model",
            profile,
            default=profile.default_model,
            models=discovered_models,
        )
        if interactive
        else profile.default_model
    )
    chosen_small = small_model or (
        _select_model(
            "Small model",
            profile,
            default=profile.small_model,
            models=discovered_models,
        )
        if interactive
        else profile.small_model
    )
    chosen_key_env = api_key_env
    if chosen_key_env is None:
        chosen_key_env = (
            typer.prompt("API key env var", default=profile.api_key_env)
            if profile.api_key_env and interactive
            else profile.api_key_env
        )
    chosen_base_url = base_url
    if chosen_base_url is None:
        chosen_base_url = (
            typer.prompt("Base URL", default=profile.base_url)
            if profile.base_url and interactive
            else profile.base_url
        )

    config = load_config()
    config.model = model_config_from_profile(
        profile.name,
        model=chosen_model,
        small_model=chosen_small,
        api_key_env=chosen_key_env,
        credential_ref=credential_ref,
        base_url=chosen_base_url,
    )
    new_local_profile = not output.exists()
    profile_config = _strict_local_profile(config) if new_local_profile else config
    written = _write_audited_provider_config(
        output,
        profile_config,
        include_local_profile=new_local_profile,
    )
    console.print(f"Wrote provider config: {written}")


def _strict_local_profile(config: LoroConfig) -> LoroConfig:
    local = LoroConfig()
    local.model = config.model.model_copy(deep=True)
    local.permissions.workspace_roots = [str(Path.cwd().resolve())]
    local.sandbox.profiles["controlled-shell"].allowed_executables = [
        "bash",
        "git",
        "node",
        "npm",
        "npx",
        "python",
        "python3",
        "pytest",
        "rg",
        "ruff",
        "sh",
        "uv",
    ]
    local.sandbox.profiles["mcp-stdio"].allowed_executables = [
        "node",
        "npx",
        "python",
        "python3",
        "uvx",
    ]
    return local


def _write_audited_provider_config(
    output: Path,
    config: LoroConfig,
    *,
    include_local_profile: bool,
) -> Path:
    existed = output.exists()
    previous = output.read_bytes() if existed else None
    sections = ["model", "permissions", "sandbox"] if include_local_profile else ["model"]
    try:
        written = write_config_sections(output, config, sections)
        _audit().write(
            "config.provider_written",
            provider=config.model.provider,
            model=config.model.model,
            path=str(written),
            local_profile_initialized=include_local_profile,
        )
    except AuditDeliveryError as error:
        try:
            if previous is None:
                output.unlink(missing_ok=True)
            else:
                atomic_write_bytes(output, previous)
        except OSError as rollback_error:
            raise typer.BadParameter(
                "Required audit delivery failed and provider configuration rollback also "
                f"failed; inspect {output}: {rollback_error}"
            ) from error
        raise typer.BadParameter(
            "Provider configuration was rolled back because required audit delivery failed: "
            f"{error}"
        ) from error
    return written


@app.command("remember")
def remember(
    content: Annotated[str, typer.Argument(help="Memory content.")],
    local: Annotated[bool, typer.Option("--local", help="Write local memory.")] = False,
    shared: Annotated[
        bool, typer.Option("--shared", help="Write shared enterprise memory.")
    ] = False,
    tenant_id: Annotated[
        str | None,
        typer.Option("--tenant-id", help="Shared memory tenant. Defaults to active identity."),
    ] = None,
    scope_type: Annotated[
        str, typer.Option("--scope-type", help="Shared memory scope type.")
    ] = "org",
    scope_key: Annotated[
        str, typer.Option("--scope-key", help="Shared memory scope key.")
    ] = "default",
    memory_type: Annotated[str, typer.Option("--memory-type", help="Shared memory type.")] = "fact",
    classification: Annotated[
        str, typer.Option("--classification", help="Shared memory classification.")
    ] = "public-internal",
    created_by: Annotated[
        str | None,
        typer.Option("--created-by", help="Shared memory author. Defaults to active identity."),
    ] = None,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    """Explicitly write a local or shared memory."""
    config = load_config()
    _enforce_safe_content(
        content,
        context="memory.shared" if shared else "memory.local",
        allow_sensitive=allow_sensitive,
    )
    if shared:
        identity = _identity()
        resolved_tenant = _shared_memory_tenant(config, tenant_id)
        draft = create_shared_memory_draft(
            content=content,
            tenant_id=resolved_tenant,
            scope_type=scope_type,
            scope_key=scope_key,
            memory_type=memory_type,
            classification=classification,
            created_by=created_by or identity.subject,
            retention_days=config.memory.shared.retention_days,
        )
        _shared_draft_store(config).stage(draft)
        _audit().write(
            "memory.shared_draft_staged",
            draft_id=draft.draft_id,
            tenant_id=draft.tenant_id,
            scope_type=draft.scope_type,
            scope_key=draft.scope_key,
            prompt_preview=prompt_preview(content),
        )
        console.print(
            f"Staged shared memory draft: {draft.draft_id}\n"
            "Review it with `loro memory drafts`, then explicitly commit it."
        )
        return
    if local or not shared:
        store = LocalMemoryStore.from_config(config.memory.local, config.safety)
        memory = store.remember(content, allow_sensitive=allow_sensitive)
        _audit().write(
            "memory.local_written",
            memory_id=memory.memory_id,
            scope=memory.scope,
            content_preview=prompt_preview(content),
        )
        console.print(f"Saved local memory: {memory.memory_id}")


@app.command("create")
def create_artifact(
    artifact_type: Annotated[
        str, typer.Argument(help="Artifact type: docs, slides, sheets, or brief.")
    ],
    prompt: Annotated[str, typer.Argument(help="Artifact prompt.")],
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
    """Create an AI-drafted document, presentation, spreadsheet, or brief."""
    normalized = artifact_type.strip().lower()
    if normalized in {"doc", "docs", "document"}:
        _create_and_print_artifact(
            prompt=prompt,
            output_dir=output_dir,
            allow_sensitive=allow_sensitive,
            context="artifact.document",
            factory=create_document_artifact,
            kind="document",
            use_ai=not no_ai,
        )
        return
    if normalized in {"slide", "slides", "presentation"}:
        _create_and_print_artifact(
            prompt=prompt,
            output_dir=output_dir,
            allow_sensitive=allow_sensitive,
            context="artifact.presentation",
            factory=create_presentation_artifact,
            kind="presentation",
            use_ai=not no_ai,
        )
        return
    if normalized in {"sheet", "sheets", "spreadsheet"}:
        _create_and_print_artifact(
            prompt=prompt,
            output_dir=output_dir,
            allow_sensitive=allow_sensitive,
            context="artifact.spreadsheet",
            factory=create_spreadsheet_artifact,
            kind="spreadsheet",
            use_ai=not no_ai,
        )
        return
    if normalized == "brief":
        _create_and_print_brief(
            prompt=prompt,
            output_dir=output_dir,
            allow_sensitive=allow_sensitive,
            brief_type="general",
            use_ai=not no_ai,
        )
        return
    raise typer.BadParameter("Artifact type must be docs, slides, sheets, or brief.")
