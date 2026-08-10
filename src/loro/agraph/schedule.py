from __future__ import annotations

from collections import defaultdict
from typing import Any

from loro.agraph.expressions import evaluate
from loro.agraph.plan import effective_edges, topological_order

TERMINAL = {"succeeded", "failed", "skipped", "blocked", "cancelled"}


def ready_nodes(graph: dict[str, Any], states: dict[str, str], scope: dict[str, Any]) -> list[str]:
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in effective_edges(graph):
        incoming[edge["to"]].append(edge)
    ready: list[str] = []
    for node_id in topological_order(graph):
        if states.get(node_id, "pending") != "pending":
            continue
        edges = incoming[node_id]
        if not edges:
            if node_id in graph.get("entrypoints", ()):
                ready.append(node_id)
            else:
                states[node_id] = "skipped"
                scope["nodes"][node_id] = {"status": "skipped", "outputs": {}}
            continue
        if not all(states.get(edge["from"]) in TERMINAL for edge in edges):
            continue
        active = [_active(edge, states, scope) for edge in edges]
        join = graph["nodes"][node_id].get("join", "all")
        needed = len(active) if join == "all" else 1
        if join == "n_of":
            needed = int(graph["nodes"][node_id]["join_count"])
        if sum(active) >= needed:
            ready.append(node_id)
        else:
            states[node_id] = "skipped"
            scope["nodes"][node_id] = {"status": "skipped", "outputs": {}}
    return ready


def _active(edge: dict[str, Any], states: dict[str, str], scope: dict[str, Any]) -> bool:
    source = states.get(edge["from"])
    kind = edge.get("kind", "sequence")
    if kind == "on_failure":
        base = source in {"failed", "blocked"}
    else:
        base = source == "succeeded"
    return base and ("when" not in edge or bool(evaluate(str(edge["when"]), scope)))
