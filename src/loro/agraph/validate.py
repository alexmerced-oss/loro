from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loro.agraph.document import GraphDocument, GraphDocumentError, load_graph
from loro.agraph.reference_validator import Finding, Report, Validator
from loro.agraph.support import unsupported_feature_findings


@dataclass(frozen=True)
class GraphReport:
    path: Path
    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "error")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "ok": self.ok,
            "findings": [finding.__dict__ for finding in self.findings],
        }


def validate_graph(document: GraphDocument | str | Path) -> GraphReport:
    if not isinstance(document, GraphDocument):
        try:
            document = load_graph(document)
        except GraphDocumentError as error:
            return GraphReport(Path(document), (Finding("AG001", "error", str(error), ""),))
    report = Report(document.path)
    Validator(document.data, report).run()
    # Surface fields the schema accepts but the executor does not enforce, so a graph
    # never appears governed by a guarantee the run will not honor.
    report.findings.extend(unsupported_feature_findings(document.data))
    return GraphReport(document.path, tuple(report.findings))
