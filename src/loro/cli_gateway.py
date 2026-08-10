from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from loro.config import (
    GatewayEndpointConfig,
    GatewayIdentityConfig,
    load_config,
    write_config_sections,
)
from loro.credentials import CredentialError, CredentialReference, CredentialVault
from loro.gateway.service import serve_gateway

gateway_app = typer.Typer(help="Configure and run authenticated remote chat gateways.")
console = Console()

_REQUIRED_CREDENTIALS = {
    "slack": ("signing-secret", "bot-token"),
    "discord": ("public-key",),
    "telegram": ("webhook-secret", "bot-token"),
    "teams": ("signing-secret", "outbound-url"),
    "signal": ("signing-secret", "outbound-url"),
    "generic": ("signing-secret", "outbound-url"),
}


@gateway_app.command("setup")
def gateway_setup(
    endpoint_id: Annotated[str | None, typer.Option("--id", help="Stable endpoint id.")] = None,
    platform: Annotated[str | None, typer.Option("--platform", help="Chat platform.")] = None,
    route: Annotated[str | None, typer.Option("--route", help="Inbound HTTP route.")] = None,
    user_id: Annotated[
        str | None, typer.Option("--user-id", help="Allowed platform user id.")
    ] = None,
    subject: Annotated[
        str | None, typer.Option("--subject", help="Trusted Loro identity subject.")
    ] = None,
    tenant: Annotated[str | None, typer.Option("--tenant", help="Trusted tenant id.")] = None,
    channel: Annotated[
        str | None, typer.Option("--channel", help="Allowed channel id. Omit for any channel.")
    ] = None,
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", help="Allowed workspace, server, tenant, or chat id."),
    ] = None,
    credential: Annotated[
        list[str] | None,
        typer.Option("--credential", help="Credential mapping name=vault://...; repeatable."),
    ] = None,
    output: Annotated[Path, typer.Option("--output", "-o", help="Config file to update.")] = Path(
        ".loro/config.local.toml"
    ),
) -> None:
    """Configure one gateway endpoint without storing secret values in TOML."""
    interactive = endpoint_id is None
    endpoint_id = endpoint_id or typer.prompt("Endpoint id", default="remote")
    platform = platform or typer.prompt(
        "Platform", default="slack", type=typer.Choice(sorted(_REQUIRED_CREDENTIALS))
    )
    if platform not in _REQUIRED_CREDENTIALS:
        raise typer.BadParameter(f"unsupported gateway platform: {platform}")
    default_route = f"/gateway/{endpoint_id}"
    route = route or (
        typer.prompt("Inbound route", default=default_route) if interactive else default_route
    )
    user_id = user_id or typer.prompt("Platform user id")
    subject = subject or typer.prompt("Loro identity subject", default=user_id)
    tenant = tenant or typer.prompt("Loro tenant")
    mappings = _credential_mappings(credential or [])
    if interactive:
        for name in _REQUIRED_CREDENTIALS[platform]:
            mappings.setdefault(
                name,
                typer.prompt(
                    f"Vault reference for {name}",
                    default=f"vault://gateway/{endpoint_id}/{name}",
                ),
            )
    missing = [name for name in _REQUIRED_CREDENTIALS[platform] if name not in mappings]
    if missing:
        raise typer.BadParameter("missing credential mappings: " + ", ".join(missing))
    config = load_config()
    config.gateway.enabled = True
    config.gateway.endpoints[endpoint_id] = GatewayEndpointConfig(
        platform=platform,  # type: ignore[arg-type]
        route=route,
        credentials=mappings,
        identities={
            user_id: GatewayIdentityConfig(subject=subject, tenant=tenant),
        },
        allowed_channels=[channel] if channel else [],
        allowed_workspaces=[workspace] if workspace else [],
    )
    written = write_config_sections(output, config, ["gateway"])
    console.print(f"Configured {platform} gateway {endpoint_id}: {written}")


@gateway_app.command("doctor")
def gateway_doctor() -> None:
    """Validate gateway routes, identity maps, vault entries, and bind posture."""
    config = load_config()
    vault = CredentialVault(config.credentials)
    findings: list[str] = []
    if not config.gateway.enabled:
        findings.append("gateway is disabled")
    if not config.gateway.endpoints:
        findings.append("gateway has no configured endpoints")
    if config.gateway.host not in {"127.0.0.1", "::1", "localhost"}:
        findings.append("non-loopback binding requires an authenticated TLS reverse proxy")
    endpoints: list[dict[str, object]] = []
    for endpoint_id, endpoint in config.gateway.endpoints.items():
        missing: list[str] = []
        for name in _REQUIRED_CREDENTIALS[endpoint.platform]:
            ref = endpoint.credentials.get(name)
            try:
                present = bool(ref and vault.get(ref))
            except CredentialError:
                present = False
            if not present:
                missing.append(name)
        if not endpoint.identities:
            findings.append(f"{endpoint_id}: no trusted identity mappings")
        endpoints.append(
            {
                "id": endpoint_id,
                "platform": endpoint.platform,
                "route": endpoint.route,
                "identities": len(endpoint.identities),
                "missing_credentials": missing,
            }
        )
        if missing:
            findings.append(f"{endpoint_id}: missing credentials: {', '.join(missing)}")
    payload = {"ok": not findings, "findings": findings, "endpoints": endpoints}
    console.print_json(data=payload)
    if findings:
        raise typer.Exit(code=1)


@gateway_app.command("serve")
def gateway_serve() -> None:
    """Run the bounded HTTP gateway service; terminate TLS in an approved reverse proxy."""
    config = load_config()
    console.print(f"Serving Loro gateways on {config.gateway.host}:{config.gateway.port}")
    try:
        serve_gateway(config)
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _credential_mappings(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, separator, ref = value.partition("=")
        if not separator or not name or not ref:
            raise typer.BadParameter("credentials must use name=vault://namespace/profile/key")
        try:
            CredentialReference.parse(ref)
        except CredentialError as error:
            raise typer.BadParameter(str(error)) from error
        result[name] = ref
    return result
