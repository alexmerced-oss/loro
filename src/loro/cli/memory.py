"""`loro memory`: local and shared memory, drafts, proposals, and migrations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer

from loro.audit import prompt_preview
from loro.cli._common import (
    _audit,
    _authorize_cli_action,
    _enforce_safe_content,
    _identity,
    _shared_draft_store,
    _shared_memory_tenant,
    console,
)
from loro.cli.ops import (
    memory_sweep as ops_memory_sweep,
)
from loro.config import (
    load_config,
)
from loro.identity import (
    resolve_identity,
)
from loro.memory.base import SharedMemoryLifecycleRequest
from loro.memory.iceberg import IcebergSharedMemoryStore
from loro.memory.local import LocalMemoryStore
from loro.memory.migrations import (
    LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
    postgres_memory_migrations,
)
from loro.memory.operations import (
    apply_shared_memory_lifecycle,
    check_shared_memory_backend,
    create_shared_memory_draft,
    render_or_commit_shared_draft,
    search_shared_memories,
)
from loro.memory.postgres import PostgresSharedMemoryStore
from loro.memory.proposals import MemoryProposal, MemoryProposalStore
from loro.memory.schemas import shared_memory_schema
from loro.resources import (
    memory_resource,
)
from loro.serialization import jsonable_mapping

memory_app = typer.Typer(help="Inspect and write Loro memories.")


memory_app.command("sweep")(ops_memory_sweep)


@memory_app.command("list")
def memory_list() -> None:
    """List local memories."""
    config = load_config()
    store = LocalMemoryStore.from_config(config.memory.local, config.safety)
    memories = store.list()
    if not memories:
        console.print("No local memories yet.")
        return
    for memory in memories:
        console.print(
            f"- [bold]{memory.memory_id}[/bold] "
            f"({memory.created_at.date().isoformat()}): {memory.content}"
        )


@memory_app.command("search")
def memory_search(query: Annotated[str, typer.Argument(help="Search query.")]) -> None:
    """Search local memories."""
    config = load_config()
    store = LocalMemoryStore.from_config(config.memory.local, config.safety)
    memories = store.search(query)
    if not memories:
        console.print("No matching local memories.")
        return
    for memory in memories:
        console.print(f"- [bold]{memory.memory_id}[/bold]: {memory.content}")


@memory_app.command("shared-search")
def memory_shared_search(
    query: Annotated[str, typer.Argument(help="Search query.")],
    tenant_id: Annotated[
        str | None,
        typer.Option("--tenant-id", help="Shared memory tenant. Defaults to active identity."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum memories to return.")] = 20,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Render backend search SQL without executing."),
    ] = False,
) -> None:
    """Search shared enterprise memory or render the backend search statement."""
    config = load_config()
    resolved_tenant = _shared_memory_tenant(config, tenant_id)
    result = search_shared_memories(
        config,
        query=query,
        tenant_id=resolved_tenant,
        limit=limit,
        execute=not dry_run,
    )
    _audit().write(
        "memory.shared_search",
        backend=result.backend,
        query=prompt_preview(query),
        tenant_id=resolved_tenant,
        executed=result.executed,
        record_count=len(result.records),
    )
    if result.executed:
        if not result.records:
            console.print("No matching shared memories.")
            return
        for record in result.records:
            console.print(f"- [bold]{record.citation}[/bold]: {record.summary}")
        return
    console.print_json(
        data={
            "backend": result.backend,
            "query": result.query,
            "tenant_id": result.tenant_id,
            "executed": result.executed,
            "messages": result.messages,
            "sql": result.statement.sql if result.statement else None,
            "params": jsonable_mapping(result.statement.params) if result.statement else None,
        }
    )


@memory_app.command("propose")
def memory_propose(
    content: Annotated[str, typer.Argument(help="Proposed memory content.")],
    target: Annotated[
        str,
        typer.Option("--target", help="Proposal target: local or shared."),
    ] = "local",
    rationale: Annotated[
        str,
        typer.Option("--rationale", help="Why this should be remembered."),
    ] = "",
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    """Create a local memory proposal record without committing memory."""
    if target not in {"local", "shared"}:
        raise typer.BadParameter("target must be local or shared.")
    _enforce_safe_content(
        content,
        context=f"memory.proposal.{target}",
        allow_sensitive=allow_sensitive,
    )
    proposal = MemoryProposal(content=content, target=target, rationale=rationale)
    MemoryProposalStore(Path(load_config().memory.local.path)).propose(proposal)
    _audit().write(
        "memory.proposal_created",
        proposal_id=proposal.proposal_id,
        target=proposal.target,
        content_preview=prompt_preview(content),
    )
    console.print(f"Created memory proposal: {proposal.proposal_id}")


@memory_app.command("proposals")
def memory_proposals() -> None:
    """List local memory proposal records."""
    proposals = MemoryProposalStore(Path(load_config().memory.local.path)).list()
    if not proposals:
        console.print("No memory proposals yet.")
        return
    for proposal in proposals:
        console.print(
            f"- [bold]{proposal.proposal_id}[/bold] "
            f"({proposal.target}, {proposal.status}): {proposal.content}"
        )


@memory_app.command("accept-proposal")
def memory_accept_proposal(
    proposal_id: Annotated[str, typer.Argument(help="Memory proposal id.")],
    tenant_id: Annotated[
        str | None,
        typer.Option("--tenant-id", help="Shared memory tenant. Defaults to active identity."),
    ] = None,
    scope_type: Annotated[
        str, typer.Option("--scope-type", help="Shared memory scope type.")
    ] = "org",
    scope_key: Annotated[
        str, typer.Option("--scope-key", help="Shared memory scope key.")
    ] = "default",
    created_by: Annotated[
        str | None,
        typer.Option("--created-by", help="Shared memory author. Defaults to active identity."),
    ] = None,
) -> None:
    """Accept a proposal into local memory or a shared-memory draft."""
    config = load_config()
    identity = _identity()
    store = MemoryProposalStore(Path(config.memory.local.path))
    proposal = store.get(proposal_id)
    if proposal is None:
        raise typer.BadParameter(f"Unknown memory proposal id: {proposal_id}")
    if proposal.target == "shared":
        _enforce_safe_content(proposal.content, context="memory.shared.proposal")
        resolved_tenant = _shared_memory_tenant(config, tenant_id)
        draft = create_shared_memory_draft(
            content=proposal.content,
            tenant_id=resolved_tenant,
            scope_type=scope_type,
            scope_key=scope_key,
            memory_type="fact",
            classification="public-internal",
            created_by=created_by or identity.subject,
            retention_days=config.memory.shared.retention_days,
        )
        _shared_draft_store(config).stage(draft)
        store.update_status(proposal_id, "accepted_as_shared_draft")
        _audit().write(
            "memory.proposal_accepted",
            proposal_id=proposal_id,
            target=proposal.target,
            draft_id=draft.draft_id,
        )
        console.print(f"Accepted proposal as shared memory draft: {draft.draft_id}")
        return
    memory = LocalMemoryStore.from_config(config.memory.local, config.safety).remember(
        proposal.content
    )
    store.update_status(proposal_id, "accepted")
    _audit().write(
        "memory.proposal_accepted",
        proposal_id=proposal_id,
        target=proposal.target,
        memory_id=memory.memory_id,
    )
    console.print(f"Accepted proposal as local memory: {memory.memory_id}")


@memory_app.command("remember")
def remember_local(
    content: Annotated[str, typer.Argument(help="Memory content.")],
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    """Explicitly write a local memory."""
    _enforce_safe_content(content, context="memory.local", allow_sensitive=allow_sensitive)
    config = load_config()
    store = LocalMemoryStore.from_config(config.memory.local, config.safety)
    memory = store.remember(content, allow_sensitive=allow_sensitive)
    _audit().write(
        "memory.local_written",
        memory_id=memory.memory_id,
        scope=memory.scope,
        content_preview=prompt_preview(content),
    )
    console.print(f"Saved local memory: {memory.memory_id}")


@memory_app.command("drafts")
def memory_drafts() -> None:
    """List staged shared memory drafts."""
    config = load_config()
    store = _shared_draft_store(config)
    drafts = store.list()
    if not drafts:
        console.print("No shared memory drafts yet.")
        return
    for draft in drafts:
        console.print(
            f"- [bold]{draft.draft_id}[/bold] "
            f"({draft.tenant_id}/{draft.scope_type}/{draft.scope_key}): {draft.summary}"
        )


@memory_app.command("schema")
def memory_schema(
    backend: Annotated[
        str,
        typer.Option("--backend", help="Shared memory backend: postgres or iceberg."),
    ] = "postgres",
) -> None:
    """Print shared memory backend schema SQL."""
    config = load_config()
    try:
        if backend == "postgres":
            console.print(PostgresSharedMemoryStore(config.memory.shared).render_schema())
            return
        console.print(shared_memory_schema(backend, config.memory.shared))  # type: ignore[arg-type]
    except (RuntimeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error


@memory_app.command("apply-schema")
def memory_apply_schema(
    execute: Annotated[
        bool,
        typer.Option(
            "--execute",
            help="Apply schema to the configured backend. Without this flag Loro only renders SQL.",
        ),
    ] = False,
) -> None:
    """Render or apply the configured shared memory backend schema."""
    config = load_config()
    backend = config.memory.shared.backend
    if backend == "postgres":
        store = PostgresSharedMemoryStore(config.memory.shared)
        if execute:
            try:
                store.apply_schema()
            except RuntimeError as error:
                raise typer.BadParameter(str(error)) from error
            _audit().write("memory.shared_schema_applied", backend=backend)
            console.print("Applied Postgres shared memory schema.")
            return
        console.print(store.render_schema())
        return
    if execute:
        raise typer.BadParameter("Live Iceberg schema application is not enabled in this MVP.")
    console.print(shared_memory_schema(backend, config.memory.shared))


@memory_app.command("migration-status")
def memory_migration_status() -> None:
    """Show the applied Postgres shared-memory schema version."""
    config = load_config()
    if config.memory.shared.backend != "postgres":
        raise typer.BadParameter("Migration status is available only for Postgres memory.")
    store = PostgresSharedMemoryStore(config.memory.shared)
    try:
        current = store.schema_version()
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(
        data={
            "backend": "postgres",
            "current_version": current,
            "latest_version": LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
            "up_to_date": current == LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
        }
    )


@memory_app.command("migrate")
def memory_migrate(
    target: Annotated[
        int,
        typer.Option("--target", min=0, help="Target Postgres memory schema version."),
    ] = LATEST_POSTGRES_MEMORY_SCHEMA_VERSION,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Apply the migration plan to the configured database."),
    ] = False,
    allow_destructive: Annotated[
        bool,
        typer.Option(
            "--allow-destructive",
            help="Authorize rollback below the durable baseline. This can delete memory data.",
        ),
    ] = False,
) -> None:
    """Render or apply versioned Postgres shared-memory migrations."""
    config = load_config()
    if config.memory.shared.backend != "postgres":
        raise typer.BadParameter("Migrations are available only for Postgres memory.")
    if target > LATEST_POSTGRES_MEMORY_SCHEMA_VERSION:
        raise typer.BadParameter(
            f"Latest supported schema is version {LATEST_POSTGRES_MEMORY_SCHEMA_VERSION}."
        )
    store = PostgresSharedMemoryStore(config.memory.shared)
    if not execute:
        migrations = postgres_memory_migrations(
            config.memory.shared.postgres_schema,
            tenant_isolation=config.memory.shared.tenant_isolation == "identity",
        )
        console.print(
            f"Postgres memory migration plan to version {target} "
            "(render only; pass --execute to apply):"
        )
        for migration in migrations:
            if migration.version <= target:
                console.print(f"\n-- {migration.version}: {migration.name}\n{migration.up}")
        return
    try:
        result = store.migrate(target_version=target, allow_destructive=allow_destructive)
    except (RuntimeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "memory.schema_migrated",
        backend="postgres",
        previous_version=result.previous_version,
        current_version=result.current_version,
        applied=list(result.applied),
        rolled_back=list(result.rolled_back),
    )
    console.print_json(data=jsonable_mapping(result.__dict__))


@memory_app.command("reconcile")
def memory_reconcile() -> None:
    """Compare Postgres memory state rows with append-only lifecycle events."""
    config = load_config()
    if config.memory.shared.backend != "postgres":
        raise typer.BadParameter("Reconciliation is available only for Postgres memory.")
    identity = resolve_identity(config.identity)
    store = PostgresSharedMemoryStore(
        config.memory.shared,
        authorized_tenant_id=(
            identity.tenant if config.memory.shared.tenant_isolation == "identity" else None
        ),
    )
    try:
        report = store.reconcile()
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "memory.reconciled",
        backend="postgres",
        tenant=identity.tenant,
        ok=report.ok,
        issues=list(report.issues),
    )
    console.print_json(data=jsonable_mapping(report.__dict__ | {"ok": report.ok}))
    raise typer.Exit(code=0 if report.ok else 1)


@memory_app.command("backend-check")
def memory_backend_check() -> None:
    """Check whether the configured shared memory backend is ready."""
    config = load_config()
    check = check_shared_memory_backend(config.memory.shared)
    console.print_json(data=check.__dict__)
    raise typer.Exit(code=0 if check.ok else 1)


@memory_app.command("snapshots")
def memory_snapshots() -> None:
    """Show content-free Iceberg memory and event snapshot state."""
    config = load_config()
    if config.memory.shared.backend != "iceberg":
        raise typer.BadParameter("Snapshot diagnostics are available only for Iceberg memory.")
    identity = resolve_identity(config.identity)
    store = IcebergSharedMemoryStore(
        config.memory.shared,
        authorized_tenant_id=(
            identity.tenant if config.memory.shared.tenant_isolation == "identity" else None
        ),
    )
    try:
        report = store.snapshot_report()
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    payload = {
        "memory": report.memory.__dict__,
        "events": report.events.__dict__,
        "aligned": report.aligned,
    }
    _audit().write("memory.iceberg_snapshots_inspected", **payload)
    console.print_json(data=jsonable_mapping(payload))


@memory_app.command("commit-draft")
def memory_commit_draft(
    draft_id: Annotated[str, typer.Argument(help="Shared memory draft id.")],
    execute: Annotated[
        bool,
        typer.Option(
            "--execute",
            help="Execute the commit. Without this flag Loro only renders backend SQL.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Non-interactive approval for an ask-gated shared-memory commit.",
        ),
    ] = False,
) -> None:
    """Render or execute an explicit shared memory draft commit."""
    config = load_config()
    draft_store = _shared_draft_store(config)
    draft = draft_store.get(draft_id)
    if draft is None:
        raise typer.BadParameter(f"Unknown shared memory draft id: {draft_id}")
    _enforce_safe_content(draft.content, context="memory.shared.commit")

    if execute:
        resource = memory_resource(
            operation="commit",
            tenant=draft.tenant_id,
            scope_type=draft.scope_type,
            scope_key=draft.scope_key,
            backend=config.memory.shared.backend,
        )
        _authorize_cli_action(
            tool="shared_memory",
            action="commit draft",
            target=f"{draft.tenant_id}/{draft.scope_type}/{draft.scope_key}/{draft.draft_id}",
            arguments={
                "draft_id": draft.draft_id,
                "tenant_id": draft.tenant_id,
                "scope_type": draft.scope_type,
                "scope_key": draft.scope_key,
                "memory_type": draft.memory_type,
                "classification": draft.classification,
                "content": draft.content,
                "created_by": draft.created_by,
            },
            risk_reason="Commit user-dictated content to shared enterprise memory.",
            non_interactive_approved=yes,
            resource=resource,
        )

    try:
        result = render_or_commit_shared_draft(config, draft, execute=execute)
    except (RuntimeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    if result.executed:
        _audit().write(
            "memory.shared_draft_committed",
            backend=result.backend,
            draft_id=draft.draft_id,
            tenant_id=draft.tenant_id,
        )
        console.print(f"Committed shared memory draft: {draft.draft_id}")
        return

    if result.statement is None:
        raise typer.BadParameter("Shared memory dry run did not produce a statement.")

    _audit().write(
        "memory.shared_draft_sql_rendered",
        backend=result.backend,
        draft_id=draft.draft_id,
        tenant_id=draft.tenant_id,
    )
    console.print_json(
        data={
            "backend": result.backend,
            "draft_id": draft.draft_id,
            "execute": False,
            "sql": result.statement.sql,
            "params": jsonable_mapping(result.statement.params),
        }
    )


@memory_app.command("lifecycle")
def memory_lifecycle(
    memory_id: Annotated[str, typer.Argument(help="Shared memory id.")],
    action: Annotated[
        str,
        typer.Option(
            "--action",
            help="Lifecycle action: correct, delete, expire, hold, or release_hold.",
        ),
    ],
    reason: Annotated[str, typer.Option("--reason", help="Required operator reason.")],
    tenant_id: Annotated[
        str | None,
        typer.Option("--tenant-id", help="Shared memory tenant. Defaults to active identity."),
    ] = None,
    content: Annotated[
        str | None,
        typer.Option("--content", help="Replacement content for correction."),
    ] = None,
    expires_at: Annotated[
        str | None,
        typer.Option("--expires-at", help="ISO-8601 expiration time for expire."),
    ] = None,
    operation_id: Annotated[
        str | None,
        typer.Option(
            "--operation-id",
            help="UUID to reuse when retrying a partial lifecycle operation.",
        ),
    ] = None,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Execute instead of rendering the backend operation."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Non-interactive approval when policy permits."),
    ] = False,
) -> None:
    """Correct, delete, expire, hold, or release a shared memory."""
    action = action.replace("-", "_")
    allowed_actions = {"correct", "delete", "expire", "hold", "release_hold"}
    if action not in allowed_actions:
        raise typer.BadParameter("Unsupported lifecycle action.")
    config = load_config()
    identity = _identity()
    resolved_tenant = _shared_memory_tenant(config, tenant_id)
    if content is not None:
        _enforce_safe_content(content, context="memory.shared.lifecycle")
    expiration: datetime | None = None
    if expires_at:
        try:
            expiration = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise typer.BadParameter("expires-at must be ISO-8601.") from error
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=UTC)
    normalized_operation_id: str | None = None
    if operation_id:
        try:
            normalized_operation_id = str(UUID(operation_id))
        except ValueError as error:
            raise typer.BadParameter("operation-id must be a UUID.") from error
    request_values: dict[str, Any] = {}
    if normalized_operation_id is not None:
        request_values["event_id"] = normalized_operation_id
    request = SharedMemoryLifecycleRequest(
        memory_id=memory_id,
        tenant_id=resolved_tenant,
        action=action,  # type: ignore[arg-type]
        actor=identity.subject,
        reason=reason,
        content=content,
        summary=prompt_preview(content, limit=120) if content else None,
        expires_at=expiration,
        **request_values,
    )
    if execute:
        resource = memory_resource(
            operation=action,
            tenant=resolved_tenant,
            scope_type="memory",
            scope_key=memory_id,
            backend=config.memory.shared.backend,
        )
        _authorize_cli_action(
            tool="shared_memory",
            action=action,
            target=f"{resolved_tenant}/{memory_id}",
            arguments={
                "memory_id": memory_id,
                "tenant_id": resolved_tenant,
                "action": action,
                "reason": reason,
                "content": content,
                "expires_at": expiration.isoformat() if expiration else None,
            },
            risk_reason="Change governed shared-memory lifecycle state.",
            non_interactive_approved=yes,
            resource=resource,
        )
    try:
        result = apply_shared_memory_lifecycle(config, request, execute=execute)
    except (PermissionError, RuntimeError, ValueError) as error:
        retry = (
            f" Retry the same operation with --operation-id {request.event_id}."
            if execute and config.memory.shared.backend == "iceberg"
            else ""
        )
        raise typer.BadParameter(f"{error}{retry}") from error
    _audit().write(
        "memory.shared_lifecycle",
        action=action,
        target=f"{resolved_tenant}/{memory_id}",
        tenant_id=resolved_tenant,
        backend=result.backend,
        executed=result.executed,
        reason=reason,
        operation_id=request.event_id,
    )
    if result.executed:
        console.print(f"Applied shared-memory lifecycle action: {action}")
        return
    console.print_json(
        data={
            "backend": result.backend,
            "execute": False,
            "action": action,
            "operation_id": request.event_id,
            "sql": result.statement.sql,
            "params": jsonable_mapping(result.statement.params),
        }
    )
