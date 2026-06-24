import pytest

from loro.config import SafetyConfig
from loro.safety import SafetyScanner


def test_safety_scanner_detects_assignment_secret() -> None:
    scanner = SafetyScanner(SafetyConfig())
    findings = scanner.scan("api_key = 'abc123456789'")
    assert findings
    assert findings[0].kind == "assignment_secret"


def test_safety_scanner_can_be_disabled() -> None:
    scanner = SafetyScanner(SafetyConfig(enabled=False))
    assert scanner.scan("api_key = 'abc123456789'") == []


def test_safety_assert_safe_blocks() -> None:
    scanner = SafetyScanner(SafetyConfig())
    with pytest.raises(ValueError):
        scanner.assert_safe("password = 'abc123456789'")
