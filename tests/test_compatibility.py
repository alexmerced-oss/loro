from __future__ import annotations

import pytest

from loro.compatibility import LoroDeprecationWarning, warn_deprecated


def test_deprecation_warning_names_replacement_and_removal_version() -> None:
    with pytest.warns(LoroDeprecationWarning, match="Loro 0.7") as captured:
        warn_deprecated("legacy.option", removal_version="0.7", replacement="current.option")

    assert "current.option" in str(captured[0].message)
