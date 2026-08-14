from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loro.provider_profiles import PROVIDER_PROFILES

REQUIRED_CASES = frozenset(
    {"completion", "native_tools", "streaming", "usage", "malformed", "retryable_error"}
)


class ProviderContractError(ValueError):
    """Raised when advertised provider evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class ProviderContractReport:
    release_line: str
    protocols: tuple[str, ...]
    profiles: tuple[str, ...]
    fixtures: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "release_line": self.release_line,
            "protocols": list(self.protocols),
            "profiles": list(self.profiles),
            "fixtures": list(self.fixtures),
            "required_cases": sorted(REQUIRED_CASES),
        }


def default_contract_paths() -> tuple[Path, Path]:
    """Resolve repository contracts or their wheel-bundled copies."""
    repository = Path(__file__).resolve().parents[2]
    matrix = repository / "docs" / "interoperability-matrix.json"
    fixtures = repository / "tests" / "fixtures" / "providers"
    if matrix.is_file() and fixtures.is_dir():
        return matrix, fixtures
    bundled = Path(__file__).resolve().parent / "contracts"
    return bundled / "interoperability-matrix.json", bundled / "providers"


def validate_provider_contracts(
    matrix_path: Path,
    fixture_root: Path,
) -> ProviderContractReport:
    matrix = _object(matrix_path)
    provider_claim = matrix.get("providers")
    if not isinstance(provider_claim, dict):
        raise ProviderContractError("interoperability matrix requires a providers object")

    protocols = provider_claim.get("protocols")
    profiles = provider_claim.get("profiles")
    if not isinstance(protocols, dict) or not isinstance(profiles, dict):
        raise ProviderContractError("provider protocols and profiles must be objects")

    fixture_names: list[str] = []
    covered_protocols: set[str] = set()
    for protocol, claim in sorted(protocols.items()):
        if not isinstance(claim, dict):
            raise ProviderContractError(f"protocol claim must be an object: {protocol}")
        status = claim.get("status")
        if status not in {"supported", "experimental"}:
            raise ProviderContractError(f"invalid status for protocol {protocol}: {status}")
        fixture_name = claim.get("fixture")
        if not isinstance(fixture_name, str) or not fixture_name:
            raise ProviderContractError(f"protocol {protocol} does not name a fixture")
        fixture = _object(fixture_root / fixture_name)
        if fixture.get("sanitized") is not True:
            raise ProviderContractError(f"provider fixture is not marked sanitized: {fixture_name}")
        if fixture.get("protocol") != protocol:
            raise ProviderContractError(f"provider fixture protocol mismatch: {fixture_name}")
        cases = fixture.get("cases")
        if not isinstance(cases, dict):
            raise ProviderContractError(f"provider fixture cases must be an object: {fixture_name}")
        missing = REQUIRED_CASES - cases.keys()
        if missing:
            raise ProviderContractError(
                f"provider fixture {fixture_name} misses cases: {', '.join(sorted(missing))}"
            )
        fixture_names.append(fixture_name)
        covered_protocols.add(protocol)

    actual_profiles = {name: profile.protocol for name, profile in PROVIDER_PROFILES.items()}
    if profiles != actual_profiles:
        missing = sorted(set(actual_profiles) - set(profiles))
        extra = sorted(set(profiles) - set(actual_profiles))
        changed = sorted(
            name
            for name in set(profiles) & set(actual_profiles)
            if profiles[name] != actual_profiles[name]
        )
        raise ProviderContractError(
            "provider profile matrix drift "
            f"(missing={missing}, extra={extra}, protocol_mismatch={changed})"
        )

    claimed_profiles = sorted(
        name for name, protocol in actual_profiles.items() if protocol in covered_protocols
    )
    return ProviderContractReport(
        release_line=str(matrix.get("release_line", "")),
        protocols=tuple(sorted(covered_protocols)),
        profiles=tuple(claimed_profiles),
        fixtures=tuple(fixture_names),
    )


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProviderContractError(f"cannot read contract JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise ProviderContractError(f"contract JSON must contain an object: {path}")
    return value
