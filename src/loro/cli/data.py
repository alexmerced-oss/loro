"""`loro data`: governed data discovery through Apache Polaris."""

from __future__ import annotations

from typing import Annotated

import click
import typer

from loro.cli._common import _audit, _authorize_cli_action, console
from loro.config import (
    load_config,
)
from loro.governed_data import explain_access, inspect_table_schema
from loro.polaris import PolarisClient, PolarisResult
from loro.resources import (
    NormalizedResource,
)

data_app = typer.Typer(help="Discover governed enterprise data.")


def _run_polaris_result(result: PolarisResult) -> None:
    _audit().write(
        "polaris.readonly_executed",
        command=result.command,
        returncode=result.returncode,
        sandbox_profile=result.sandbox_profile,
        sandbox_os_enforced=result.sandbox_os_enforced,
        output_truncated=result.output_truncated,
    )
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)
    raise typer.Exit(code=result.returncode)


def _polaris_client() -> PolarisClient:
    config = load_config()
    if not config.polaris.enabled:
        console.print(
            "Polaris is disabled. Enable [polaris] before using this command.",
            markup=False,
        )
        raise typer.Exit(code=2)
    context = click.get_current_context(silent=True)
    action = context.info_name if context is not None and context.info_name else "discovery"
    arguments = dict(context.params) if context is not None else {}
    data_options = context.obj if context is not None and isinstance(context.obj, dict) else {}
    target_parts = [
        str(arguments[key])
        for key in ("catalog", "namespace", "table", "view", "resource", "role", "policy")
        if arguments.get(key) is not None
    ]
    resource = NormalizedResource(
        kind="polaris",
        fields={
            "operation": action,
            "catalog": str(arguments.get("catalog") or config.polaris.catalog or ""),
            "namespace": str(arguments.get("namespace") or ""),
            "table": str(arguments.get("table") or ""),
            "resource": str(arguments.get("resource") or ""),
            "role": str(arguments.get("catalog_role") or arguments.get("principal_role") or ""),
            "policy": str(arguments.get("policy") or ""),
        },
    )
    _authorize_cli_action(
        tool="governed_data",
        action=action,
        target="/".join(target_parts) or config.polaris.catalog or "catalog",
        arguments=arguments,
        risk_reason="Read metadata from the governed Apache Polaris catalog.",
        non_interactive_approved=bool(data_options.get("yes", False)),
        resource=resource,
    )
    return PolarisClient(
        config.polaris,
        config.sandbox,
        workspace_roots=config.permissions.workspace_roots,
    )


@data_app.callback()
def data_options(
    context: typer.Context,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Non-interactive approval for ask-gated governed-data discovery.",
        ),
    ] = False,
) -> None:
    """Configure governed-data command approval behavior."""
    context.ensure_object(dict)
    context.obj["yes"] = yes


@data_app.command("catalogs")
def data_catalogs() -> None:
    """List Polaris catalogs through the typed client."""
    _run_polaris_result(_polaris_client().list_catalogs())


@data_app.command("catalog")
def data_catalog(catalog: Annotated[str, typer.Argument(help="Catalog name.")]) -> None:
    """Describe one Polaris catalog through the typed client."""
    _run_polaris_result(_polaris_client().get_catalog(catalog))


