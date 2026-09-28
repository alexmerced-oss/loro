from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from loro.agraph.document import GraphDocumentError, load_graph
from loro.agraph.execute import GraphExecutionError, GraphExecutor, RedactedParamsError
from loro.agraph.generate import write_ai_generated_graph, write_generated_graph
from loro.agraph.plan import build_plan
from loro.agraph.policy import evaluate_policy
from loro.agraph.store import GraphRunStore
from loro.agraph.validate import validate_graph
from loro.config import load_config
from loro.data_protection import DataProtectionEngine
from loro.memory.proposals import MemoryProposal, MemoryProposalStore
from loro.runtime import AgentRuntime

graph_app = typer.Typer(help="Validate, govern, generate, and execute Agentic Graphs.")
policy_app = typer.Typer(help="Explain managed Agentic Graph policy decisions.")
graph_app.add_typer(policy_app, name="policy")
console = Console()


AllowUnknownExecutors = Annotated[
    bool,
    typer.Option(
        "--allow-unknown-executors",
        help="Run nodes whose executor extension Loro does not implement as model tasks.",
    ),
]
stderr = Console(stderr=True)


def _graph_config(allow_unknown_executors: bool = False):
    """Loaded config, with unknown executor extensions allowed when the flag asks for it."""

    config = load_config()
    if allow_unknown_executors:
        config.agraph = config.agraph.model_copy(update={"allow_unknown_executors": True})
    return config


def _policy_errors(findings) -> list:
    return [item for item in findings if item.severity == "error"]


def _warn(findings) -> None:
    for item in findings:
        if item.severity == "warning":
            stderr.print(f"Warning {item.code}: {item.message}")


def _load(path: Path):
    try:
        return load_graph(path, max_bytes=load_config().agraph.max_document_bytes)
    except GraphDocumentError as error:
        raise typer.BadParameter(str(error)) from error


@graph_app.command("validate")
def graph_validate(
    path: Annotated[Path, typer.Argument(help="AGS JSON or YAML document.")],
    strict: Annotated[bool, typer.Option("--strict", help="Treat warnings as failures.")] = False,
    allow_unknown_executors: AllowUnknownExecutors = False,
) -> None:
    """Validate an AGS document and managed Loro policy."""
    document = _load(path)
    report = validate_graph(document)
    policy = evaluate_policy(document.data, _graph_config(allow_unknown_executors).agraph)
    payload = report.to_payload()
    payload["digest"] = document.digest
    payload["policy_findings"] = [item.__dict__ for item in policy]
    policy_warnings = len(policy) - len(_policy_errors(policy))
    payload["ok"] = (
        report.ok
        and not _policy_errors(policy)
        and not (strict and (report.warnings or policy_warnings))
    )
    console.print_json(data=payload)
    if not payload["ok"]:
        raise typer.Exit(code=1)


