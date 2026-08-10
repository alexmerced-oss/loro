from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GraphPlan:
    graph_id: str
    node_count: int
    topological_order: tuple[str, ...]
    reachable: tuple[str, ...]
    edges: tuple[dict[str, Any], ...]
    worst_case_executions: int
    estimated_cost_usd: float | None
    tier_histogram: dict[str, int]
    max_parallel_nodes: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "topological_order": list(self.topological_order),
            "reachable": list(self.reachable),
            "edges": list(self.edges),
            "worst_case_executions": self.worst_case_executions,
            "estimated_cost_usd": self.estimated_cost_usd,
            "tier_histogram": self.tier_histogram,
            "max_parallel_nodes": self.max_parallel_nodes,
        }


def effective_edges(document: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    edges: list[dict[str, Any]] = [dict(edge) for edge in document.get("edges") or []]
    for node_id, node in (document.get("nodes") or {}).items():
        edges.extend(
            {"from": dependency, "to": node_id, "kind": "sequence"}
            for dependency in node.get("depends_on") or []
        )
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for edge in edges:
        edge.setdefault("kind", "sequence")
        key = (
            str(edge.get("from")),
            str(edge.get("to")),
            str(edge["kind"]),
            str(edge.get("when", "")),
        )
        if key not in seen:
            seen.add(key)
            result.append(edge)
    return tuple(result)


def build_plan(document: dict[str, Any]) -> GraphPlan:
    nodes = document.get("nodes") or {}
    edges = effective_edges(document)
    order = _topological_order(nodes, edges)
    reachable = _reachable(document.get("entrypoints") or [], edges)
    cost = _worst_cost(document)
    return GraphPlan(
        graph_id=str(document.get("id", "")),
        node_count=len(nodes),
        topological_order=tuple(order),
        reachable=tuple(node for node in order if node in reachable),
        edges=edges,
        worst_case_executions=_worst_case(document),
        estimated_cost_usd=cost,
        tier_histogram=dict(
            Counter(
                ((node.get("intelligence") or {}).get("tier") or "standard")
                for node in nodes.values()
                if node.get("type", "task") != "gate"
            )
        ),
        max_parallel_nodes=int((document.get("constraints") or {}).get("max_parallel_nodes", 1)),
    )


def topological_order(document: dict[str, Any]) -> tuple[str, ...]:
    """Return the normative deterministic order for a graph or fragment."""
    nodes = document.get("nodes") or {}
    return tuple(_topological_order(nodes, effective_edges(document)))


def _topological_order(nodes: dict[str, Any], edges: tuple[dict[str, Any], ...]) -> list[str]:
    declaration = {node: index for index, node in enumerate(nodes)}
    incoming = {node: 0 for node in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source, target = edge["from"], edge["to"]
        if source in nodes and target in nodes:
            incoming[target] += 1
            outgoing[source].append(target)
    ready = sorted((node for node, count in incoming.items() if count == 0), key=declaration.get)
    result: list[str] = []
    while ready:
        node = ready.pop(0)
        result.append(node)
        for target in outgoing[node]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort(key=declaration.get)
    return result


def _reachable(entrypoints: list[str], edges: tuple[dict[str, Any], ...]) -> set[str]:
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[str(edge["from"])].append(str(edge["to"]))
    found = set(entrypoints)
    queue = deque(entrypoints)
    while queue:
        for target in outgoing[queue.popleft()]:
            if target not in found:
                found.add(target)
                queue.append(target)
    return found


def _worst_case(document: dict[str, Any], multiplier: int = 1) -> int:
    total = 0
    fragments = document.get("subgraphs") or {}
    for node in (document.get("nodes") or {}).values():
        attempts = int(((node.get("failure") or {}).get("retry") or {}).get("max_attempts", 1))
        node_type = node.get("type", "task")
        factor = multiplier * attempts
        total += factor
        block = node.get(node_type) or {}
        body = block.get("body") or block.get("inline") or fragments.get(block.get("use"))
        if body:
            width = int(block.get("max_iterations", block.get("max_items", 1)))
            total += _worst_case(body, factor * width)
    return total


def _worst_cost(document: dict[str, Any], multiplier: int = 1) -> float | None:
    total = 0.0
    found = False
    fragments = document.get("subgraphs") or {}
    for node in (document.get("nodes") or {}).values():
        attempts = int(((node.get("failure") or {}).get("retry") or {}).get("max_attempts", 1))
        factor = multiplier * attempts
        estimate = (node.get("estimate") or {}).get("cost_usd")
        if estimate is not None:
            total += float(estimate) * factor
            found = True
        node_type = node.get("type", "task")
        block = node.get(node_type) or {}
        body = block.get("body") or block.get("inline") or fragments.get(block.get("use"))
        if body:
            width = int(block.get("max_iterations", block.get("max_items", 1)))
            nested = _worst_cost(body, factor * width)
            if nested is not None:
                total += nested
                found = True
    return total if found else None
