from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

from loro.config import DataProtectionPatternConfig, SafetyConfig

Classification = Literal["public", "internal", "confidential", "restricted"]
DataSurface = Literal[
    "model_input",
    "model_output",
    "memory_local",
    "memory_shared",
    "artifact",
    "session",
    "session_message",
    "tool_output",
    "audit",
]
DecisionAction = Literal["allow", "redact", "block"]

CLASSIFICATION_ORDER: dict[Classification, int] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


@dataclass(frozen=True)
class DataFinding:
    kind: str
    classification: Classification
    snippet: str
    start: int
    end: int


class ContentScanner(Protocol):
    def scan(self, text: str) -> list[DataFinding]: ...


@dataclass(frozen=True)
class DataProtectionDecision:
    surface: DataSurface
    classification: Classification
    maximum_classification: Classification
    action: DecisionAction
    findings: tuple[DataFinding, ...]
    ignored_findings: tuple[DataFinding, ...]
    content: str
    policy_reason: str

    @property
    def blocked(self) -> bool:
        return self.action == "block"

    @property
    def redacted(self) -> bool:
        return self.action == "redact"

    def metadata(self) -> dict[str, object]:
        return {
            "surface": self.surface,
            "classification": self.classification,
            "maximum_classification": self.maximum_classification,
            "action": self.action,
            "finding_count": len(self.findings),
            "finding_kinds": sorted({finding.kind for finding in self.findings}),
            "ignored_finding_count": len(self.ignored_findings),
            "ignored_finding_kinds": sorted({finding.kind for finding in self.ignored_findings}),
        }


class RegexContentScanner:
    BUILTIN_PATTERNS: tuple[tuple[str, str, Classification], ...] = (
        ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "restricted"),
        (
            "assignment_secret",
            r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[^'\"\s]{8,}",
            "restricted",
        ),
        ("github_token", r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", "restricted"),
        ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b", "restricted"),
    )

    def __init__(
        self,
        custom_patterns: list[DataProtectionPatternConfig] | None = None,
        *,
        redaction_text: str = "[redacted]",
    ) -> None:
        patterns = [
            (kind, re.compile(pattern), classification)
            for kind, pattern, classification in self.BUILTIN_PATTERNS
        ]
        patterns.extend(
            (item.kind, re.compile(item.pattern), item.classification)
            for item in custom_patterns or []
        )
        self.patterns = patterns
        self.redaction_text = redaction_text

    def scan(self, text: str) -> list[DataFinding]:
        findings: list[DataFinding] = []
        for kind, pattern, classification in self.patterns:
            for match in pattern.finditer(text):
                findings.append(
                    DataFinding(
                        kind=kind,
                        classification=classification,
                        snippet=_preview(match.group(0), self.redaction_text),
                        start=match.start(),
                        end=match.end(),
                    )
                )
        return sorted(findings, key=lambda finding: (finding.start, finding.end))


class DataProtectionEngine:
    """Apply one managed classification and DLP contract across content surfaces."""

    LABEL_PATTERN = re.compile(
        r"(?i)\[\s*classification\s*:\s*(public|internal|confidential|restricted)\s*\]"
    )

    def __init__(
        self,
        config: SafetyConfig,
        scanners: list[ContentScanner] | None = None,
    ) -> None:
        self.config = config
        self.scanners = scanners or [
            RegexContentScanner(config.custom_patterns, redaction_text=config.redaction_text)
        ]

    def evaluate(
        self,
        text: str,
        surface: DataSurface,
        *,
        classification: Classification | None = None,
        allow_sensitive: bool = False,
    ) -> DataProtectionDecision:
        surface_policy = self.config.surfaces.get(surface)
        if surface_policy is None:
            raise ValueError(f"No data-protection policy is configured for surface: {surface}")
        if not self.config.enabled:
            return DataProtectionDecision(
                surface=surface,
                classification=classification or self.config.default_classification,
                maximum_classification=surface_policy.maximum_classification,
                action="allow",
                findings=(),
                ignored_findings=(),
                content=text,
                policy_reason="data protection disabled",
            )

        scanned = tuple(finding for scanner in self.scanners for finding in scanner.scan(text))
        allowed_kinds = set(surface_policy.allowed_finding_kinds)
        ignored_findings = tuple(finding for finding in scanned if finding.kind in allowed_kinds)
        findings = tuple(finding for finding in scanned if finding.kind not in allowed_kinds)
        declared = classification or self._label(text) or self.config.default_classification
        detected = declared
        for finding in findings:
            if CLASSIFICATION_ORDER[finding.classification] > CLASSIFICATION_ORDER[detected]:
                detected = finding.classification
        declared_exceeds_ceiling = (
            CLASSIFICATION_ORDER[declared]
            > CLASSIFICATION_ORDER[surface_policy.maximum_classification]
        )
        finding_triggered = bool(findings) and self.config.block_on_findings
        exceeds_ceiling = declared_exceeds_ceiling or (
            finding_triggered
            and CLASSIFICATION_ORDER[detected]
            > CLASSIFICATION_ORDER[surface_policy.maximum_classification]
        )
        triggered = finding_triggered or exceeds_ceiling
        if allow_sensitive and self.config.allow_sensitive_override:
            action: DecisionAction = "allow"
            reason = "explicit development override"
        elif triggered:
            action = surface_policy.action
            reason = "findings detected" if findings else "classification exceeds surface ceiling"
        else:
            action = "allow"
            reason = "within surface policy"
        content = self._redact(text, findings) if action == "redact" else text
        return DataProtectionDecision(
            surface=surface,
            classification=detected,
            maximum_classification=surface_policy.maximum_classification,
            action=action,
            findings=findings,
            ignored_findings=ignored_findings,
            content=content,
            policy_reason=reason,
        )

    def enforce(
        self,
        text: str,
        surface: DataSurface,
        *,
        classification: Classification | None = None,
        allow_sensitive: bool = False,
    ) -> DataProtectionDecision:
        decision = self.evaluate(
            text,
            surface,
            classification=classification,
            allow_sensitive=allow_sensitive,
        )
        if decision.blocked:
            kinds = sorted({finding.kind for finding in decision.findings})
            detail = ", ".join(kinds) if kinds else decision.classification
            raise ValueError(f"Data protection blocked {surface}: {detail}")
        return decision

    def _label(self, text: str) -> Classification | None:
        labels = [match.group(1).lower() for match in self.LABEL_PATTERN.finditer(text)]
        if not labels:
            return None
        return max(labels, key=lambda item: CLASSIFICATION_ORDER[item])  # type: ignore[index,return-value]

    def _redact(self, text: str, findings: tuple[DataFinding, ...]) -> str:
        if not findings:
            return self.config.redaction_text
        intervals: list[tuple[int, int]] = []
        for finding in sorted(findings, key=lambda item: (item.start, item.end)):
            if intervals and finding.start <= intervals[-1][1]:
                intervals[-1] = (intervals[-1][0], max(intervals[-1][1], finding.end))
            else:
                intervals.append((finding.start, finding.end))
        redacted = text
        for start, end in reversed(intervals):
            redacted = redacted[:start] + self.config.redaction_text + redacted[end:]
        return redacted


def _preview(value: str, redaction_text: str) -> str:
    if len(value) <= 12:
        return redaction_text
    return f"{value[:4]}...{redaction_text}...{value[-4:]}"