@data_app.command("namespaces")
def data_namespaces(
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris namespaces through the typed client."""
    _run_polaris_result(_polaris_client().list_namespaces(catalog=catalog))


@data_app.command("namespace")
def data_namespace(
    namespace: Annotated[str, typer.Argument(help="Namespace name.")],
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Describe one Polaris namespace through the typed client."""
    _run_polaris_result(_polaris_client().get_namespace(namespace, catalog=catalog))


@data_app.command("tables")
def data_tables(
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris tables through the typed client."""
    _run_polaris_result(_polaris_client().list_tables(namespace=namespace, catalog=catalog))


@data_app.command("table")
def data_table(
    table: Annotated[str, typer.Argument(help="Table name.")],
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Describe one Polaris table through the typed client."""
    _run_polaris_result(_polaris_client().get_table(table, namespace=namespace, catalog=catalog))


@data_app.command("schema")
def data_schema(
    table: Annotated[str, typer.Argument(help="Table name.")],
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Inspect a governed table schema through Polaris metadata."""
    result = inspect_table_schema(
        _polaris_client(),
        table=table,
        namespace=namespace,
        catalog=catalog,
    )
    _audit().write(
        "data.schema_inspected",
        table=table,
        namespace=namespace,
        catalog=catalog,
        ok=result.ok,
    )
    console.print_json(data=result.to_payload())
    raise typer.Exit(code=0 if result.ok else 1)


@data_app.command("explain-access")
def data_explain_access(
    resource: Annotated[str, typer.Argument(help="Table or resource identifier.")],
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
    catalog_role: Annotated[
        str | None,
        typer.Option("--catalog-role", help="Catalog role to inspect privileges for."),
    ] = None,
) -> None:
    """Explain read-only Polaris discovery results for a governed resource."""
    result = explain_access(
        _polaris_client(),
        resource=resource,
        namespace=namespace,
        catalog=catalog,
        catalog_role=catalog_role,
    )
    _audit().write(
        "data.access_explained",
        resource=resource,
        namespace=namespace,
        catalog=catalog,
        catalog_role=catalog_role,
        ok=result.ok,
    )
    console.print_json(data=result.to_payload())
    raise typer.Exit(code=0 if result.ok else 1)


@data_app.command("views")
def data_views(
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris views through the typed client."""
    _run_polaris_result(_polaris_client().list_views(namespace=namespace, catalog=catalog))


@data_app.command("view")
def data_view(
    view: Annotated[str, typer.Argument(help="View name.")],
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Describe one Polaris view through the typed client."""
    _run_polaris_result(_polaris_client().get_view(view, namespace=namespace, catalog=catalog))


@data_app.command("principal-roles")
def data_principal_roles() -> None:
    """List Polaris principal roles through the typed client."""
    _run_polaris_result(_polaris_client().list_principal_roles())


@data_app.command("principal-role")
def data_principal_role(role: Annotated[str, typer.Argument(help="Principal role name.")]) -> None:
    """Describe one Polaris principal role through the typed client."""
    _run_polaris_result(_polaris_client().get_principal_role(role))


@data_app.command("catalog-roles")
def data_catalog_roles(
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris catalog roles through the typed client."""
    _run_polaris_result(_polaris_client().list_catalog_roles(catalog=catalog))


@data_app.command("catalog-role")
def data_catalog_role(
    role: Annotated[str, typer.Argument(help="Catalog role name.")],
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Describe one Polaris catalog role through the typed client."""
    _run_polaris_result(_polaris_client().get_catalog_role(role, catalog=catalog))


@data_app.command("privileges")
def data_privileges(
    catalog_role: Annotated[
        str | None,
        typer.Option("--catalog-role", help="Catalog role name."),
    ] = None,
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris privileges through the typed client."""
    _run_polaris_result(
        _polaris_client().list_privileges(catalog_role=catalog_role, catalog=catalog)
    )


@data_app.command("policies")
def data_policies(
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """List Polaris policies through the typed client."""
    _run_polaris_result(_polaris_client().list_policies(catalog=catalog))


@data_app.command("policy")
def data_policy(
    policy: Annotated[str, typer.Argument(help="Policy name.")],
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
) -> None:
    """Describe one Polaris policy through the typed client."""
    _run_polaris_result(_polaris_client().get_policy(policy, catalog=catalog))


@data_app.command("applicable-policies")
def data_applicable_policies(
    resource: Annotated[str, typer.Argument(help="Resource identifier.")],
    catalog: Annotated[str | None, typer.Option("--catalog", help="Catalog name.")] = None,
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace name."),
    ] = None,
) -> None:
    """List Polaris policies applicable to a resource through the typed client."""
    _run_polaris_result(
        _polaris_client().list_applicable_policies(
            resource,
            catalog=catalog,
            namespace=namespace,
        )
    )


@data_app.command("polaris")
def data_polaris(
    args: Annotated[list[str], typer.Argument(help="Read-only Polaris CLI arguments.")],
) -> None:
    """Run a read-only Polaris CLI operation through Loro's wrapper."""
    _run_polaris_result(_polaris_client().run_readonly(args))
