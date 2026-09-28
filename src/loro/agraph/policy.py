from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

from loro.config import AGraphConfig

TIER_RANK = {"minimal": 1, "standard": 2, "advanced": 3, "frontier": 4}

# Node-level `x-` extensions that choose how a node executes. AGS lets a harness ignore `x-`
# members, but an extension must not change execution semantics (SPEC 23), so a graph that
# depends on an executor Loro does not implement is refused rather than quietly run as a model
# task. Loro implements none yet.
IMPLEMENTED_EXECUTOR_EXTENSIONS: frozenset[str] = frozenset()


def executor_extensions(node: dict[str, Any]) -> list[str]:
    """The executor extension keys on a node (`x-executor` or `x-<vendor>-executor`)."""

    return sorted(
        key
        for key in node
        if isinstance(key, str)
        and key.startswith("x-")
        and (key == "x-executor" or key.endswith("-executor"))
    )


@dataclass(frozen=True)
class PolicyFinding:
    code: str
    message: str
    pointer: str
    severity: str = "error"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def evaluate_policy(document: dict[str, Any], config: AGraphConfig) -> tuple[PolicyFinding, ...]:
    findings: list[PolicyFinding] = []
    if not isinstance(document, dict):
        return (PolicyFinding("AG001", "graph document must be an object", ""),)
    if not config.enabled:
        findings.append(PolicyFinding("LP001", "Agentic Graph support is disabled.", ""))
        return tuple(findings)
    nodes = document.get("nodes") or {}
    if not isinstance(nodes, dict):
        return (PolicyFinding("AG001", "graph nodes must be an object", "/nodes"),)
    nodes = {node_id: node for node_id, node in nodes.items() if isinstance(node, dict)}
    if len(nodes) > config.max_nodes:
        findings.append(
            PolicyFinding(
                "LP002",
                f"graph has {len(nodes)} nodes; managed maximum is {config.max_nodes}",
                "/nodes",
            )
        )
    # Callers evaluate policy on raw data before checking the schema report, so an
    # out-of-enum tier or non-integer conformance level must produce a finding, not a
    # KeyError/ValueError traceback.
    required_level = _int_or_none(document.get("requires_conformance", 1))
    if required_level is None:
        findings.append(
            PolicyFinding(
                "AG001",
                "requires_conformance must be an integer",
                "/requires_conformance",
            )
        )
        required_level = 1
    maximum_level = min(config.conformance_level, 3)
    if required_level > maximum_level:
        findings.append(
            PolicyFinding(
                "AG303",
                f"graph requires conformance level {required_level}; "
                f"harness policy allows {maximum_level}",
                "/requires_conformance",
            )
        )
    for node_id, node in nodes.items():
        pointer = f"/nodes/{node_id}"
        for key in executor_extensions(node):
            if key in IMPLEMENTED_EXECUTOR_EXTENSIONS:
                continue
            if config.allow_unknown_executors:
                findings.append(
                    PolicyFinding(
                        "LP011",
                        f"node {node_id!r} declares executor extension {key!r}, which Loro "
                        "does not implement; it will run as an ordinary model task because "
                        "[agraph] allow_unknown_executors is true",
                        f"{pointer}/{key}",
                        severity="warning",
                    )
                )
            else:
                findings.append(
                    PolicyFinding(
                        "LP011",
                        f"node {node_id!r} declares executor extension {key!r}, which Loro "
                        "does not implement. Run it in a harness that does, or pass "
                        "--allow-unknown-executors (or set [agraph] allow_unknown_executors = "
                        "true) to run the node as an ordinary model task",
                        f"{pointer}/{key}",
                    )
                )
        tier = ((node.get("intelligence") or {}).get("tier")) or "standard"
        if tier not in TIER_RANK:
            findings.append(
                PolicyFinding(
                    "AG001",
                    f"unknown intelligence tier {tier!r}",
                    f"{pointer}/intelligence/tier",
                )
            )
        if TIER_RANK.get(tier, TIER_RANK["standard"]) > TIER_RANK[config.max_tier]:
            findings.append(
                PolicyFinding(
                    "LP003",
                    f"tier {tier!r} exceeds managed maximum {config.max_tier!r}",
                    f"{pointer}/intelligence/tier",
                )
            )
        requirements = node.get("requirements") or {}
        permissions = requirements.get("permissions") or []
        for permission in permissions:
            if any(
                fnmatch.fnmatchcase(permission, pattern) for pattern in config.forbidden_permissions
            ):
                findings.append(
                    PolicyFinding(
                        "LP004",
                        f"permission {permission!r} is forbidden",
                        f"{pointer}/requirements/permissions",
                    )
                )
            if any(
                fnmatch.fnmatchcase(permission, pattern) for pattern in config.require_gate_before
            ) and not _has_gate_predecessor(document, node_id):
                findings.append(
                    PolicyFinding(
                        "LP005", f"permission {permission!r} requires a preceding gate", pointer
                    )
                )
        criteria = ((node.get("success") or {}).get("criteria")) or []
        kinds = {criterion.get("kind") for criterion in criteria}
        if "command" in kinds and not config.allow_command_criteria:
            findings.append(
                PolicyFinding(
                    "LP006",
                    (
                        "command criteria are disabled by graph policy; an administrator can "
                        "enable them with [agraph] allow_command_criteria = true"
                    ),
                    f"{pointer}/success/criteria",
                )
            )
        if "external" in kinds and not config.allow_external_criteria:
            findings.append(
                PolicyFinding(
                    "LP007",
                    (
                        "external criteria are disabled by graph policy; an administrator can "
                        "enable them with [agraph] allow_external_criteria = true"
                    ),
                    f"{pointer}/success/criteria",
                )
            )
        if config.required_criteria_kinds and node.get("type", "task") == "task":
            if not kinds.intersection(config.required_criteria_kinds):
                findings.append(
                    PolicyFinding(
                        "LP008",
                        "node lacks a managed required criterion kind",
                        f"{pointer}/success",
                    )
                )
        block = node.get("subgraph") or {}
        ref = block.get("ref") or {}
        uri = str(ref.get("uri", ""))
        if uri and not uri.startswith((".", "/")) and not config.allow_external_subgraph_refs:
            findings.append(
                PolicyFinding(
                    "LP009",
                    "external subgraph references are disabled",
                    f"{pointer}/subgraph/ref/uri",
                )
            )
        if uri and config.require_integrity_for_refs and not ref.get("integrity"):
            findings.append(
                PolicyFinding(
                    "LP010",
                    "subgraph reference requires an integrity digest",
                    f"{pointer}/subgraph/ref",
                )
            )
    return tuple(findings)


def _has_gate_predecessor(document: dict[str, Any], node_id: str) -> bool:
    nodes = document.get("nodes") or {}
    incoming = set((nodes.get(node_id) or {}).get("depends_on") or [])
    incoming.update(
        edge.get("from") for edge in document.get("edges") or [] if edge.get("to") == node_id
    )
    seen: set[str] = set()
    while incoming:
        predecessor = incoming.pop()
        if predecessor in seen or predecessor not in nodes:
            continue
        seen.add(predecessor)
        if (nodes[predecessor].get("type") or "task") == "gate":
            return True
        incoming.update(nodes[predecessor].get("depends_on") or [])
        incoming.update(
            edge.get("from")
            for edge in document.get("edges") or []
            if edge.get("to") == predecessor
        )
    return False
