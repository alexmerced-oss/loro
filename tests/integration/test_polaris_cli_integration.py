import os

import pytest

from loro.config import PolarisConfig
from loro.polaris import PolarisClient

pytestmark = pytest.mark.integration


def test_polaris_cli_lists_catalogs_when_enabled() -> None:
    if os.environ.get("LORO_INTEGRATION_POLARIS") != "1":
        pytest.skip("Set LORO_INTEGRATION_POLARIS=1 to run Polaris CLI integration tests.")
    cli_path = os.environ.get("LORO_POLARIS_CLI", "polaris")
    result = PolarisClient(PolarisConfig(cli_path=cli_path)).list_catalogs()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
