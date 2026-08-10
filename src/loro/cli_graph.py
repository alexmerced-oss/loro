from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from loro.agraph.document import GraphDocumentError, load_graph
from loro.agraph.execute import GraphExecutionError, GraphExecutor
from loro.agraph.generate import write_generated_graph
from loro.agraph.plan import build_plan
from loro.agraph.policy import evaluate_policy
from loro.agraph.store import GraphRunStore
from loro.agraph.validate import validate_graph
from loro.config import load_config
from loro.data_protection import DataProtectionEngine
from loro.memory.proposals import MemoryProposal, MemoryProposalStore

graph_app = typer.Typer(help="Validate, govern, generate, and execute Agentic Graphs.")
policy_app = typer.Typer(help="Explain managed Agentic Graph policy decisions.")
graph_app.add_typer(policy_app, name="policy")
console = Console()


def _load(path: Path):
    try:
        return load_graph(path, max_bytes=load_config().agraph.max_document_bytes)
    except GraphDocumentError as error:
        raise typer.BadParameter(str(error)) from error


@graph_app.command("validate")
def graph_validate(
    path: Annotated[Path, typer.Argument(help="AGS JSON or YAML document.")],
    strict: Annotated[bool, typer.Option("--strict", help="Treat warnings as failures.")] = False,
) -> None:
    """Validate an AGS document and managed Loro policy."""
    document = _load(path)
    report = validate_graph(document)
    policy = evaluate_policy(document.data, load_config().agraph)
    payload = report.to_payload()
    payload["digest"] = document.digest
    payload["policy_findings"] = [item.__dict__ for item in policy]
    payload["ok"] = report.ok and not policy and not (strict and report.warnings)
    console.print_json(data=payload)
    if not payload["ok"]:
        raise typer.Exit(code=1)


@graph_app.command("plan")
def graph_plan(
    path: Annotated[Path, typer.Argument(help="AGS JSON or YAML document.")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show deterministic order, fan-out, cost, and routing demand."""
    document = _load(path)
    report = validate_graph(document)
    config = load_config()
    policy = evaluate_policy(document.data, config.agraph)
    if not report.ok or policy:
        payload = report.to_payload()
        payload["policy_findings"] = [item.__dict__ for item in policy]
        console.print_json(data=payload)
        raise typer.Exit(code=1)
    payload = build_plan(document.data).to_payload()
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
) -> None:
    """Explain every managed graph-policy denial."""
    document = _load(path)
    findings = evaluate_policy(document.data, load_config().agraph)
    console.print_json(
        data={"allowed": not findings, "findings": [item.__dict__ for item in findings]}
    )
    if findings:
        raise typer.Exit(code=1)


@graph_app.command("run")
def graph_run(
    path: Annotated[Path, typer.Argument(help="AGS JSON or YAML document.")],
    params: Annotated[
        str, typer.Option("--params", help="JSON object of graph parameters.")
    ] = "{}",
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
) -> None:
    """Execute a governed Agentic Graph."""
    config = load_config()
    if yes and not config.approvals.allow_non_interactive:
        raise typer.BadParameter("--yes is denied by approvals.allow_non_interactive")
    try:
        values = json.loads(params)
        if not isinstance(values, dict):
            raise ValueError("params must be an object")
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


@graph_app.command("resume")
def graph_resume(
    run_id: Annotated[str, typer.Argument(help="Durable graph run id.")],
    force: Annotated[bool, typer.Option("--force", help="Accept a changed graph digest.")] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Approve pending gates when policy allows.")
    ] = False,
    params: Annotated[
        str, typer.Option("--params", help="JSON params, required again for redacted values.")
    ] = "{}",
) -> None:
    """Resume a paused Agentic Graph with digest protection."""
    config = load_config()
    if yes and not config.approvals.allow_non_interactive:
        raise typer.BadParameter("--yes is denied by approvals.allow_non_interactive")
    force_approved = not force or yes
    if force and not yes:
        force_approved = typer.confirm(
            "The graph changed. Approve forced resume using the new digest?"
        )
    try:
        values = json.loads(params)
        if not isinstance(values, dict):
            raise ValueError("params must be an object")
        # `graph run` executes with the graph file's directory as the workspace. Resume
        # used to default to Path.cwd(), so file_exists/artifact_present/json_schema
        # criteria and local subgraph refs resolved against a different root.
        store = GraphRunStore(config.agraph, DataProtectionEngine(config.safety))
        source = str(store.get(run_id).get("metadata", {}).get("source", ""))
        workspace = Path(source).resolve().parent if source else None
        record = GraphExecutor(
            config,
            workspace=workspace,
            gate_provider=(lambda _prompt, _roles: True) if yes else None,
        ).resume(
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
) -> None:
    """Generate, validate, and policy-check an AGS graph skeleton."""
    try:
        path = write_generated_graph(goal, output, load_config())
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(str(path))


@graph_app.command("skill-path")
def graph_skill_path() -> None:
    """Print the bundled AGS authoring Skill path for reviewed installation."""
    bundled = Path(__file__).parent / "bundled_skills" / "agentic-graph"
    source = Path(__file__).resolve().parents[2] / "skills" / "agentic-graph"
    path = bundled if bundled.is_dir() else source
    if not path.is_dir():
        raise typer.BadParameter("bundled agentic-graph Skill is unavailable")
    typer.echo(path)
