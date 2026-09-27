"""`loro providers`: AI provider profiles, discovery, and smoke tests."""

from __future__ import annotations

from typing import Annotated

import typer

from loro.cli._common import _audit, console
from loro.config import (
    load_config,
)
from loro.models import ModelMessage, create_model_client, redact_model_request, smoke_model_client
from loro.provider_contracts import (
    ProviderContractError,
    default_contract_paths,
    validate_provider_contracts,
)
from loro.providers import (
    check_provider_config,
    get_provider_profile,
    model_config_from_profile,
    provider_names,
)

providers_app = typer.Typer(help="Inspect and configure AI providers.")


@providers_app.command("list")
def providers_list() -> None:
    """List built-in provider profiles."""
    for name in provider_names():
        profile = get_provider_profile(name)
        console.print(
            f"- [bold]{name}[/bold]: {profile.display_name} "
            f"({profile.protocol}, default={profile.default_model})"
        )


@providers_app.command("show")
def providers_show(provider: Annotated[str, typer.Argument(help="Provider name.")]) -> None:
    """Show one provider profile."""
    try:
        profile = get_provider_profile(provider)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(
        data={
            "name": profile.name,
            "display_name": profile.display_name,
            "default_model": profile.default_model,
            "small_model": profile.small_model,
            "aliases": list(profile.aliases),
            "api_key_env": profile.api_key_env,
            "base_url": profile.base_url,
            "protocol": profile.protocol,
            "optional_header_env": dict(profile.optional_header_env),
            "notes": profile.notes,
        }
    )


@providers_app.command("check")
def providers_check(
    provider: Annotated[
        str | None,
        typer.Argument(help="Provider name. Defaults to configured provider."),
    ] = None,
) -> None:
    """Check provider config and required environment variables."""
    config = load_config()
    model_config = config.model
    if provider:
        profile = get_provider_profile(provider)
        model_config = model_config_from_profile(
            profile.name,
            model=profile.default_model,
            small_model=profile.small_model,
        )
    check = check_provider_config(model_config)
    console.print_json(
        data={
            "provider": check.provider,
            "ok": check.ok,
            "api_key_env": check.api_key_env,
            "api_key_present": check.api_key_present,
            "credential_ref": check.credential_ref,
            "credential_present": check.credential_present,
            "base_url": check.base_url,
            "protocol": check.protocol,
            "messages": check.messages,
        }
    )
    raise typer.Exit(code=0 if check.ok else 1)


@providers_app.command("request")
def providers_request(
    prompt: Annotated[str, typer.Argument(help="Prompt to build a request for.")],
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name. Defaults to configured provider."),
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Model override.")] = None,
    base_url: Annotated[str | None, typer.Option("--base-url", help="Base URL override.")] = None,
) -> None:
    """Print a redacted model request without sending it."""
    config = load_config()
    model_config = config.model
    if provider:
        profile = get_provider_profile(provider)
        model_config = model_config_from_profile(
            profile.name,
            model=model or profile.default_model,
            small_model=profile.small_model,
            base_url=base_url,
        )
    elif model or base_url:
        model_config.model = model or model_config.model
        model_config.base_url = base_url or model_config.base_url
    client = create_model_client(model_config)
    request = client.build_request([ModelMessage(role="user", content=prompt)])
    console.print_json(data=redact_model_request(request))


@providers_app.command("smoke")
def providers_smoke(
    prompt: Annotated[
        str,
        typer.Argument(help="Prompt for the smoke request."),
    ] = "Reply with ok.",
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name. Defaults to configured provider."),
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Model override.")] = None,
    base_url: Annotated[str | None, typer.Option("--base-url", help="Base URL override.")] = None,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Actually send the request. Default is dry-run."),
    ] = False,
    stream: Annotated[
        bool,
        typer.Option("--stream", help="Use the streaming interface when executing."),
    ] = False,
) -> None:
    """Build or execute a provider smoke request."""
    config = load_config()
    model_config = config.model
    if provider:
        profile = get_provider_profile(provider)
        model_config = model_config_from_profile(
            profile.name,
            model=model or profile.default_model,
            small_model=profile.small_model,
            base_url=base_url,
        )
    elif model or base_url:
        model_config.model = model or model_config.model
        model_config.base_url = base_url or model_config.base_url
    result = smoke_model_client(model_config, prompt=prompt, execute=execute, stream=stream)
    _audit().write(
        "provider.smoke",
        provider=model_config.provider,
        model=model_config.model,
        execute=execute,
        stream=stream,
        ok=result.get("ok") if execute else None,
    )
    console.print_json(data=result)
    if execute and not result.get("ok", False):
        raise typer.Exit(code=1)


@providers_app.command("conformance")
def providers_conformance() -> None:
    """Validate sanitized provider contracts and the advertised profile matrix."""
    matrix, fixtures = default_contract_paths()
    try:
        report = validate_provider_contracts(matrix, fixtures)
    except ProviderContractError as error:
        console.print_json(data={"ok": False, "error": str(error)})
        raise typer.Exit(code=1) from error
    console.print_json(data=report.as_dict())
