class IcebergSharedMemoryStore:
    """Iceberg shared memory backend placeholder.

    The implementation will use PyIceberg or the Iceberg REST catalog and must
    preserve append-only auditability through Iceberg snapshots.
    """

    def remember(self, content: str, scope: str = "shared") -> None:
        raise NotImplementedError("Iceberg shared memory is not implemented yet.")
