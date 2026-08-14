from __future__ import annotations

import json
import sys
from pathlib import Path

from loro.provider_contracts import ProviderContractError, validate_provider_contracts

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    matrix_path = ROOT / "docs" / "interoperability-matrix.json"
    try:
        report = validate_provider_contracts(
            matrix_path,
            ROOT / "tests" / "fixtures" / "providers",
        )
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        for surface in ("mcp", "skills", "graphs", "gateways", "agent_profiles"):
            claim = matrix.get(surface)
            if not isinstance(claim, dict) or not claim.get("evidence"):
                raise ProviderContractError(f"{surface} must name conformance evidence")
            for evidence in claim["evidence"]:
                if not (ROOT / evidence).exists():
                    raise ProviderContractError(f"missing {surface} evidence path: {evidence}")
    except (OSError, json.JSONDecodeError, ProviderContractError) as error:
        print(f"Interoperability matrix invalid: {error}")
        return 1
    print(
        "Interoperability matrix OK: "
        f"{len(report.protocols)} provider protocols, {len(report.profiles)} profiles."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
