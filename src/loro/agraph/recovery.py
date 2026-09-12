"""Read-only recovery evidence; inspecting a record never resumes an action."""

from pathlib import Path
from typing import Any

from loro.agraph.document import load_graph


def recovery_summary(record: dict[str, Any]) -> dict[str, Any]:
    nodes = record.get("nodes", {})
    if isinstance(nodes, list):
        nodes = {item["node_id"]: item for item in nodes}
    source = record.get("metadata", {}).get("source")
    current_digest = None
    error = None
    if source:
        try:
            current_digest = load_graph(Path(source)).digest
        except (OSError, ValueError) as exc:
            error = str(exc)
    return {
        "run_id": record.get("run_id"),
        "status": record.get("status"),
        "completed_nodes": [
            key for key, node in nodes.items() if node.get("status") == "succeeded"
        ],
        "pending_gates": [
            key for key, node in nodes.items() if node.get("status") == "awaiting_human"
        ],
        "uncertain_nodes": [key for key, node in nodes.items() if node.get("status") == "running"],
        "recorded_graph_digest": record.get("graph_digest"),
        "current_graph_digest": current_digest,
        "graph_changed": current_digest != record.get("graph_digest") if current_digest else None,
        "source_error": error,
        "guidance": (
            "Review uncertain effects and current profiles before explicit resume. "
            "Completed nodes are preserved; this report does not execute tools."
        ),
    }
