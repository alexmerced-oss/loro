"""Agentic Graph support for the local Web UI.

Loro is an AGS level 3 harness with durable resume, gates, loops, maps and
subgraphs, and none of it was reachable from `loro web`. This module exposes
the same runtime the CLI drives: discovery, validation, planning, execution,
and the durable run records the executor already writes.

Nothing here widens authority. Execution goes through `GraphExecutor`, so
identity, permission policy, sandbox profiles, budgets and the audit log all
apply exactly as they do from the terminal. Human gates are the one place the
browser participates: a gate blocks the run until a decision arrives, rather
than being auto-approved.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Any
from uuid import uuid4

from loro.agraph.document import load_graph
from loro.agraph.plan import build_plan
from loro.agraph.store import GraphRunStore
from loro.agraph.validate import validate_graph
from loro.config import load_config
from loro.data_protection import DataProtectionEngine

GRAPH_SUFFIXES = (".agraph.yaml", ".agraph.yml", ".agraph.json")

# Discovery is bounded: a deep node_modules tree should not become a file scan.
MAX_DISCOVERY_DEPTH = 4
MAX_DISCOVERED = 200
SKIP_DIRECTORIES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".loro",
}


def confined_path(project_root: Path, raw: str) -> Path:
    """Resolve `raw` inside `project_root`, refusing anything that escapes it."""
    root = project_root.resolve()
    candidate = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Graph path must stay inside the workspace.")
    return candidate


def discover(project_root: Path) -> list[dict[str, Any]]:
    """Find Agentic Graph documents in the workspace."""
    root = project_root.resolve()
    found: list[dict[str, Any]] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > MAX_DISCOVERY_DEPTH or len(found) >= MAX_DISCOVERED:
            return
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if len(found) >= MAX_DISCOVERED:
                return
            if entry.is_dir():
                if entry.name in SKIP_DIRECTORIES or entry.name.startswith("."):
                    continue
                walk(entry, depth + 1)
            elif any(entry.name.endswith(suffix) for suffix in GRAPH_SUFFIXES):
                found.append(
                    {
                        "path": str(entry.relative_to(root)),
                        "name": entry.name,
                        "size_bytes": entry.stat().st_size,
                    }
                )

    walk(root, 0)
    return found


def describe(project_root: Path, raw_path: str) -> dict[str, Any]:
    """Validate and plan a graph without running anything."""
    path = confined_path(project_root, raw_path)
    if not path.is_file():
        raise FileNotFoundError(f"No graph at {raw_path}")

    config = load_config(project_root)
    document = load_graph(path, max_bytes=config.agraph.max_document_bytes)
    report = validate_graph(document)
    plan = build_plan(document.data)

    nodes = []
    raw_nodes = dict(document.data.get("nodes", {}))
    for node_id in plan.topological_order:
        node = dict(raw_nodes.get(node_id, {}))
        nodes.append(
            {
                "id": node_id,
                "title": str(node.get("title") or node_id),
                "type": str(node.get("type") or "task"),
                "description": str(node.get("description") or "").strip(),
                "depends_on": list(node.get("depends_on") or []),
                "profile": str(node.get("x-agent-profile") or ""),
                "tier": str((node.get("intelligence") or {}).get("tier") or ""),
                "state": "pending",
            }
        )

    gates = [item["id"] for item in nodes if item["type"] == "gate"]

    return {
        "ok": report.ok,
        "path": str(path.relative_to(project_root.resolve())),
        "graph_id": document.graph_id,
        "title": str(document.data.get("title") or document.graph_id),
        "objective": str(document.data.get("objective") or ""),
        "digest": document.digest,
        "nodes": nodes,
        "gates": gates,
        "node_count": plan.node_count,
        "max_parallel": plan.max_parallel_nodes,
        "worst_case_executions": plan.worst_case_executions,
        "estimated_cost_usd": plan.estimated_cost_usd,
        "tier_histogram": plan.tier_histogram,
        "findings": [finding.__dict__ for finding in report.findings],
    }


@dataclass
class GateRequest:
    request_id: str
    prompt: str
    roles: list[str]
    decided: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


class GraphRunHandle:
    """A running graph, its event stream, and any gate awaiting a decision."""

    def __init__(self, run_id: str, path: str) -> None:
        self.run_id = run_id
        self.path = path
        self.events: list[dict[str, Any]] = []
        self.queue: Queue[dict[str, Any]] = Queue()
        self.lock = threading.Lock()
        self.status = "running"
        self.record: dict[str, Any] | None = None
        self.error: str | None = None
        self.gate: GateRequest | None = None

    def publish(self, event_type: str, **payload: Any) -> None:
        with self.lock:
            event = {"seq": len(self.events), "type": event_type, **payload}
            self.events.append(event)
        self.queue.put(event)

    def since(self, after: int) -> list[dict[str, Any]]:
        """Replay from a cursor so a reconnecting browser loses nothing."""
        with self.lock:
            return [event for event in self.events if event["seq"] > after]

    def wait(self, timeout: float) -> dict[str, Any] | None:
        try:
            return self.queue.get(timeout=timeout)
        except Empty:
            return None

    def decide_gate(self, request_id: str, approved: bool) -> bool:
        gate = self.gate
        if gate is None or gate.request_id != request_id or gate.decided.is_set():
            return False
        gate.approved = approved
        gate.decided.set()
        return True


class GraphService:
    """Discovery, planning, and governed execution for the Web UI."""

    def __init__(self, project_root: Path, *, max_concurrent: int = 2) -> None:
        self.project_root = project_root.resolve()
        self.handles: dict[str, GraphRunHandle] = {}
        self.semaphore = threading.BoundedSemaphore(max_concurrent)
        self.lock = threading.Lock()

    # -- reads ---------------------------------------------------------------

    def list_graphs(self) -> list[dict[str, Any]]:
        return discover(self.project_root)

    def plan(self, raw_path: str) -> dict[str, Any]:
        return describe(self.project_root, raw_path)

    def history(self, limit: int = 25) -> list[dict[str, Any]]:
        config = load_config(self.project_root)
        store = GraphRunStore(config.agraph, DataProtectionEngine(config.safety))
        records = []
        for run_id in list(store.list())[:limit]:
            try:
                record = store.get(run_id if isinstance(run_id, str) else str(run_id))
            except Exception:
                continue
            records.append(
                {
                    "run_id": record.get("run_id") or run_id,
                    "graph_id": record.get("graph_id", ""),
                    "status": record.get("status", ""),
                    "started_at": record.get("started_at", ""),
                    "finished_at": record.get("finished_at", ""),
                }
            )
        return records

    def record(self, run_id: str) -> dict[str, Any]:
        config = load_config(self.project_root)
        store = GraphRunStore(config.agraph, DataProtectionEngine(config.safety))
        return store.get(run_id)

    # -- execution -----------------------------------------------------------

    def start(self, raw_path: str, *, dry_run: bool = False) -> GraphRunHandle:
        # Validate before taking a slot: a broken document should fail loudly
        # and immediately rather than occupying a worker.
        summary = self.plan(raw_path)
        if not summary["ok"]:
            errors = [f["message"] for f in summary["findings"] if f.get("severity") == "error"]
            raise ValueError("; ".join(errors) or "The graph is not valid.")

        run_id = str(uuid4())
        handle = GraphRunHandle(run_id, summary["path"])
        with self.lock:
            self.handles[run_id] = handle
        handle.publish(
            "run.started",
            run_id=run_id,
            graph_id=summary["graph_id"],
            digest=summary["digest"],
            nodes=summary["nodes"],
            dry_run=dry_run,
        )

        thread = threading.Thread(
            target=self._execute,
            args=(handle, raw_path, dry_run),
            name=f"loro-graph-{run_id[:8]}",
            daemon=True,
        )
        thread.start()
        return handle

    def _execute(self, handle: GraphRunHandle, raw_path: str, dry_run: bool) -> None:
        acquired = self.semaphore.acquire(timeout=300)
        if not acquired:
            handle.status = "failed"
            handle.error = "Timed out waiting for a free graph worker."
            handle.publish("run.failed", error=handle.error)
            return
        try:
            from loro.agraph.execute import GraphExecutor

            config = load_config(self.project_root)
            path = confined_path(self.project_root, raw_path)

            def gate_provider(prompt: str, roles: Any) -> bool:
                """Block the run on a browser decision instead of auto-approving."""
                request = GateRequest(
                    request_id=str(uuid4()),
                    prompt=str(prompt),
                    roles=[str(role) for role in (roles or [])],
                )
                handle.gate = request
                handle.publish(
                    "gate.requested",
                    request_id=request.request_id,
                    prompt=request.prompt,
                    roles=request.roles,
                )
                # A gate that is never answered must not pin a worker forever.
                if not request.decided.wait(timeout=1800):
                    handle.publish("gate.timeout", request_id=request.request_id)
                    return False
                handle.publish(
                    "gate.resolved",
                    request_id=request.request_id,
                    approved=request.approved,
                )
                handle.gate = None
                return request.approved

            executor = GraphExecutor(
                config,
                workspace=path.parent,
                gate_provider=gate_provider,
            )
            record = executor.run(
                path,
                dry_run=dry_run,
                run_id=handle.run_id,
                # The browser approved this exact digest by starting the run.
                plan_approved=True,
            )
            handle.record = record
            handle.status = str(record.get("status") or "succeeded")
            handle.publish("run.finished", status=handle.status, record=record)
        except Exception as error:  # noqa: BLE001 - surfaced to the client
            handle.status = "failed"
            handle.error = str(error)
            handle.publish("run.failed", error=handle.error)
        finally:
            self.semaphore.release()
            handle.publish("run.closed", status=handle.status)

    def handle(self, run_id: str) -> GraphRunHandle:
        handle = self.handles.get(run_id)
        if handle is None:
            raise KeyError(run_id)
        return handle
