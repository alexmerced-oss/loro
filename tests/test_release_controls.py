from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from loro.audit.inventory import AUDIT_EVENT_FAMILIES, audit_event_family

ROOT = Path(__file__).resolve().parents[1]


def test_audit_family_prefixes_are_unique_and_classify_expected_events() -> None:
    prefixes = [family.prefix for family in AUDIT_EVENT_FAMILIES]

    assert len(prefixes) == len(set(prefixes))
    assert audit_event_family("approval.used").consequential is True
    assert audit_event_family("provider.smoke").consequential is False
    assert audit_event_family("unknown.event") is None


def test_audit_source_and_enterprise_evidence_checks_pass() -> None:
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    for script in (
        "check_audit_inventory.py",
        "check_data_support_matrix.py",
        "check_enterprise_evidence.py",
        "check_interoperability_matrix.py",
        "check_oap_conformance.py",
        "check_reference_deployment.py",
    ):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script)],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    contract = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_release_contract.py"),
            "--check",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert contract.returncode == 0, contract.stdout + contract.stderr


def test_machine_readable_support_matrix_has_explicit_stability() -> None:
    matrix = json.loads((ROOT / "docs" / "support-matrix.json").read_text(encoding="utf-8"))

    assert matrix["schema_version"] == "1.0"
    assert matrix["release_line"] == "0.12"
    assert matrix["stability"] == "stabilization"
    assert "linux" in matrix["operating_systems"]["supported"]
    assert "iceberg" in matrix["memory_backends"]["experimental"]
    assert matrix["known_limitations"]


def test_release_manifest_hashes_actual_artifacts(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    artifact = dist / "loro_agent-0.12.0-py3-none-any.whl"
    artifact.write_bytes(b"fixture-wheel")
    output = dist / "release-manifest.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_release_manifest.py"),
            "--dist",
            str(dist),
            "--output",
            str(output),
            "--commit",
            "a" * 40,
            "--workflow-run",
            "fixture-run",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["commit"] == "a" * 40
    assert manifest["workflow_run"] == "fixture-run"
    assert manifest["data_support_matrix"]["release_line"] == "0.12"
    assert manifest["interoperability_matrix"]["release_line"] == "0.12"
    assert manifest["release_contract"]["release_line"] == "0.12"
    assert manifest["artifacts"] == [
        {
            "bytes": len(b"fixture-wheel"),
            "name": artifact.name,
            "sha256": "sha256:f36b78f6b00e4ffc0ca0ae15c97cc8653705681563fe446176452b3464f11b33",
        }
    ]
