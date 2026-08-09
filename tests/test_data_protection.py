import json

import pytest

from loro.audit import AuditLogger
from loro.config import (
    AuditConfig,
    DataProtectionPatternConfig,
    SafetyConfig,
)
from loro.data_protection import DataFinding, DataProtectionEngine


def test_persistence_blocks_restricted_finding() -> None:
    engine = DataProtectionEngine(SafetyConfig())

    decision = engine.evaluate("api_key = 'abcdefghijk'", "memory_local")

    assert decision.blocked is True
    assert decision.classification == "restricted"
    assert decision.metadata()["finding_kinds"] == ["assignment_secret"]
    with pytest.raises(ValueError, match="memory_local"):
        engine.enforce("api_key = 'abcdefghijk'", "memory_local")


def test_output_redaction_merges_overlapping_findings() -> None:
    engine = DataProtectionEngine(SafetyConfig())

    decision = engine.evaluate("token=ghp_abcdefghijklmnopqrstuvwxyz1234", "tool_output")

    assert decision.redacted is True
    assert decision.content == "[redacted]"
    assert {finding.kind for finding in decision.findings} == {
        "assignment_secret",
        "github_token",
    }


def test_classification_label_applies_surface_ceiling() -> None:
    engine = DataProtectionEngine(SafetyConfig())

    decision = engine.evaluate("[classification: restricted] payroll", "audit")

    assert decision.classification == "restricted"
    assert decision.redacted is True
    assert decision.content == "[redacted]"


def test_managed_policy_can_disable_sensitive_override() -> None:
    engine = DataProtectionEngine(SafetyConfig(allow_sensitive_override=False))

    decision = engine.evaluate("password=abcdefghijk", "artifact", allow_sensitive=True)

    assert decision.blocked is True


def test_legacy_finding_block_switch_remains_compatible() -> None:
    engine = DataProtectionEngine(SafetyConfig(block_on_findings=False))

    decision = engine.evaluate("password=abcdefghijk", "artifact")

    assert decision.action == "allow"
    assert decision.classification == "restricted"


def test_partial_surface_configuration_inherits_defaults() -> None:
    config = SafetyConfig.model_validate(
        {"surfaces": {"model_input": {"maximum_classification": "internal"}}}
    )

    assert config.surfaces["model_input"].maximum_classification == "internal"
    assert config.surfaces["artifact"].action == "block"


def test_surface_allowlist_ignores_only_named_finding_kind() -> None:
    config = SafetyConfig.model_validate(
        {
            "surfaces": {"artifact": {"allowed_finding_kinds": ["internal_case_id"]}},
            "custom_patterns": [
                {
                    "kind": "internal_case_id",
                    "pattern": "CASE-[0-9]{5}",
                    "classification": "confidential",
                }
            ],
        }
    )

    allowed = DataProtectionEngine(config).evaluate("CASE-12345", "artifact")
    blocked = DataProtectionEngine(config).evaluate("password=abcdefghijk", "artifact")

    assert allowed.action == "allow"
    assert allowed.findings == ()
    assert allowed.metadata()["ignored_finding_kinds"] == ["internal_case_id"]
    assert blocked.blocked is True


def test_custom_patterns_and_pluggable_scanners() -> None:
    class EmployeeIdScanner:
        def scan(self, text: str) -> list[DataFinding]:
            return [
                DataFinding(
                    kind="employee_id",
                    classification="confidential",
                    snippet="[redacted]",
                    start=0,
                    end=len(text),
                )
            ]

    config = SafetyConfig(
        custom_patterns=[
            DataProtectionPatternConfig(
                kind="case_id", pattern=r"CASE-\d{5}", classification="confidential"
            )
        ]
    )
    custom = DataProtectionEngine(config).evaluate("CASE-12345", "artifact")
    plugged = DataProtectionEngine(config, scanners=[EmployeeIdScanner()]).evaluate(
        "E-123", "tool_output"
    )

    assert custom.findings[0].kind == "case_id"
    assert custom.blocked is True
    assert plugged.content == "[redacted]"


def test_audit_recursively_redacts_sensitive_values(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(AuditConfig(path=str(path)), safety_config=SafetyConfig())

    logger.write("test.event", nested={"credential": "token=abcdefghijk"})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["details"]["nested"]["credential"] == "[redacted]"
    assert payload["redaction"]["method"] == "managed-data-protection"
    assert payload["redaction"]["fields"] == ["details.nested.credential"]


def test_unknown_surface_configuration_fails_closed() -> None:
    config = SafetyConfig()
    del config.surfaces["model_input"]
    engine = DataProtectionEngine(config)

    with pytest.raises(ValueError, match="model_input"):
        engine.evaluate("hello", "model_input")
