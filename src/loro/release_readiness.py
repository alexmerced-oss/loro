from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from loro import __version__
from loro.audit import AuditLogger
from loro.config import LoroConfig
from loro.config_check import check_config
from loro.identity import diagnose_identity
from loro.models import smoke_model_client
from loro.sandbox import SandboxRunner

ReadinessStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: ReadinessStatus
    detail: str


@dataclass(frozen=True)
class ReleaseReadinessReport:
    schema_version: str
    loro_version: str
    release_line: str
    ready: bool
    checks: tuple[ReadinessCheck, ...]
    external_gates: tuple[str, ...]
    content_recorded: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "checks": [asdict(check) for check in self.checks],
            "external_gates": list(self.external_gates),
        }


def load_release_contract() -> dict[str, Any]:
    resource = files("loro").joinpath("contracts/release-contract.json")
    if resource.is_file():
        raw = resource.read_text(encoding="utf-8")
    else:
        raw = (Path(__file__).resolve().parents[2] / "docs" / "release-contract.json").read_text(
            encoding="utf-8"
        )
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise RuntimeError("Unsupported bundled release contract.")
    return payload


def assess_release_readiness(config: LoroConfig) -> ReleaseReadinessReport:
    contract = load_release_contract()
    checks: list[ReadinessCheck] = []
    expected_version = contract.get("package_version")
    checks.append(
        ReadinessCheck(
            "release_contract",
            "pass" if expected_version == __version__ else "fail",
            f"bundled contract version {expected_version}; runtime version {__version__}",
        )
    )

    findings = check_config(config)
    errors = [finding for finding in findings if finding.severity == "error"]
    warnings = [finding for finding in findings if finding.severity == "warning"]
    config_status: ReadinessStatus = "fail" if errors else "warn" if warnings else "pass"
    checks.append(
        ReadinessCheck(
            "configuration",
            config_status,
            f"{len(errors)} error(s), {len(warnings)} warning(s)",
        )
    )

    identity = diagnose_identity(config.identity)
    checks.append(
        ReadinessCheck(
            "identity",
            "pass" if identity.ok else "fail",
            "required identity fields resolved"
            if identity.ok
            else "missing required fields: " + ", ".join(identity.missing_fields),
        )
    )

    sandbox = SandboxRunner(config.sandbox).diagnose()
    profiles = sandbox.get("profiles", {})
    unready = sorted(name for name, item in profiles.items() if not item.get("ready"))
    process_only = sorted(
        name for name, item in profiles.items() if not item.get("filesystem_os_enforced", False)
    )
    sandbox_status: ReadinessStatus = "fail" if unready else "warn" if process_only else "pass"
    sandbox_detail = (
        "unready profiles: " + ", ".join(unready)
        if unready
        else "process-only profiles: " + ", ".join(process_only)
        if process_only
        else "all selected profiles are OS-enforced"
    )
    checks.append(ReadinessCheck("sandbox", sandbox_status, sandbox_detail))

    try:
        audit = AuditLogger(config.audit, safety_config=config.safety).doctor()
    except (OSError, RuntimeError) as error:
        checks.append(ReadinessCheck("audit", "fail", f"audit diagnostics failed: {error}"))
    else:
        checks.append(
            ReadinessCheck(
                "audit",
                "pass" if audit["ok"] else "fail",
                "; ".join(audit["issues"]) or f"sink {audit['sink']} is configured",
            )
        )

    try:
        provider = smoke_model_client(config.model, execute=False)
    except Exception as error:  # noqa: BLE001 - report a diagnostic without aborting other checks
        checks.append(ReadinessCheck("provider", "fail", str(error)))
    else:
        checks.append(
            ReadinessCheck(
                "provider",
                "pass",
                f"{provider['provider']} / {provider['model']} request builds cleanly",
            )
        )

    if config.memory.shared.enabled:
        checks.append(
            ReadinessCheck(
                "shared_memory",
                "warn",
                f"{config.memory.shared.backend} requires controlled live backend evidence",
            )
        )
    else:
        checks.append(ReadinessCheck("shared_memory", "pass", "shared memory is disabled"))

    return ReleaseReadinessReport(
        schema_version="1.0",
        loro_version=__version__,
        release_line=str(contract["release_line"]),
        ready=not any(check.status == "fail" for check in checks),
        checks=tuple(checks),
        external_gates=tuple(str(item) for item in contract.get("external_gates", [])),
    )


__all__ = [
    "ReadinessCheck",
    "ReleaseReadinessReport",
    "assess_release_readiness",
    "load_release_contract",
]
