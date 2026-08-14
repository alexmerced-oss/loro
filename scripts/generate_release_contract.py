# ruff: noqa: E402
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from typer.main import get_command

from loro import __version__
from loro.approvals import JsonApprovalStore
from loro.audit.metrics import METRICS_SCHEMA_VERSION
from loro.audit.schema import AUDIT_SCHEMA_VERSION
from loro.benchmarks import BENCHMARK_SCHEMA_VERSION
from loro.cli import app
from loro.config import CONFIG_SCHEMA_VERSION, MCPServerConfig
from loro.memory.migrations import LATEST_POSTGRES_MEMORY_SCHEMA_VERSION
from loro.recovery import RECOVERY_SCHEMA_VERSION

OUTPUT = ROOT / "docs" / "release-contract.json"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def build_contract() -> dict[str, object]:
    support_path = ROOT / "docs" / "support-matrix.json"
    data_path = ROOT / "docs" / "data-support-matrix.json"
    interop_path = ROOT / "docs" / "interoperability-matrix.json"
    oap_path = ROOT / "docs" / "oap-conformance.json"
    deployment_path = ROOT / "deploy" / "reference" / "manifest.json"
    managed_policy_path = ROOT / "deploy" / "reference" / "managed.toml"
    compose_path = ROOT / "deploy" / "reference" / "compose.yaml"
    support = json.loads(support_path.read_text(encoding="utf-8"))
    interoperability = json.loads(interop_path.read_text(encoding="utf-8"))
    root = get_command(app)
    mcp_protocol_factory = MCPServerConfig.model_fields[
        "allowed_protocol_versions"
    ].default_factory
    if mcp_protocol_factory is None:
        raise RuntimeError("MCP protocol defaults are not declared.")
    command_map = {
        name: sorted(getattr(command, "commands", {}))
        for name, command in sorted(root.commands.items())
    }
    return {
        "schema_version": "1.0",
        "release_line": ".".join(__version__.split(".")[:2]),
        "package_version": __version__,
        "stability": "stabilization",
        "change_policy": "stable-core-preserved-experimental-oap-addition",
        "cli": command_map,
        "schemas": {
            "approval": JsonApprovalStore.SCHEMA_VERSION,
            "audit": AUDIT_SCHEMA_VERSION,
            "benchmark": BENCHMARK_SCHEMA_VERSION,
            "configuration": CONFIG_SCHEMA_VERSION,
            "metrics": METRICS_SCHEMA_VERSION,
            "open_agent_profile": "1.0-provisional",
            "postgres_memory": LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
            "recovery": RECOVERY_SCHEMA_VERSION,
        },
        "protocols": {
            "agentic_graph": "1.0",
            "mcp": mcp_protocol_factory(),
            "open_agent_profile": "1.0-provisional-level-3",
            "provider": sorted(interoperability["providers"]["protocols"]),
        },
        "supported": {
            key: value["supported"]
            for key, value in support.items()
            if isinstance(value, dict) and "supported" in value
        },
        "experimental": {
            key: value["experimental"]
            for key, value in support.items()
            if isinstance(value, dict) and "experimental" in value
        },
        "digests": {
            "data_support_matrix": _sha256(data_path),
            "interoperability_matrix": _sha256(interop_path),
            "oap_conformance": _sha256(oap_path),
            "reference_compose": _sha256(compose_path),
            "reference_deployment": _sha256(deployment_path),
            "reference_managed_policy": _sha256(managed_policy_path),
            "support_matrix": _sha256(support_path),
        },
        "external_gates": [
            "accountable-owner-and-on-call-assignment",
            "controlled-pilot-completion",
            "corporate-identity-and-policy-validation",
            "independent-penetration-test",
            "legal-privacy-data-and-security-approval",
            "production-backup-restore-and-disaster-recovery",
            "production-provider-audit-and-gateway-evidence",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify the frozen release contract.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(build_contract(), indent=2, sort_keys=True) + "\n"
    if args.write:
        OUTPUT.write_text(expected, encoding="utf-8")
        print(f"Wrote {OUTPUT.relative_to(ROOT)}")
        return 0
    actual = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
    if actual != expected:
        print("Release contract drifted; review the change and regenerate with --write.")
        return 1
    print("Release contract OK: frozen CLI, schemas, protocols, matrices, and deployment agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
