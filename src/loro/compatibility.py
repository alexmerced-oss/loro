from __future__ import annotations

import warnings


class LoroDeprecationWarning(DeprecationWarning):
    """Warning for a Loro surface scheduled for removal or incompatible change."""


def warn_deprecated(
    feature: str,
    *,
    removal_version: str,
    replacement: str | None = None,
) -> None:
    message = f"{feature} is deprecated and is scheduled for removal in Loro {removal_version}."
    if replacement:
        message += f" Use {replacement} instead."
    warnings.warn(message, LoroDeprecationWarning, stacklevel=2)


__all__ = ["LoroDeprecationWarning", "warn_deprecated"]
