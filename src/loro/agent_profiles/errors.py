class ProfileError(ValueError):
    """An agent profile is invalid or cannot be applied safely."""


class ConflictError(ProfileError):
    """A profile changed since a delta was created."""


class NarrowingError(ProfileError):
    """A profile cannot be safely narrowed to managed policy."""
