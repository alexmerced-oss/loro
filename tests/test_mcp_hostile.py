from __future__ import annotations

from types import SimpleNamespace

import pytest

from loro.config import MCPConfig, MCPExtensionConfig, MCPServerConfig
from loro.mcp.client import (
    MCPExtensionError,
    MCPProtocolError,
    _enforce_protocol_policy,
    _require_remote_extension,
)
from loro.mcp.extensions import TASKS_EXTENSION_ID, MCPExtensionRegistry


def test_negotiated_version_downgrade_and_unknown_revision_fail_closed() -> None:
    server = MCPServerConfig(
        transport="stdio",
        command="fixture-server",
        allowed_protocol_versions=["2026-07-28", "2025-11-25"],
        minimum_protocol_version="2025-11-25",
    )
    for hostile_version in ("2024-11-05", "2099-01-01", "draft"):
        with pytest.raises(MCPProtocolError, match="not allowed"):
            _enforce_protocol_policy(server, hostile_version)


def test_capability_confusion_cannot_activate_unadvertised_tasks() -> None:
    modern_without_tasks = SimpleNamespace(
        info=SimpleNamespace(protocol_version="2026-07-28", extensions=[])
    )
    with pytest.raises(MCPExtensionError, match="did not advertise"):
        _require_remote_extension(modern_without_tasks, TASKS_EXTENSION_ID)

    classic_claiming_tasks = SimpleNamespace(
        info=SimpleNamespace(protocol_version="2025-11-25", extensions=[TASKS_EXTENSION_ID])
    )
    with pytest.raises(MCPProtocolError, match="requires protocol 2026-07-28"):
        _require_remote_extension(classic_claiming_tasks, TASKS_EXTENSION_ID)


def test_unknown_extension_data_remains_inert_even_when_peer_requests_it() -> None:
    unknown = "com.example/hostile-extension"
    config = MCPConfig(
        enabled=True,
        allowed_extensions=[unknown],
        extensions={unknown: MCPExtensionConfig(version="1", adapter="tasks")},
    )
    server = MCPServerConfig(
        transport="stdio",
        command="fixture-server",
        extensions=[unknown],
    )
    status = MCPExtensionRegistry(config).statuses(server)[0]
    assert status.allowed is True
    assert status.implemented is False
    assert status.active is False
    assert "no trusted Loro adapter" in str(status.reason)
