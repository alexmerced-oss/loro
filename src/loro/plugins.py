"""Hooks and plugins: extend Loro without widening its authority.

Two extension points, both opt-in:

* **Plugins** are Python packages that expose a ``loro.plugins`` entry point returning a
  :class:`LoroPlugin`. Installing a package does nothing: a plugin is loaded only when its name
  is listed in ``[plugins] enabled``. A plugin may contribute tools (``plugin.<name>.<tool>``)
  and ``pre_tool`` / ``post_tool`` hooks.
* **Command hooks** are executables configured in ``[[plugins.hooks]]``. They run in a sandbox
  profile with the tool event as JSON in ``LORO_HOOK_EVENT``; exit status 0 allows the call and
  2 blocks it (stderr is the reason).

Hooks can only narrow what happens. A ``pre_tool`` hook can block a call but cannot approve one,
change its arguments, or grant permissions; a ``post_tool`` hook observes the result and cannot
change it. Plugin tools go through the same permission policy (``permissions.plugins``),
approvals, data protection and audit as built-in tools, and an Open Agent Profile must list a
plugin tool by name before an agent can use it. Every plugin load, tool call, hook decision and
hook failure is audited.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from loro.config import CommandHookConfig, PluginsConfig, SandboxConfig
from loro.tool_schemas import ToolSchema

ENTRY_POINT_GROUP = "loro.plugins"
MAX_EVENT_BYTES = 16_384
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class PluginError(RuntimeError):
    """A plugin could not be loaded or violated the plugin contract."""


@dataclass(frozen=True)
class ToolEvent:
    """What a hook sees: the call, never secrets beyond the (truncated) arguments."""

    phase: Literal["pre_tool", "post_tool"]
    tool: str
    arguments: Mapping[str, Any]
    origin: str
    subject: str
    session_id: str | None = None
    ok: bool | None = None
    output_preview: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "phase": self.phase,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "origin": self.origin,
            "subject": self.subject,
            "session_id": self.session_id,
        }
        if self.phase == "post_tool":
            payload["ok"] = self.ok
            payload["output_preview"] = self.output_preview
        text = json.dumps(payload, default=str)
        if len(text.encode("utf-8")) > MAX_EVENT_BYTES:
            payload["arguments"] = {"truncated": True}
            payload["output_preview"] = (self.output_preview or "")[:1000] or None
        return payload


@dataclass(frozen=True)
class HookDecision:
    allow: bool = True
    reason: str = ""


PreHook = Callable[[ToolEvent], HookDecision | None]
PostHook = Callable[[ToolEvent], None]


@dataclass(frozen=True)
class PluginTool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[Mapping[str, Any]], str]
    risk_reason: str = "Run a tool provided by a Loro plugin."


@dataclass
class LoroPlugin:
    """What a ``loro.plugins`` entry point returns (or a zero-argument callable returning it)."""

    name: str
    version: str = "0"
    description: str = ""
    tools: list[PluginTool] = field(default_factory=list)
    pre_tool: list[PreHook] = field(default_factory=list)
    post_tool: list[PostHook] = field(default_factory=list)


@dataclass(frozen=True)
class InstalledPlugin:
    name: str
    distribution: str | None
    version: str | None
    enabled: bool
    loaded: LoroPlugin | None = None
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        plugin = self.loaded
        return {
            "name": self.name,
            "distribution": self.distribution,
            "version": self.version,
            "enabled": self.enabled,
            "loaded": plugin is not None,
            "error": self.error,
            "description": plugin.description if plugin else "",
            "tools": [f"plugin.{self.name}.{tool.name}" for tool in plugin.tools] if plugin else [],
            "hooks": (
                {"pre_tool": len(plugin.pre_tool), "post_tool": len(plugin.post_tool)}
                if plugin
                else {"pre_tool": 0, "post_tool": 0}
            ),
        }


def _entry_points() -> list[metadata.EntryPoint]:
    return list(metadata.entry_points(group=ENTRY_POINT_GROUP))


def discover(config: PluginsConfig) -> list[InstalledPlugin]:
    """Installed plugins; only enabled ones are imported."""

    found: list[InstalledPlugin] = []
    enabled = set(config.enabled)
    for entry in _entry_points():
        distribution = entry.dist.name if entry.dist is not None else None
        version = entry.dist.version if entry.dist is not None else None
        if entry.name not in enabled:
            found.append(InstalledPlugin(entry.name, distribution, version, False))
            continue
        try:
            found.append(InstalledPlugin(entry.name, distribution, version, True, _load(entry)))
        except Exception as error:  # noqa: BLE001 - a broken plugin is reported, not fatal
            found.append(InstalledPlugin(entry.name, distribution, version, True, error=str(error)))
    listed = {item.name for item in found}
    for name in sorted(enabled - listed):
        found.append(InstalledPlugin(name, None, None, True, error="enabled but not installed"))
    return found


def _untrusted_location(entry: metadata.EntryPoint) -> str | None:
    """Refuse distributions that live in the working directory (a cloned repo could plant one).

    ``python -m loro`` puts the current directory first on ``sys.path``, so a
    ``*.dist-info`` directory committed to a project could otherwise masquerade as an
    installed plugin.
    """

    dist = entry.dist
    if dist is None:
        return "entry point has no installed distribution"
    try:
        location = Path(str(dist.locate_file(""))).resolve()
    except (OSError, TypeError, ValueError):
        return "distribution location is unknown"
    cwd = Path.cwd().resolve()
    if location == cwd or cwd in location.parents:
        return f"distribution is inside the working directory ({location})"
    return None


def _load(entry: metadata.EntryPoint) -> LoroPlugin:
    problem = _untrusted_location(entry)
    if problem:
        raise PluginError(f"Refusing plugin {entry.name}: {problem}.")
    target = entry.load()
    plugin = target() if callable(target) and not isinstance(target, LoroPlugin) else target
    if not isinstance(plugin, LoroPlugin):
        raise PluginError(f"Entry point {entry.name} did not return a LoroPlugin.")
    if plugin.name != entry.name or not _NAME.fullmatch(plugin.name):
        raise PluginError(
            f"Plugin name {plugin.name!r} must match its entry point {entry.name!r} and use "
            "lowercase letters, digits, - or _."
        )
    for tool in plugin.tools:
        if not _NAME.fullmatch(tool.name):
            raise PluginError(f"Plugin tool name {tool.name!r} is invalid.")
    return plugin


class PluginManager:
    """The enabled plugins and command hooks for one configuration."""

    def __init__(
        self,
        config: PluginsConfig,
        sandbox: SandboxConfig,
        *,
        workspace_roots: list[str] | None = None,
        audit: Callable[..., object] | None = None,
        plugins: list[LoroPlugin] | None = None,
    ) -> None:
        self.config = config
        self.sandbox = sandbox
        self.workspace_roots = workspace_roots or []
        self.audit = audit or (lambda *_args, **_kwargs: None)
        if plugins is None:
            loaded = [item for item in discover(config) if item.loaded is not None]
            plugins = [item.loaded for item in loaded if item.loaded is not None]
            for item in loaded:
                self.audit(
                    "plugin.loaded",
                    plugin=item.name,
                    distribution=item.distribution,
                    version=item.version,
                )
        self.plugins = plugins
        self._tools = {
            f"plugin.{plugin.name}.{tool.name}": (plugin, tool)
            for plugin in plugins
            for tool in plugin.tools
        }

    @property
    def active(self) -> bool:
        return bool(self.plugins or self.config.hooks)

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name=name,
                description=f"[plugin {plugin.name}] {tool.description}",
                parameters=tool.parameters,
            )
            for name, (plugin, tool) in self._tools.items()
        ]

    def tool(self, name: str) -> tuple[LoroPlugin, PluginTool]:
        try:
            return self._tools[name]
        except KeyError as error:
            raise PluginError(f"No enabled plugin provides {name}.") from error

    # ------------------------------------------------------------------ hooks

    def before(self, event: ToolEvent) -> HookDecision:
        """Run every pre_tool hook; the first block wins. Failures follow hook_failure."""

        for plugin in self.plugins:
            for hook in plugin.pre_tool:
                decision = self._guard(plugin.name, partial(hook, event), event)
                if decision is not None and not decision.allow:
                    self.audit(
                        "plugin.hook_denied",
                        plugin=plugin.name,
                        tool=event.tool,
                        reason=decision.reason,
                    )
                    return decision
        for hook_config in self.config.hooks:
            if hook_config.event != "pre_tool" or not _matches(hook_config, event.tool):
                continue
            decision = self._guard(
                hook_config.name, partial(self._run_command, hook_config, event), event
            )
            if decision is not None and not decision.allow:
                self.audit(
                    "plugin.hook_denied",
                    plugin=hook_config.name,
                    tool=event.tool,
                    reason=decision.reason,
                )
                return decision
        return HookDecision()

    def after(self, event: ToolEvent) -> None:
        for plugin in self.plugins:
            for hook in plugin.post_tool:
                self._guard(plugin.name, partial(hook, event), event, post=True)
        for hook_config in self.config.hooks:
            if hook_config.event == "post_tool" and _matches(hook_config, event.tool):
                self._guard(
                    hook_config.name,
                    partial(self._run_command, hook_config, event),
                    event,
                    post=True,
                )

    def _guard(
        self, name: str, call: Callable[[], Any], event: ToolEvent, *, post: bool = False
    ) -> HookDecision | None:
        try:
            result = call()
        except Exception as error:  # noqa: BLE001 - a failing hook is a policy event
            self.audit(
                "plugin.hook_failed",
                plugin=name,
                tool=event.tool,
                phase=event.phase,
                error=type(error).__name__,
            )
            if post or self.config.hook_failure == "allow":
                return None
            return HookDecision(
                False,
                f"hook {name} failed ({type(error).__name__}); "
                "blocked because plugins.hook_failure = deny",
            )
        return result if isinstance(result, HookDecision) else None

    def _run_command(self, hook: CommandHookConfig, event: ToolEvent) -> HookDecision:
        from loro.sandbox import SandboxRunner

        runner = SandboxRunner(
            self.sandbox,
            workspace_roots=self.workspace_roots,
            environ={
                **_host_environment(),
                "LORO_HOOK_EVENT": json.dumps(event.to_payload(), default=str),
            },
        )
        profile = hook.sandbox_profile or self.sandbox.hook_profile
        allowlisted = self.sandbox.profiles[profile].environment_allowlist
        if "LORO_HOOK_EVENT" not in allowlisted:
            raise PluginError(
                f"Sandbox profile {profile} must allow LORO_HOOK_EVENT for command hooks."
            )
        result = runner.run(
            list(hook.command),
            profile_name=profile,
            cwd=Path(hook.cwd).expanduser() if hook.cwd else None,
            timeout=hook.timeout_seconds,
        )
        if result.returncode == 2:
            return HookDecision(False, (result.stderr or result.stdout).strip()[:500] or "blocked")
        if result.returncode != 0:
            raise PluginError(f"command hook exited {result.returncode}")
        return HookDecision()


def _matches(hook: CommandHookConfig, tool: str) -> bool:
    return any(fnmatch.fnmatchcase(tool, pattern) for pattern in hook.match)


def _host_environment() -> dict[str, str]:
    import os

    return dict(os.environ)
