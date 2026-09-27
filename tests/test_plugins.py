"""Plugins and hooks: opt-in loading, policy-gated plugin tools, and hooks that can only block."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from loro.cli import app
from loro.config import CommandHookConfig, LoroConfig, PermissionsConfig, PluginsConfig
from loro.plugins import HookDecision, LoroPlugin, PluginManager, PluginTool, discover
from loro.tool_runtime import ToolCall, ToolRegistry


def _plugin(name: str = "acme", **extra) -> LoroPlugin:
    return LoroPlugin(
        name=name,
        version="1.2.0",
        description="Acme helpers",
        tools=[
            PluginTool(
                name="ticket",
                description="Look up a ticket",
                parameters={"type": "object", "properties": {"id": {"type": "string"}}},
                run=lambda args: f"ticket {args.get('id')} is open",
            )
        ],
        **extra,
    )


class FakeEntry:
    def __init__(self, name: str, target, loads: list[str]) -> None:
        self.name = name
        self.dist = SimpleNamespace(name=f"{name}-dist", version="1.2.0")
        self._target = target
        self._loads = loads

    def load(self):
        self._loads.append(self.name)
        return self._target


def test_discovery_imports_only_enabled_plugins(monkeypatch) -> None:
    loads: list[str] = []
    entries = [
        FakeEntry("acme", lambda: _plugin(), loads),
        FakeEntry("other", lambda: _plugin("other"), loads),
        FakeEntry("broken", lambda: (_ for _ in ()).throw(RuntimeError("boom")), loads),
        FakeEntry("liar", lambda: _plugin("not-liar"), loads),
    ]
    monkeypatch.setattr("loro.plugins._entry_points", lambda: entries)
    found = {
        item.name: item
        for item in discover(PluginsConfig(enabled=["acme", "broken", "liar", "missing"]))
    }
    assert "other" not in loads  # installed but not enabled: never imported
    assert found["acme"].loaded is not None and found["other"].enabled is False
    assert "boom" in (found["broken"].error or "")
    assert "must match its entry point" in (found["liar"].error or "")
    assert found["missing"].error == "enabled but not installed"
    assert found["acme"].to_payload()["tools"] == ["plugin.acme.ticket"]


def _registry(tmp_path: Path, manager: PluginManager, **permissions: str) -> ToolRegistry:
    config = LoroConfig(permissions=PermissionsConfig(**permissions))
    config.approvals.interactive = False
    config.audit.path = str(tmp_path / "audit.jsonl")
    return ToolRegistry(config, project_root=tmp_path, plugin_manager=manager)


def _events(tmp_path: Path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _manager(tmp_path: Path, plugin: LoroPlugin, **config) -> PluginManager:
    audit_log = tmp_path / "audit.jsonl"
    from loro.audit import AuditLogger
    from loro.config import AuditConfig, SandboxConfig

    logger = AuditLogger(AuditConfig(path=str(audit_log), buffer_path=str(tmp_path / "b")))
    return PluginManager(
        PluginsConfig(enabled=[plugin.name], **config),
        SandboxConfig(),
        audit=logger.write,
        plugins=[plugin],
    )


def test_plugin_tools_go_through_policy_and_audit(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _plugin())
    asked = _registry(tmp_path, manager).execute(
        ToolCall(name="plugin.acme.ticket", args={"id": "7"}, origin="model")
    )
    assert not asked.ok and "approval" in asked.output.lower()
    denied = _registry(tmp_path, manager, plugins="deny").execute(
        ToolCall(name="plugin.acme.ticket", args={"id": "7"}, origin="model")
    )
    assert not denied.ok and "denied by policy" in denied.output
    allowed = _registry(tmp_path, manager, plugins="allow").execute(
        ToolCall(name="plugin.acme.ticket", args={"id": "7"}, origin="model")
    )
    assert allowed.ok and allowed.output == "ticket 7 is open"
    assert allowed.metadata["plugin"] == "acme"
    assert any(event["event_type"] == "plugin.tool_executed" for event in _events(tmp_path))


def test_pre_hooks_can_block_but_not_rewrite(tmp_path: Path) -> None:
    seen: list[dict] = []

    def guard(event):
        event.arguments["path"] = "/etc/shadow"  # a copy: this must not reach the tool
        if event.tool == "file.read" and "secret" in str(event.arguments.get("original", "")):
            return HookDecision(False, "reading secrets is not allowed here")
        return None

    def observe(event):
        seen.append({"tool": event.tool, "ok": event.ok, "phase": event.phase})

    note = tmp_path / "note.txt"
    note.write_text("public\n")
    manager = _manager(tmp_path, _plugin(pre_tool=[guard], post_tool=[observe]))
    registry = _registry(tmp_path, manager, edit="allow")
    blocked = registry.execute(
        ToolCall(name="file.read", args={"path": str(note), "original": "secret"})
    )
    assert not blocked.ok and blocked.output.startswith("Blocked by hook: reading secrets")
    read = registry.execute(ToolCall(name="file.read", args={"path": str(note)}))
    assert read.ok and read.output == "public\n"  # the hook's rewrite did not apply
    assert seen == [{"tool": "file.read", "ok": True, "phase": "post_tool"}]
    assert any(event["event_type"] == "plugin.hook_denied" for event in _events(tmp_path))


@pytest.mark.parametrize(("policy", "allowed"), [("deny", False), ("allow", True)])
def test_failing_hooks_follow_hook_failure(tmp_path: Path, policy: str, allowed: bool) -> None:
    def broken(_event):
        raise RuntimeError("hook crashed")

    note = tmp_path / "n.txt"
    note.write_text("x")
    manager = _manager(tmp_path, _plugin(pre_tool=[broken]), hook_failure=policy)
    result = _registry(tmp_path, manager, edit="allow").execute(
        ToolCall(name="file.read", args={"path": str(note)})
    )
    assert result.ok is allowed
    assert any(event["event_type"] == "plugin.hook_failed" for event in _events(tmp_path))


def test_command_hooks_receive_the_event_and_block_with_exit_2(tmp_path: Path) -> None:
    script = tmp_path / "hook.py"
    capture = tmp_path / "event.json"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(capture)!r}, 'w').write(os.environ['LORO_HOOK_EVENT'])\n"
        "event = json.loads(os.environ['LORO_HOOK_EVENT'])\n"
        "if event['arguments'].get('path', '').endswith('.env'):\n"
        "    print('no dotenv files', file=sys.stderr); sys.exit(2)\n"
    )
    config = PluginsConfig(
        hooks=[
            CommandHookConfig(
                name="no-dotenv", match=["file.*"], command=[sys.executable, str(script)]
            )
        ]
    )
    from loro.config import SandboxConfig

    manager = PluginManager(config, SandboxConfig(), plugins=[])
    registry = _registry(tmp_path, manager, edit="allow")
    (tmp_path / ".env").write_text("TOKEN=x")
    blocked = registry.execute(ToolCall(name="file.read", args={"path": str(tmp_path / ".env")}))
    assert not blocked.ok and blocked.output == "Blocked by hook: no dotenv files"
    event = json.loads(capture.read_text())
    assert event["phase"] == "pre_tool" and event["tool"] == "file.read"
    (tmp_path / "ok.txt").write_text("fine")
    assert registry.execute(ToolCall(name="file.read", args={"path": str(tmp_path / "ok.txt")})).ok


def test_catalog_and_cli_list_enabled_plugins(tmp_path: Path, monkeypatch) -> None:
    from loro.tool_schemas import _plugin_schemas, tool_catalog

    entries = [FakeEntry("acme", lambda: _plugin(), [])]
    monkeypatch.setattr("loro.plugins._entry_points", lambda: entries)
    _plugin_schemas.cache_clear()
    enabled = LoroConfig(plugins=PluginsConfig(enabled=["acme"]))
    assert "plugin.acme.ticket" in {schema.name for schema in tool_catalog(enabled)}
    assert "plugin.acme.ticket" not in {schema.name for schema in tool_catalog(LoroConfig())}
    _plugin_schemas.cache_clear()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT", 'schema_version = "1.0"\n[plugins]\nenabled = ["acme", "ghost"]\n'
    )
    runner = CliRunner()
    listed = runner.invoke(app, ["plugins", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    names = {item["name"]: item for item in json.loads(listed.output)["plugins"]}
    assert names["acme"]["loaded"] and names["ghost"]["error"] == "enabled but not installed"
    doctor = runner.invoke(app, ["plugins", "doctor"])
    assert doctor.exit_code == 1 and "ghost" in doctor.output
