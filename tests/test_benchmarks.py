from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loro.benchmarks import run_reference_benchmarks, write_benchmark_report
from loro.cli import app


def test_reference_benchmarks_are_content_free_and_serializable(tmp_path: Path) -> None:
    report = run_reference_benchmarks(iterations=2, warmup=0, workspace=tmp_path / "work")
    output = write_benchmark_report(report, tmp_path / "report.json")
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.0"
    assert payload["content_recorded"] is False
    assert payload["passed"] is True
    assert {result["name"] for result in payload["results"]} == {
        "audit_jsonl_delivery",
        "config_validation",
        "local_memory_search_101",
        "operational_metrics_update",
    }
    assert "reference retrieval marker" not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("iterations", "warmup", "message"),
    [(0, 0, "iterations"), (1, -1, "warmup")],
)
def test_reference_benchmark_rejects_invalid_counts(
    iterations: int, warmup: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        run_reference_benchmarks(iterations=iterations, warmup=warmup)


def test_operations_benchmark_cli_writes_evidence(tmp_path: Path) -> None:
    output = tmp_path / "benchmark.json"

    result = CliRunner().invoke(
        app,
        [
            "operations",
            "benchmark",
            "--iterations",
            "2",
            "--warmup",
            "0",
            "--strict",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is True
