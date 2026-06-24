class PostgresSharedMemoryStore:
    """Postgres shared memory backend placeholder.

    The implementation must enforce explicit user-dictated writes and row-level
    tenant/scope filtering before becoming production-ready.
    """

    def remember(self, content: str, scope: str = "shared") -> None:
        raise NotImplementedError("Postgres shared memory is not implemented yet.")