@graph_app.command("plan")
def graph_plan(
    path: Annotated[Path, typer.Argument(help="AGS JSON or YAML document.")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
    allow_unknown_executors: AllowUnknownExecutors = False,
) -> None:
    """Show deterministic order, fan-out, cost, and routing demand."""
    document = _load(path)
    report = validate_graph(document)
    config = _graph_config(allow_unknown_executors)
    policy = evaluate_policy(document.data, config.agraph)
    if not report.ok or _policy_errors(policy):
        payload = report.to_payload()
        payload["policy_findings"] = [item.__dict__ for item in policy]
        console.print_json(data=payload)
        raise typer.Exit(code=1)
    _warn(policy)
    payload = build_plan(document.data).to_payload()
    payload["policy_findings"] = [item.__dict__ for item in policy]
    payload["effective_max_parallel_nodes"] = min(
        payload["max_parallel_nodes"], config.agraph.max_parallel_nodes
    )
    if as_json:
        console.print_json(data=payload)
        return
    console.print(f"Graph: {payload['graph_id']}")
    console.print("Order: " + " -> ".join(payload["topological_order"]))
    console.print(f"Worst-case executions: {payload['worst_case_executions']}")
    console.print(f"Estimated cost: {payload['estimated_cost_usd']}")
    console.print(f"Tier demand: {payload['tier_histogram']}")


@policy_app.command("explain")
def graph_policy_explain(
    path: Annotated[Path, typer.Argument(help="AGS JSON or YAML document.")],
    allow_unknown_executors: AllowUnknownExecutors = False,
) -> None:
    """Explain every managed graph-policy denial."""
    document = _load(path)
    findings = evaluate_policy(document.data, _graph_config(allow_unknown_executors).agraph)
    allowed = not _policy_errors(findings)
    console.print_json(data={"allowed": allowed, "findings": [item.__dict__ for item in findings]})
    if not allowed:
        raise typer.Exit(code=1)


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _param_value(raw: str, spec: Mapping[str, Any] | None) -> Any:
    """A --param or prompted value: text for string params, JSON for other declared types."""

    if spec is None or spec.get("type") in (None, "string"):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _param_values(
    params: str, param_file: Path | None, pairs: list[str], specs: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge --params, then --param-file, then each --param NAME=VALUE (later wins)."""

    values: dict[str, Any] = {}
    sources: list[tuple[str, str]] = [("--params", params)]
    if param_file is not None:
        try:
            sources.append((f"--param-file {param_file}", param_file.read_text(encoding="utf-8")))
        except OSError as error:
            raise ValueError(f"cannot read --param-file {param_file}: {error.strerror}") from error
    for label, text in sources:
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} is not valid JSON: {error.msg}") from error
        if not isinstance(loaded, dict):
            raise ValueError(f"{label} must be a JSON object")
        values.update(loaded)
    for pair in pairs:
        name, separator, raw = pair.partition("=")
        if not separator or not name:
            raise ValueError(f"--param expects NAME=VALUE, got {pair.split('=')[0]!r}")
        values[name] = _param_value(raw, specs.get(name))
    return values


@graph_app.command("run")
def graph_run(
    path: Annotated[Path, typer.Argument(help="AGS JSON or YAML document.")],
    params: Annotated[
        str, typer.Option("--params", help="JSON object of graph parameters.")
    ] = "{}",
    param: Annotated[
        list[str] | None,
        typer.Option(
            "--param",
            help="One graph parameter as NAME=VALUE (repeatable; JSON for non-string types).",
        ),
    ] = None,
    param_file: Annotated[
        Path | None,
        typer.Option("--param-file", help="JSON file with graph parameters."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate and persist a plan without executing.")
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Approve graph gates non-interactively when policy allows."),
    ] = False,
    remember_outcome: Annotated[
        str | None,
        typer.Option(
            "--remember-outcome",
            help="Explicit text to propose for shared memory after a successful run.",
        ),
    ] = None,
    allow_unknown_executors: AllowUnknownExecutors = False,
) -> None:
    """Execute a governed Agentic Graph.

    Parameters come from --params, then --param-file, then each --param (later wins).
    """
    config = _graph_config(allow_unknown_executors)
    if yes and not config.approvals.allow_non_interactive:
        raise typer.BadParameter("--yes is denied by approvals.allow_non_interactive")
    _warn(evaluate_policy(_load(path).data, config.agraph))
    try:
        specs = (_load(path).data.get("params") or {}) if path.is_file() else {}
        values = _param_values(params, param_file, list(param or []), specs)
        executor = GraphExecutor(
            config,
            workspace=path.resolve().parent,
            gate_provider=(lambda _prompt, _roles: True) if yes else None,
        )
        approved = dry_run or yes
        if not dry_run and not yes:
            document = _load(path)
            plan = build_plan(document.data)
            console.print(
                f"Graph {document.graph_id}: {plan.node_count} nodes, "
                f"worst-case {plan.worst_case_executions} executions"
            )
            console.print(f"Digest: {document.digest}")
            approved = typer.confirm("Approve this exact graph digest for execution?")
        if not approved:
            raise typer.Abort()
        record = executor.run(
            path,
            params=values,
            dry_run=dry_run,
            plan_approved=approved,
        )
    except (ValueError, GraphExecutionError) as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=record)
    if remember_outcome and record["status"] == "succeeded":
        protected = (
            DataProtectionEngine(config.safety).enforce(remember_outcome, "memory_shared").content
        )
        proposal = MemoryProposal(
            content=protected,
            target="shared",
            rationale=f"Explicit outcome from AGS run {record['run_id']}",
        )
        MemoryProposalStore(Path(config.memory.local.path)).propose(proposal)
        console.print(f"Created shared-memory proposal: {proposal.proposal_id}")
    if record["status"] == "failed":
        raise typer.Exit(code=1)


@graph_app.command("status")
def graph_status(run_id: Annotated[str, typer.Argument(help="Durable graph run id.")]) -> None:
    """Read a durable Agentic Graph run record."""
    config = load_config()
    try:
        record = GraphRunStore(config.agraph, DataProtectionEngine(config.safety)).get(run_id)
    except (FileNotFoundError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=record)


@graph_app.command("recovery")
def graph_recovery(run_id: Annotated[str, typer.Argument(help="Durable graph run id.")]) -> None:
    """Show completed, pending and uncertain work before deciding to resume."""
    from loro.agraph.recovery import recovery_summary

    config = load_config()
    try:
        record = GraphRunStore(config.agraph, DataProtectionEngine(config.safety)).get(run_id)
        console.print_json(data=recovery_summary(record))
    except (FileNotFoundError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error


@graph_app.command("resume")
def graph_resume(
    run_id: Annotated[str, typer.Argument(help="Durable graph run id.")],
    force: Annotated[bool, typer.Option("--force", help="Accept a changed graph digest.")] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Approve pending gates when policy allows.")
    ] = False,
    params: Annotated[
        str, typer.Option("--params", help="JSON object of params to supply again.")
    ] = "{}",
    param: Annotated[
        list[str] | None,
        typer.Option(
            "--param",
            help="Supply one param again as NAME=VALUE (repeatable; JSON for non-string types).",
        ),
    ] = None,
    param_file: Annotated[
        Path | None,
        typer.Option("--param-file", help="JSON file with params to supply again."),
    ] = None,
    allow_unknown_executors: AllowUnknownExecutors = False,
) -> None:
    """Resume a paused Agentic Graph with digest protection.

    Values redacted in the run record (secrets caught by data protection) must be supplied
    again. In a terminal, Loro asks for each one with hidden input; otherwise it exits 2 and
    names them.
    """
    config = _graph_config(allow_unknown_executors)
    if yes and not config.approvals.allow_non_interactive:
        raise typer.BadParameter("--yes is denied by approvals.allow_non_interactive")
    try:
        # `graph run` executes with the graph file's directory as the workspace. Resume
        # used to default to Path.cwd(), so file_exists/artifact_present/json_schema
        # criteria and local subgraph refs resolved against a different root.
        store = GraphRunStore(config.agraph, DataProtectionEngine(config.safety))
        source = str(store.get(run_id).get("metadata", {}).get("source", ""))
        workspace = Path(source).resolve().parent if source else None
        specs: Mapping[str, Any] = {}
        if source and Path(source).is_file():
            specs = _load(Path(source)).data.get("params", {}) or {}
        values = _param_values(params, param_file, list(param or []), specs)
        executor = GraphExecutor(
            config,
            workspace=workspace,
            gate_provider=(lambda _prompt, _roles: True) if yes else None,
        )
        missing = executor.redacted_params(run_id, values)
        if missing and _interactive():
            console.print(
                "These params were redacted in the run record; enter them again "
                "(input is hidden): " + ", ".join(missing)
            )
            for name in missing:
                entered = typer.prompt(name, hide_input=True)
                values[name] = _param_value(entered, specs.get(name))
        elif missing:
            raise RedactedParamsError(missing)
    except (FileNotFoundError, ValueError, GraphExecutionError) as error:
        raise typer.BadParameter(str(error)) from error
    force_approved = not force or yes
    if force and not yes:
        force_approved = typer.confirm(
            "The graph changed. Approve forced resume using the new digest?"
        )
    try:
        record = executor.resume(
            run_id,
            force=force,
            force_approved=force_approved,
            params=values,
        )
    except (FileNotFoundError, ValueError, GraphExecutionError) as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=record)


@graph_app.command("generate")
def graph_generate(
    goal: Annotated[str, typer.Argument(help="Goal to decompose into a governed graph.")],
    output: Annotated[Path, typer.Option("--out", help="Output .agraph.yaml path.")] = Path(
        "generated.agraph.yaml"
    ),
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="Explicitly create an offline one-node skeleton.")
    ] = False,
) -> None:
    """Author, validate, and policy-check an AGS graph."""
    config = load_config()
    try:
        if no_ai:
            path = write_generated_graph(goal, output, config)
        else:
            runtime = AgentRuntime(config)
            path = write_ai_generated_graph(
                goal,
                output,
                config,
                lambda prompt: runtime.run(prompt, mode="plan", session_id=None).response,
            )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(str(path))


@graph_app.command("skill-path")
def graph_skill_path() -> None:
    """Print the bundled AGS authoring Skill path for reviewed installation."""
    bundled = Path(__file__).resolve().parents[1] / "bundled_skills" / "agentic-graph"
    source = Path(__file__).resolve().parents[3] / "skills" / "agentic-graph"
    path = bundled if bundled.is_dir() else source
    if not path.is_dir():
        raise typer.BadParameter("bundled agentic-graph Skill is unavailable")
    typer.echo(path)
