from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loro.audit import prompt_preview
from loro.config import LoroConfig, SharedMemoryConfig
from loro.identity import resolve_identity
from loro.memory.base import (
    SharedMemoryBackendCheck,
    SharedMemoryDraft,
    SharedMemoryLifecycleRequest,
    SharedMemorySearchResult,
    SharedMemoryStatement,
)
from loro.memory.iceberg import IcebergSharedMemoryStore
from loro.memory.postgres import PostgresSharedMemoryStore


@dataclass(frozen=True)
class SharedMemoryCommitResult:
    backend: str
    draft: SharedMemoryDraft
    executed: bool
    statement: SharedMemoryStatement | None = None


@dataclass(frozen=True)
class SharedMemoryLifecycleResult:
    backend: str
    request: SharedMemoryLifecycleRequest
    executed: bool
    statement: SharedMemoryStatement


def search_shared_memories(
    config: LoroConfig,
    *,
    query: str,
    tenant_id: str = "default",
    limit: int = 20,
    execute: bool = True,
) -> SharedMemorySearchResult:
    backend = config.memory.shared.backend
    authorized_tenant = _authorized_tenant(config, tenant_id)
    if backend == "postgres":
        store = PostgresSharedMemoryStore(
            config.memory.shared, authorized_tenant_id=authorized_tenant
        )
        statement = store.render_search(tenant_id=tenant_id, query=query, limit=limit)
        if not execute:
            return SharedMemorySearchResult(
                backend=backend,
                query=query,
                tenant_id=tenant_id,
                executed=False,
                statement=statement,
                messages=["Rendered Postgres shared-memory search SQL."],
            )
        try:
            records = store.search(tenant_id=tenant_id, query=query, limit=limit)
        except RuntimeError as error:
            return SharedMemorySearchResult(
                backend=backend,
                query=query,
                tenant_id=tenant_id,
                executed=False,
                statement=statement,
                messages=[str(error), "Rendered SQL instead of executing search."],
            )
        return SharedMemorySearchResult(
            backend=backend,
            query=query,
            tenant_id=tenant_id,
            executed=True,
            records=records,
            messages=[f"Found {len(records)} shared memories."],
        )
    if backend == "iceberg":
        store = IcebergSharedMemoryStore(
            config.memory.shared, authorized_tenant_id=authorized_tenant
        )
        statement = store.render_search(
            tenant_id=tenant_id,
            query=query,
            limit=limit,
        )
        if not execute:
            return SharedMemorySearchResult(
                backend=backend,
                query=query,
                tenant_id=tenant_id,
                executed=False,
                statement=statement,
                messages=["Rendered Iceberg shared-memory search SQL."],
            )
        try:
            records = store.search(tenant_id=tenant_id, query=query, limit=limit)
        except RuntimeError as error:
            return SharedMemorySearchResult(
                backend=backend,
                query=query,
                tenant_id=tenant_id,
                executed=False,
                statement=statement,
                messages=[str(error), "Rendered SQL instead of executing search."],
            )
        return SharedMemorySearchResult(
            backend=backend,
            query=query,
            tenant_id=tenant_id,
            executed=True,
            records=records,
            statement=statement,
            messages=[
                f"Found {len(records)} shared memories.",
                "Executed through PyIceberg against the configured governed catalog.",
            ],
        )
    raise ValueError(f"Unsupported shared memory backend: {backend}")


def create_shared_memory_draft(
    content: str,
    *,
    tenant_id: str,
    scope_type: str,
    scope_key: str,
    memory_type: str,
    classification: str,
    created_by: str,
    retention_days: int | None = None,
) -> SharedMemoryDraft:
    return SharedMemoryDraft(
        content=content,
        summary=prompt_preview(content, limit=120),
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_key=scope_key,
        memory_type=memory_type,
        classification=classification,
        created_by=created_by,
        expires_at=(datetime.now(UTC) + timedelta(days=retention_days))
        if retention_days
        else None,
    )


def check_shared_memory_backend(config: SharedMemoryConfig) -> SharedMemoryBackendCheck:
    if config.backend == "postgres":
        return PostgresSharedMemoryStore(config).check()
    if config.backend == "iceberg":
        return IcebergSharedMemoryStore(config).check()
    return SharedMemoryBackendCheck(
        backend=config.backend,
        ok=False,
        messages=["Unsupported shared memory backend."],
    )


def render_or_commit_shared_draft(
    config: LoroConfig,
    draft: SharedMemoryDraft,
    *,
    execute: bool = False,
) -> SharedMemoryCommitResult:
    backend = config.memory.shared.backend
    authorized_tenant = _authorized_tenant(config, draft.tenant_id)
    if backend == "postgres":
        store = PostgresSharedMemoryStore(
            config.memory.shared, authorized_tenant_id=authorized_tenant
        )
        if execute:
            store.commit_draft(draft)
            return SharedMemoryCommitResult(backend=backend, draft=draft, executed=True)
        return SharedMemoryCommitResult(
            backend=backend,
            draft=draft,
            executed=False,
            statement=store.render_insert(draft),
        )
    if backend == "iceberg":
        store = IcebergSharedMemoryStore(
            config.memory.shared, authorized_tenant_id=authorized_tenant
        )
        if execute:
            store.commit_draft(draft)
            return SharedMemoryCommitResult(backend=backend, draft=draft, executed=True)
        return SharedMemoryCommitResult(
            backend=backend,
            draft=draft,
            executed=False,
            statement=store.render_insert(draft),
        )
    raise ValueError(f"Unsupported shared memory backend: {backend}")


def apply_shared_memory_lifecycle(
    config: LoroConfig,
    request: SharedMemoryLifecycleRequest,
    *,
    execute: bool = False,
) -> SharedMemoryLifecycleResult:
    _validate_lifecycle_request(request)
    backend = config.memory.shared.backend
    authorized_tenant = _authorized_tenant(config, request.tenant_id)
    if backend == "postgres":
        store = PostgresSharedMemoryStore(
            config.memory.shared, authorized_tenant_id=authorized_tenant
        )
    elif backend == "iceberg":
        store = IcebergSharedMemoryStore(
            config.memory.shared, authorized_tenant_id=authorized_tenant
        )
    else:
        raise ValueError(f"Unsupported shared memory backend: {backend}")
    statement = store.render_lifecycle(request)
    if execute:
        store.apply_lifecycle(request)
    return SharedMemoryLifecycleResult(
        backend=backend,
        request=request,
        executed=execute,
        statement=statement,
    )


def _validate_lifecycle_request(request: SharedMemoryLifecycleRequest) -> None:
    if not request.memory_id.strip() or not request.tenant_id.strip() or not request.actor.strip():
        raise ValueError("Memory lifecycle identity fields must be non-empty.")
    if not request.reason.strip():
        raise ValueError("Memory lifecycle operations require a reason.")
    if request.action == "correct" and (not request.content or not request.summary):
        raise ValueError("Memory correction requires replacement content and summary.")
    if request.action == "expire" and request.expires_at is None:
        raise ValueError("Memory expiration requires expires_at.")


def _authorized_tenant(config: LoroConfig, requested_tenant: str) -> str | None:
    if config.memory.shared.tenant_isolation != "identity":
        return None
    identity_tenant = resolve_identity(config.identity).tenant
    if requested_tenant != identity_tenant:
        raise PermissionError(f"Cross-tenant shared-memory access denied: {requested_tenant}")
    return identity_tenant
