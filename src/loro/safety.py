import re
from dataclasses import dataclass

from loro.config import SafetyConfig


@dataclass(frozen=True)
class SafetyFinding:
    kind: str
    snippet: str
    start: int
    end: int


class SafetyScanner:
    """Detect obvious secrets before memory or artifact persistence."""

    PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
        (
            "assignment_secret",
            re.compile(
                r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[^'\"\s]{8,}"
            ),
        ),
        ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
        ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    )

    def __init__(self, config: SafetyConfig) -> None:
        self.config = config

    def scan(self, text: str) -> list[SafetyFinding]:
        if not self.config.enabled:
            return []
        findings: list[SafetyFinding] = []
        for kind, pattern in self.PATTERNS:
            for match in pattern.finditer(text):
                findings.append(
                    SafetyFinding(
                        kind=kind,
                        snippet=self._redact(match.group(0)),
                        start=match.start(),
                        end=match.end(),
                    )
                )
        return findings

    def assert_safe(self, text: str) -> list[SafetyFinding]:
        findings = self.scan(text)
        if findings and self.config.block_on_findings:
            kinds = ", ".join(sorted({finding.kind for finding in findings}))
            raise ValueError(f"Sensitive content detected: {kinds}")
        return findings

    def _redact(self, value: str) -> str:
        if len(value) <= 12:
            return "[redacted]"
        return f"{value[:4]}...[redacted]...{value[-4:]}"
