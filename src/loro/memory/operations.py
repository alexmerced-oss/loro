from dataclasses import dataclass

from loro.audit import prompt_preview
from loro.config import LoroConfig, SharedMemoryConfig
from loro.memory.base import (
    SharedMemoryBackendCheck,
    SharedMemoryDraft,
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


def search_shared_memories(
    config: LoroConfig,
    *,
    query: str,
    tenant_id: str = "default",
    limit: int = 20,
    execute: bool = True,
) -> SharedMemorySearchResult:
    backend = config.memory.shared.backend
    if backend == "postgres":
        store = PostgresSharedMemoryStore(config.memory.shared)
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
        statement = IcebergSharedMemoryStore(config.memory.shared).render_search(
            tenant_id=tenant_id,
            query=query,
            limit=limit,
        )
        return SharedMemorySearchResult(
            backend=backend,
            query=query,
            tenant_id=tenant_id,
            executed=False,
            statement=statement,
            messages=["Live Iceberg shared-memory search is not enabled in this MVP."],
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
    )


def check_shared_memory_backend(config: SharedMemoryConfig) -> SharedMemoryBackendCheck:
    if config.backend == "postgres":
        return PostgresSharedMemoryStore(config).check()
    if config.backend == "iceberg":
        store = IcebergSharedMemoryStore(config)
        return SharedMemoryBackendCheck(
            backend="iceberg",
            ok=False,
            messages=[
                f"Iceberg memory table: {store.memory_table}",
                f"Iceberg event table: {store.events_table}",
                "Live Iceberg commits are not enabled in this MVP.",
            ],
        )
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
    if backend == "postgres":
        store = PostgresSharedMemoryStore(config.memory.shared)
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
        if execute:
            raise ValueError("Live Iceberg commits are not enabled in this MVP.")
        return SharedMemoryCommitResult(
            backend=backend,
            draft=draft,
            executed=False,
            statement=IcebergSharedMemoryStore(config.memory.shared).render_insert(draft),
        )
    raise ValueError(f"Unsupported shared memory backend: {backend}")
