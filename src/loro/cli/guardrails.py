"""`loro safety|sandbox|policy|identity`: guardrail inspection commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from loro.cli._common import _audit, _data_protection, _identity, _permissions, console
from loro.config import (
    load_config,
)
from loro.identity import (
    diagnose_identity,
)
from loro.permissions import PermissionRequest
from loro.resources import (
    resource_from_payload,
)
from loro.sandbox import SandboxRunner

safety_app = typer.Typer(help="Scan content for obvious secrets.")


identity_app = typer.Typer(help="Inspect and validate the active enterprise identity.")


policy_app = typer.Typer(help="Explain normalized permission decisions.")


sandbox_app = typer.Typer(help="Inspect subprocess isolation profiles.")


@policy_app.command("explain")
def policy_explain(
    request_json: Annotated[
        str,
        typer.Argument(
            help=("JSON request with tool, action, optional target, and optional resource.")
        ),
    ],
) -> None:
    """Explain the policy decision for a normalized request fixture."""
    try:
        fixture = json.loads(request_json)
    except json.JSONDecodeError as error:
        raise typer.BadParameter(f"Invalid request JSON: {error.msg}") from error
    if not isinstance(fixture, dict):
        raise typer.BadParameter("Policy request fixture must be a JSON object.")
    tool = fixture.get("tool")
    action = fixture.get("action")
    if not isinstance(tool, str) or not tool.strip():
        raise typer.BadParameter("Policy request requires a non-empty tool.")
    if not isinstance(action, str) or not action.strip():
        raise typer.BadParameter("Policy request requires a non-empty action.")
    resource_payload = fixture.get("resource")
    resource = None
    if resource_payload is not None:
        if not isinstance(resource_payload, dict):
            raise typer.BadParameter("Policy request resource must be a JSON object.")
        if isinstance(resource_payload.get("fields"), dict):
            resource_payload = {
                "kind": resource_payload.get("kind"),
                **resource_payload["fields"],
            }
        try:
            resource = resource_from_payload(resource_payload)
        except PermissionError as error:
            raise typer.BadParameter(str(error)) from error
    target = fixture.get("target")
    if target is not None and not isinstance(target, str):
        raise typer.BadParameter("Policy request target must be a string.")
    result = _permissions().evaluate(
        PermissionRequest(
            tool=tool.strip(),
            action=action.strip(),
            target=target,
            resource=resource,
        )
    )
    console.print_json(
        data={
            "decision": result.decision,
            "reason": result.reason,
            "policy_version": result.policy_version,
            "policy_source": result.policy_source,
            "matched_rule": result.matched_rule,
            "normalized_resource": resource.to_payload() if resource else None,
        }
    )


@identity_app.command("show")
def identity_show() -> None:
    """Show the resolved identity context without exposing credentials."""
    console.print_json(data=_identity().to_payload())


@identity_app.command("doctor")
def identity_doctor() -> None:
    """Check whether the resolved identity satisfies required fields."""
    diagnostic = diagnose_identity(load_config().identity)
    console.print_json(data=diagnostic.to_payload())
    raise typer.Exit(code=0 if diagnostic.ok else 1)


@sandbox_app.command("doctor")
def sandbox_doctor() -> None:
    """Report whether configured subprocess isolation profiles can be enforced."""
    config = load_config()
    report = SandboxRunner(
        config.sandbox,
        workspace_roots=config.permissions.workspace_roots,
    ).diagnose()
    console.print_json(data=report)
    profiles = report["profiles"]
    if isinstance(profiles, dict) and not all(
        isinstance(profile, dict) and profile.get("ready") for profile in profiles.values()
    ):
        raise typer.Exit(code=1)


@safety_app.command("scan")
def safety_scan(
    text: Annotated[str | None, typer.Argument(help="Text to scan.")] = None,
    file: Annotated[Path | None, typer.Option("--file", "-f", help="File to scan.")] = None,
    surface: Annotated[
        str, typer.Option("--surface", help="Managed content surface to evaluate.")
    ] = "artifact",
) -> None:
    """Classify content and evaluate its managed data-protection policy."""
    if text is None and file is None:
        raise typer.BadParameter("Provide text or --file.")
    content = file.read_text(encoding="utf-8") if file else text or ""
    if surface not in load_config().safety.surfaces:
        raise typer.BadParameter(f"Unknown data-protection surface: {surface}")
    decision = _data_protection().evaluate(content, surface)  # type: ignore[arg-type]
    findings = list(decision.findings)
    _audit().write(
        "safety.scan",
        source=str(file) if file else "argument",
        finding_count=len(findings),
        finding_kinds=sorted({finding.kind for finding in findings}),
        data_protection=decision.metadata(),
    )
    console.print(
        f"Classification: {decision.classification}; action: {decision.action}; "
        f"surface: {decision.surface}."
    )
    if not findings:
        console.print("No sensitive patterns detected.")
        if decision.blocked:
            raise typer.Exit(code=1)
        return
    for finding in findings:
        console.print(
            f"- {finding.kind}: {finding.snippet} ({finding.start}-{finding.end})",
            markup=False,
        )
    raise typer.Exit(code=1)


@safety_app.command("doctor")
def safety_doctor() -> None:
    """Report the effective managed data-protection policy."""
    config = load_config().safety
    console.print_json(
        data={
            "enabled": config.enabled,
            "default_classification": config.default_classification,
            "allow_sensitive_override": config.allow_sensitive_override,
            "custom_pattern_count": len(config.custom_patterns),
            "surfaces": {
                name: policy.model_dump() for name, policy in sorted(config.surfaces.items())
            },
        }
    )
