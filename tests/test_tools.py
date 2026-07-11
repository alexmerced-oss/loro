from pathlib import Path
from subprocess import CompletedProcess

import pytest

from loro.config import PermissionRuleConfig, PermissionsConfig, PolarisConfig
from loro.permissions import PermissionEngine, PermissionRequest
from loro.polaris import PolarisClient
from loro.tools.files import FileTools
from loro.tools.shell import ShellTools


def test_permission_requires_approval_for_ask() -> None:
    engine = PermissionEngine(PermissionsConfig(shell="ask"))
    with pytest.raises(PermissionError):
        engine.require_allowed(PermissionRequest(tool="shell", action="run"))
    assert engine.require_allowed(PermissionRequest(tool="shell", action="run"), approved=True)


def test_permission_denies_policy() -> None:
    engine = PermissionEngine(PermissionsConfig(shell="deny"))
    with pytest.raises(PermissionError, match="denied by policy"):
        engine.require_allowed(PermissionRequest(tool="shell", action="run"), approved=True)


def test_permission_rule_allows_specific_target_without_approval() -> None:
    engine = PermissionEngine(
        PermissionsConfig(
            edit="ask",
            rules=[
                PermissionRuleConfig(
                    tool="edit",
                    action="read*",
                    target="docs/*",
                    decision="allow",
                    reason="docs are readable",
                )
            ],
        )
    )
    result = engine.require_allowed(
        PermissionRequest(tool="edit", action="read file", target="docs/README.md")
    )
    assert result.decision == "allow"
    assert result.reason == "docs are readable"


def test_permission_rule_denies_specific_shell_target() -> None:
    engine = PermissionEngine(
        PermissionsConfig(
            shell="allow",
            rules=[
                PermissionRuleConfig(
                    tool="shell",
                    action="run*",
                    target="rm *",
                    decision="deny",
                )
            ],
        )
    )
    with pytest.raises(PermissionError, match="denied by policy"):
        engine.require_allowed(
            PermissionRequest(tool="shell", action="run command", target="rm -rf tmp"),
            approved=True,
        )


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


def test_polaris_typed_methods_build_readonly_commands(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, capture_output, text, check):
        calls.append(command)
        return CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("loro.polaris.run", fake_run)
    client = PolarisClient(PolarisConfig(cli_path="polaris"))

    assert client.list_catalogs().command == ["polaris", "catalogs", "list"]
    assert client.get_catalog("prod").command == ["polaris", "catalogs", "get", "prod"]
    assert client.list_namespaces(catalog="prod").command == [
        "polaris",
        "namespaces",
        "list",
        "--catalog",
        "prod",
    ]
    assert client.get_namespace("analytics", catalog="prod").command == [
        "polaris",
        "namespaces",
        "get",
        "analytics",
        "--catalog",
        "prod",
    ]
    assert client.list_tables(namespace="analytics", catalog="prod").command == [
        "polaris",
        "tables",
        "list",
        "--namespace",
        "analytics",
        "--catalog",
        "prod",
    ]
    assert client.get_table("events", namespace="analytics", catalog="prod").command == [
        "polaris",
        "tables",
        "get",
        "events",
        "--namespace",
        "analytics",
        "--catalog",
        "prod",
    ]
    assert client.list_views(namespace="analytics", catalog="prod").command == [
        "polaris",
        "views",
        "list",
        "--namespace",
        "analytics",
        "--catalog",
        "prod",
    ]
    assert client.get_view("daily_events", namespace="analytics", catalog="prod").command == [
        "polaris",
        "views",
        "get",
        "daily_events",
        "--namespace",
        "analytics",
        "--catalog",
        "prod",
    ]
    assert client.list_principal_roles().command == ["polaris", "principal-roles", "list"]
    assert client.get_principal_role("analyst").command == [
        "polaris",
        "principal-roles",
        "get",
        "analyst",
    ]
    assert client.list_catalog_roles(catalog="prod").command == [
        "polaris",
        "catalog-roles",
        "list",
        "--catalog",
        "prod",
    ]
    assert client.get_catalog_role("reader", catalog="prod").command == [
        "polaris",
        "catalog-roles",
        "get",
        "reader",
        "--catalog",
        "prod",
    ]
    assert client.list_privileges(catalog_role="reader", catalog="prod").command == [
        "polaris",
        "privileges",
        "list",
        "--catalog-role",
        "reader",
        "--catalog",
        "prod",
    ]
    assert client.list_policies(catalog="prod").command == [
        "polaris",
        "policies",
        "list",
        "--catalog",
        "prod",
    ]
    assert client.get_policy("pii-mask", catalog="prod").command == [
        "polaris",
        "policies",
        "get",
        "pii-mask",
        "--catalog",
        "prod",
    ]
    assert client.list_applicable_policies(
        "events",
        catalog="prod",
        namespace="analytics",
    ).command == [
        "polaris",
        "applicable-policies",
        "list",
        "--resource",
        "events",
        "--catalog",
        "prod",
        "--namespace",
        "analytics",
    ]
    assert len(calls) == 16
