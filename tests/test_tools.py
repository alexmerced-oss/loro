from pathlib import Path

import pytest

from loro.config import PermissionsConfig, PolarisConfig
from loro.permissions import PermissionEngine, PermissionRequest
from loro.polaris import PolarisClient
from loro.tools.files import FileTools
from loro.tools.shell import ShellTools


def test_permission_requires_approval_for_ask() -> None:
    engine = PermissionEngine(PermissionsConfig(shell="ask"))
    with pytest.raises(PermissionError):
        engine.require_allowed(PermissionRequest(tool="shell", action="run"))
    assert engine.require_allowed(PermissionRequest(tool="shell", action="run"), approved=True)


def test_file_search(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("hello loro\nanother line\n", encoding="utf-8")
    matches = FileTools().search(tmp_path, "loro")
    assert len(matches) == 1
    assert matches[0].line_number == 1


def test_shell_tool_runs_without_shell() -> None:
    result = ShellTools().run(["python", "-c", "print('loro')"])
    assert result.returncode == 0
    assert result.stdout.strip() == "loro"


def test_polaris_rejects_mutation() -> None:
    client = PolarisClient(PolarisConfig())
    with pytest.raises(PermissionError):
        client.run_readonly(["catalogs", "create", "example"])
