"""Diagnostics for AGS fields this harness accepts but does not enforce.

A graph that declares a timeout, an escalation path, or a bounded map fan-out reads as
governed. Where the executor silently ignores such a field, the graph is not actually
governed by it — so the loader reports it rather than letting the document imply a
guarantee the run will not honor.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from loro.agraph.reference_validator import Finding

__all__ = ["UNSUPPORTED_FIELDS", "unsupported_feature_findings"]

# (JSON-pointer-ish path within a node, human description). The path is matched against
# the node's own structure, so "policy.on_expression_error" means node["policy"]["…"].
UNSUPPORTED_FIELDS: tuple[tuple[str, str], ...] = (
    ("policy.on_expression_error", "expression-error policy is not applied at run time"),
    ("map.max_parallel", "map fan-out runs under the graph-wide parallel limit instead"),
    ("success.evaluation_order", "criteria are evaluated in declaration order"),
    ("failure.escalation", "escalation routing is not performed"),
)

# criterion.timeout_seconds is honored only for command criteria.
_CRITERION_TIMEOUT_SUPPORTED_KIND = "command"


def unsupported_feature_findings(document: dict[str, Any]) -> list[Finding]:
    """Report every accepted-but-unenforced field present in the document."""

    findings: list[Finding] = []
    nodes = document.get("nodes")
    if not isinstance(nodes, dict):
        return findings
    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        pointer_base = f"/nodes/{node_id}"
        for path, description in UNSUPPORTED_FIELDS:
            if _present(node, path):
                findings.append(
                    Finding(
                        "AG901",
                        # Informational, not a defect in the graph: `--strict` treats
                        # warnings as failures, and a portable graph declaring a field
                        # this harness does not enforce is still a valid graph.
                        "info",
                        f"{path} is accepted but not enforced by this harness: {description}.",
                        f"{pointer_base}/{path.replace('.', '/')}",
                    )
                )
        gate = node.get("gate")
        if isinstance(gate, dict) and gate.get("on_timeout") == "escalate":
            # Gate timeouts are enforced; escalation routing is not, so a timed-out gate
            # with on_timeout: escalate fails closed instead.
            findings.append(
                Finding(
                    "AG901",
                    "info",
                    "gate.on_timeout: escalate is accepted but escalation routing is not "
                    "performed by this harness: a timed-out gate fails instead.",
                    f"{pointer_base}/gate/on_timeout",
                )
            )
        for index, criterion in _criteria(node):
            if not isinstance(criterion, dict) or "timeout_seconds" not in criterion:
                continue
            if criterion.get("kind") == _CRITERION_TIMEOUT_SUPPORTED_KIND:
                continue
            findings.append(
                Finding(
                    "AG901",
                    "info",
                    "criterion.timeout_seconds is accepted but only enforced for "
                    f"{_CRITERION_TIMEOUT_SUPPORTED_KIND} criteria.",
                    f"{pointer_base}/success/criteria/{index}/timeout_seconds",
                )
            )
    return findings


def _present(node: dict[str, Any], path: str) -> bool:
    current: Any = node
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return False
        current = current[segment]
    return current is not None


def _criteria(node: dict[str, Any]) -> Iterator[tuple[int, Any]]:
    success = node.get("success")
    criteria = success.get("criteria") if isinstance(success, dict) else None
    if isinstance(criteria, list):
        yield from enumerate(criteria)
