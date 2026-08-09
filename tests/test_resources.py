import os
from pathlib import Path

import pytest

from loro.config import PermissionRuleConfig, PermissionsConfig
from loro.permissions import PermissionEngine, PermissionRequest
from loro.resources import (
    ResourceNormalizationError,
    filesystem_resource,
    git_resource,
    memory_resource,
    polaris_resource,
    provider_resource,
    shell_resource,
)


def test_filesystem_relative_and_absolute_paths_normalize_identically(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "docs" / "note.md"
    monkeypatch.chdir(workspace)

    relative = filesystem_resource(
        "docs/../docs/note.md",
        operation="read",
        workspace_roots=[str(workspace)],
    )
    absolute = filesystem_resource(
        target,
        operation="read",
        workspace_roots=[str(workspace)],
    )

    assert relative.fields["path"] == absolute.fields["path"] == str(target)
    assert relative.fields["workspace_root"] == str(workspace)


def test_filesystem_traversal_outside_workspace_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ResourceNormalizationError, match="outside configured"):
        filesystem_resource(
            workspace / ".." / "outside.txt",
            operation="write",
            workspace_roots=[str(workspace)],
        )


def test_filesystem_symlink_escape_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ResourceNormalizationError, match="outside configured"):
        filesystem_resource(
            workspace / "linked" / "secret.txt",
            operation="read",
            workspace_roots=[str(workspace)],
        )


@pytest.mark.skipif(os.name == "nt", reason="Windows path matching is case-insensitive")
def test_structured_filesystem_path_rule_is_case_sensitive(tmp_path: Path) -> None:
    workspace = tmp_path / "Workspace"
    workspace.mkdir()
    resource = filesystem_resource(
        workspace / "Readme.md",
        operation="read",
        workspace_roots=[str(workspace)],
    )
    engine = PermissionEngine(
        PermissionsConfig(
            edit="deny",
            rules=[
                PermissionRuleConfig(
                    tool="edit",
                    action="read file",
                    resource_kind="filesystem",
                    resource={"path": f"{workspace}/readme.md"},
                    decision="allow",
                )
            ],
        )
    )

    result = engine.evaluate(PermissionRequest(tool="edit", action="read file", resource=resource))

    assert result.decision == "deny"
    assert result.matched_rule is None


def test_shell_resource_normalizes_executable_for_structured_rule() -> None:
    resource = shell_resource(["python", "-c", "print('ok')"])
    engine = PermissionEngine(
        PermissionsConfig(
            shell="allow",
            rules=[
                PermissionRuleConfig(
                    tool="shell",
                    action="run*",
                    resource_kind="shell",
                    resource={"executable_name": "python"},
                    decision="deny",
                    reason="interpreter blocked",
                )
            ],
        )
    )

    result = engine.evaluate(
        PermissionRequest(tool="shell", action="run command", resource=resource)
    )

    assert Path(str(resource.fields["executable"])).name == "python"
    assert result.decision == "deny"
    assert result.reason == "interpreter blocked"
    assert result.policy_source == "permissions.rules[0]"


def test_shell_resource_rejects_nul_encoding() -> None:
    with pytest.raises(ResourceNormalizationError, match="NUL"):
        shell_resource(["python", "bad\x00argument"])


def test_git_path_escape_fails_closed(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()

    with pytest.raises(ResourceNormalizationError, match="outside configured"):
        git_resource(
            operation="add",
            cwd=repository,
            workspace_roots=[str(repository)],
            paths=["../secret.txt"],
        )


def test_polaris_memory_and_provider_resources_are_structured() -> None:
    polaris = polaris_resource(
        ["tables", "get", "events", "--namespace", "analytics", "--catalog", "prod"]
    )
    memory = memory_resource(
        operation="search", tenant="platform", scope_type="team", scope_key="data"
    )
    provider = provider_resource(
        operation="complete", provider="openai", model="gpt-5", base_url="https://gateway"
    )

    assert polaris.fields["operation"] == "tables.get"
    assert polaris.fields["catalog"] == "prod"
    assert polaris.fields["namespace"] == "analytics"
    assert polaris.fields["resource"] == "events"
    assert memory.fields["tenant"] == "platform"
    assert provider.fields["model"] == "gpt-5"


def test_legacy_target_glob_remains_supported_with_resource() -> None:
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

    result = engine.evaluate(
        PermissionRequest(
            tool="shell",
            action="run command",
            target="rm -rf tmp",
            resource=shell_resource(["rm", "-rf", "tmp"]),
        )
    )

    assert result.decision == "deny"
