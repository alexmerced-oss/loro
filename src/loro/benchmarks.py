from __future__ import annotations

import json
import platform
import statistics
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from loro import __version__
from loro.audit import AuditLogger
from loro.audit.metrics import OperationalMetrics
from loro.config import AuditConfig, LoroConfig
from loro.memory.local import LocalMemoryStore

BENCHMARK_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    iterations: int
    median_ms: float
    p95_ms: float
    maximum_ms: float
    target_p95_ms: float
    passed: bool


@dataclass(frozen=True)
class BenchmarkReport:
    schema_version: str
    loro_version: str
    generated_at: str
    environment: dict[str, str]
    content_recorded: bool
    passed: bool
    results: tuple[BenchmarkResult, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "results": [asdict(result) for result in self.results],
        }


def run_reference_benchmarks(
    *,
    iterations: int = 25,
    warmup: int = 3,
    workspace: Path | None = None,
) -> BenchmarkReport:
    """Run deterministic local baselines without prompts, providers, or remote services."""

    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if warmup < 0:
        raise ValueError("warmup cannot be negative")

    if workspace is None:
        with tempfile.TemporaryDirectory(prefix="loro-benchmark-") as temporary:
            return _run_suite(Path(temporary), iterations=iterations, warmup=warmup)
    workspace.mkdir(parents=True, exist_ok=True)
    return _run_suite(workspace, iterations=iterations, warmup=warmup)


def write_benchmark_report(report: BenchmarkReport, output: Path) -> Path:
    destination = output.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report.to_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def _run_suite(workspace: Path, *, iterations: int, warmup: int) -> BenchmarkReport:
    memory = LocalMemoryStore(workspace / "memory")
    for index in range(100):
        memory.remember(f"reference memory record {index:03d}")
    memory.remember("reference retrieval marker")

    audit = AuditLogger(AuditConfig(path=str(workspace / "audit.jsonl")))
    metrics = OperationalMetrics(workspace / "metrics.json")
    scenarios: tuple[tuple[str, Callable[[], object], float], ...] = (
        ("config_validation", lambda: LoroConfig.model_validate({}), 100.0),
        ("local_memory_search_101", lambda: memory.search("retrieval marker"), 100.0),
        (
            "audit_jsonl_delivery",
            lambda: audit.write("runtime.benchmark_sample", duration_ms=1),
            250.0,
        ),
        (
            "operational_metrics_update",
            lambda: metrics.observe(
                "runtime.task_completed",
                {"duration_ms": 1, "status": "completed"},
                delivery_status="delivered",
            ),
            250.0,
        ),
    )
    results = tuple(
        _measure(name, operation, target, iterations=iterations, warmup=warmup)
        for name, operation, target in scenarios
    )
    return BenchmarkReport(
        schema_version=BENCHMARK_SCHEMA_VERSION,
        loro_version=__version__,
        generated_at=datetime.now(UTC).isoformat(),
        environment={
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "operating_system": platform.system().lower(),
            "architecture": platform.machine().lower(),
        },
        content_recorded=False,
        passed=all(result.passed for result in results),
        results=results,
    )


def _measure(
    name: str,
    operation: Callable[[], object],
    target_p95_ms: float,
    *,
    iterations: int,
    warmup: int,
) -> BenchmarkResult:
    for _ in range(warmup):
        operation()
    durations: list[float] = []
    for _ in range(iterations):
        started = perf_counter_ns()
        operation()
        durations.append((perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(durations)
    p95_index = max(0, min(len(ordered) - 1, (95 * len(ordered) + 99) // 100 - 1))
    p95 = ordered[p95_index]
    return BenchmarkResult(
        name=name,
        iterations=iterations,
        median_ms=round(statistics.median(durations), 3),
        p95_ms=round(p95, 3),
        maximum_ms=round(max(durations), 3),
        target_p95_ms=target_p95_ms,
        passed=p95 <= target_p95_ms,
    )


__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "BenchmarkReport",
    "BenchmarkResult",
    "run_reference_benchmarks",
    "write_benchmark_report",
]
