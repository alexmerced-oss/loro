"""Agentic Graph Specification 1.0 support for Loro."""

from loro.agraph.document import GraphDocument, GraphDocumentError, load_graph
from loro.agraph.validate import GraphReport, validate_graph

CONFORMANCE_LEVEL = 3
SUPPORTED_FEATURES = (
    "validation.layers.1-3",
    "planning",
    "sequential-and-parallel-scheduling",
    "task-decision-gate-loop-map-subgraph",
    "criteria.all",
    "durable-run-records",
    "resume-digest-guard",
    "generation",
)

__all__ = [
    "CONFORMANCE_LEVEL",
    "SUPPORTED_FEATURES",
    "GraphDocument",
    "GraphDocumentError",
    "GraphReport",
    "load_graph",
    "validate_graph",
]
