from __future__ import annotations

import json

from typer.testing import CliRunner

from loro import __version__
from loro.cli import app
from loro.config import LoroConfig
from loro.release_readiness import assess_release_readiness, load_release_contract


def test_bundled_release_contract_matches_runtime_and_freezes_cli() -> None:
    contract = load_release_contract()

    assert contract["package_version"] == __version__
    assert contract["release_line"] == "0.18"
    assert contract["stability"] == "stabilization"
    assert "release-readiness" in contract["cli"]["operations"]
    assert contract["schemas"]["configuration"] == "1.0"
    assert contract["external_gates"]


def test_release_readiness_is_content_free_and_separates_external_gates() -> None:
    report = assess_release_readiness(LoroConfig())
    payload = report.to_payload()

    assert report.ready is True
    assert payload["content_recorded"] is False
    assert payload["external_gates"]
    assert any(check["name"] == "release_contract" for check in payload["checks"])
    assert "prompt" not in json.dumps(payload).casefold()


def test_release_readiness_fails_missing_required_identity_and_invalid_audit() -> None:
    config = LoroConfig.model_validate(
        {
            "identity": {
                "environment_enabled": False,
                "required_fields": ["subject", "tenant"],
            },
            "audit": {"sink": "http", "http_url": None},
        }
    )

    report = assess_release_readiness(config)
    statuses = {check.name: check.status for check in report.checks}

    assert report.ready is False
    assert statuses["identity"] == "fail"
    assert statuses["audit"] == "fail"


def test_release_readiness_cli_supports_warning_gate(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("loro.cli.load_config", LoroConfig)
    runner = CliRunner()

    output = tmp_path / "readiness.json"
    normal = runner.invoke(app, ["operations", "release-readiness", "--output", str(output)])
    strict = runner.invoke(app, ["operations", "release-readiness", "--strict"])

    assert normal.exit_code == 0, normal.output
    assert strict.exit_code == 1
    assert '"content_recorded": false' in normal.output
    assert json.loads(output.read_text(encoding="utf-8"))["release_line"] == "0.18"
