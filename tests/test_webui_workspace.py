from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from loro.webui.workspace import (
    ContextReference,
    ScheduleInput,
    ScheduleStore,
    UploadInput,
    WorkspaceService,
)


def test_workspace_context_is_bounded_and_confined(tmp_path: Path) -> None:
    (tmp_path / "brief.md").write_text("governed evidence", encoding="utf-8")
    (tmp_path / ".loro").mkdir()
    (tmp_path / ".loro" / "config.local.toml").write_text("secret state", encoding="utf-8")
    service = WorkspaceService(tmp_path)

    files = service.files()
    prompt, manifest = service.context([ContextReference(path="brief.md")])

    assert [item["path"] for item in files] == ["brief.md"]
    assert "governed evidence" in prompt
    assert manifest[0]["delivery"] == "inline"
    with pytest.raises(ValueError, match="inside the active project"):
        service.context([ContextReference(path="../outside.txt")])
    with pytest.raises(ValueError, match="Internal Loro state"):
        service.context([ContextReference(path=".loro/config.local.toml")])


def test_workspace_uploads_and_previews_binary_context_by_path(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path)
    uploaded = service.save_upload(
        "conversation-1",
        UploadInput(
            name="../diagram.png",
            media_type="image/png",
            content_base64=base64.b64encode(b"png-data").decode(),
        ),
    )
    prompt, manifest = service.context([ContextReference(path=uploaded["path"])])
    content, media_type, name = service.read(uploaded["path"])

    assert "Attachment at" in prompt
    assert manifest[0]["delivery"] == "workspace_path"
    assert content == b"png-data"
    assert media_type == "image/png"
    assert name.endswith("-diagram.png")


def test_schedule_store_runs_due_graphs_and_records_refusals(tmp_path: Path) -> None:
    handles: list[str] = []

    def start(path: str):
        handles.append(path)
        return SimpleNamespace(run_id=f"run-{len(handles)}")

    store = ScheduleStore(tmp_path, start)
    created = store.create(ScheduleInput(graph_path="release.agraph.yaml", interval_minutes=5))
    due = datetime.fromisoformat(created["next_run_at"]) + timedelta(seconds=1)
    changed = store.tick(due)

    assert handles == ["release.agraph.yaml"]
    assert changed[0]["last_run_id"] == "run-1"
    paused = store.update(created["id"], False)
    assert paused["enabled"] is False
    assert store.tick(datetime.now(UTC) + timedelta(days=1)) == []


def test_schedule_store_records_governed_start_error(tmp_path: Path) -> None:
    def refuse(_path: str):
        raise PermissionError("managed policy denied the graph")

    store = ScheduleStore(tmp_path, refuse)
    created = store.create(ScheduleInput(graph_path="blocked.agraph.yaml", interval_minutes=1))
    changed = store.tick(datetime.fromisoformat(created["next_run_at"]) + timedelta(seconds=1))
    assert changed[0]["last_run_id"] is None
    assert "managed policy denied" in changed[0]["last_error"]


def test_extension_inventory_exposes_editable_structure_without_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    config = SimpleNamespace(
        skills=SimpleNamespace(
            enabled=False,
            managed_paths=[],
            allow_user=False,
            user_paths=[],
            allow_project=False,
            project_paths=[],
            max_files=20,
        ),
        mcp=SimpleNamespace(
            enabled=True,
            servers={
                "private": SimpleNamespace(
                    enabled=True,
                    transport="streamable_http",
                    url="https://example.test/mcp",
                    command=None,
                    extensions=[],
                )
            },
            extensions={
                "tasks": SimpleNamespace(
                    model_dump=lambda **_kwargs: {
                        "enabled": True,
                        "version": "1",
                        "adapter": "tasks",
                    }
                )
            },
        ),
    )
    monkeypatch.setattr("loro.webui.workspace.load_config", lambda _root: config)

    result = WorkspaceService(tmp_path).extensions()
    server = result["mcp_servers"][0]

    assert server["configured"] is True
    assert "target" not in server
    assert server["url"] == "https://example.test/mcp"


def test_manage_project_skill_lifecycle(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path)
    saved = service.manage_extension(
        {
            "kind": "skill",
            "name": "release-review",
            "description": "Review releases",
            "body": "Check tests and evidence.",
        }
    )
    assert saved["ok"] is True
    assert (tmp_path / ".loro/skills/release-review/SKILL.md").is_file()
    inventory = service.extensions()
    skill = next(item for item in inventory["skills"] if item["name"] == "release-review")
    assert skill["editable"] is True
    assert skill["body"] == "Check tests and evidence."
    service.manage_extension({"kind": "skill", "action": "delete", "name": "release-review"})
    assert not (tmp_path / ".loro/skills/release-review").exists()


def test_manage_mcp_server_lifecycle(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path)
    service.manage_extension(
        {
            "kind": "mcp",
            "name": "docs",
            "transport": "streamable_http",
            "url": "https://example.test/mcp",
            "protocol_mode": "auto",
            "enabled": True,
        }
    )
    server = next(item for item in service.extensions()["mcp_servers"] if item["name"] == "docs")
    assert server["url"] == "https://example.test/mcp"
    service.manage_extension({"kind": "mcp", "action": "delete", "name": "docs"})
    assert service.extensions()["mcp_servers"] == []
