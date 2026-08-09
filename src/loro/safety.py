from loro.config import SafetyConfig
from loro.data_protection import DataFinding as SafetyFinding
from loro.data_protection import DataProtectionEngine


class SafetyScanner:
    """Detect obvious secrets before memory or artifact persistence."""

    def __init__(self, config: SafetyConfig) -> None:
        self.config = config
        self.engine = DataProtectionEngine(config)

    def scan(self, text: str) -> list[SafetyFinding]:
        return list(self.engine.evaluate(text, "artifact").findings)

    def assert_safe(self, text: str) -> list[SafetyFinding]:
        findings = self.scan(text)
        if findings and self.config.block_on_findings:
            kinds = ", ".join(sorted({finding.kind for finding in findings}))
            raise ValueError(f"Sensitive content detected: {kinds}")
        return findings
