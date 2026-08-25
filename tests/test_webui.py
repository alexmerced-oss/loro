from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from loro.approvals import ApprovalRequest
from loro.cli import app
from loro.webui.conversations import SCHEMA_VERSION, ConversationStore
from loro.webui.server import create_app
from loro.webui.services import RunHandle


async def _csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/session")
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def _headers(token: str) -> dict[str, str]:
    return {"X-Loro-CSRF": token}


def _prepare_project(path: Path) -> None:
    config = path / ".loro" / "config.local.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        'schema_version = "1.0"\n\n[model]\nprovider = "mock"\n'
        'model = "mock-agent"\nsmall_model = "mock-small"\n\n'
        f'[audit]\nenabled = false\npath = "{path / ".loro" / "audit.jsonl"}"\n\n'
        f'[sessions]\npath = "{path / ".loro" / "sessions"}"\n'
        f'message_path = "{path / ".loro" / "session-messages"}"\n',
        encoding="utf-8",
    )


def _profile(name: str = "reviewer") -> dict[str, object]:
    return {
        "apiVersion": "oap/v1",
        "kind": "AgentProfile",
        "metadata": {
            "name": name,
            "revision": 1,
            "description": "Reviews concrete evidence.",
        },
        "spec": {
            "role": {"instructions": "Be precise and cite evidence."},
            "tools": {"policy": "inherit"},
            "writeback": "propose",
        },
        "state": [],
        "history": [],
    }


def test_conversation_store_is_append_only_and_durable(tmp_path: Path) -> None:
    path = tmp_path / "web.sqlite3"
    store = ConversationStore(path, synchronous="OFF")
    conversation = store.create_conversation(workspace=str(tmp_path))
    first = store.add_message(conversation["id"], role="user", content="hello")
    second = store.add_message(conversation["id"], role="assistant", content="hi")

    reopened = ConversationStore(path, synchronous="OFF")
    assert [item["id"] for item in reopened.list_messages(conversation["id"])] == [
        first["id"],
        second["id"],
    ]
    assert reopened.update_conversation(conversation["id"], title="Renamed")["title"] == "Renamed"
    archived = reopened.update_conversation(conversation["id"], status="archived")
    assert archived["status"] == "archived"
    assert reopened.list_conversations() == []
    assert len(reopened.list_conversations(include_archived=True)) == 1


def test_conversation_store_validates_inputs(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "web.sqlite3", synchronous="OFF")
    conversation = store.create_conversation(workspace=str(tmp_path))
    with pytest.raises(ValueError, match="Invalid message role"):
        store.add_message(conversation["id"], role="authority", content="no")
    with pytest.raises(ValueError, match="Invalid conversation status"):
        store.update_conversation(conversation["id"], status="deleted")
    with pytest.raises(KeyError):
        store.get_conversation("missing")


@pytest.mark.asyncio
async def test_web_api_conversations_streaming_and_csrf(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    app_instance = create_app(
        project_root=tmp_path,
        database_path=tmp_path / "web.sqlite3",
        database_synchronous="OFF",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        assert (await client.post("/api/conversations", json={})).status_code == 403
        token = await _csrf(client)
        created = await client.post(
            "/api/conversations", json={}, headers=_headers(token)
        )
        assert created.status_code == 201
        conversation_id = created.json()["id"]

        started = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Reply briefly."},
            headers=_headers(token),
        )
        assert started.status_code == 202
        run_id = started.json()["run_id"]
        events = await client.get(f"/api/runs/{run_id}/events")
        assert events.status_code == 200
        assert "event: run.completed" in events.text
        messages = (await client.get(f"/api/conversations/{conversation_id}/messages")).json()
        assert [item["role"] for item in messages] == ["user", "assistant"]
        assert (await client.get(f"/api/runs/{run_id}")).json()["status"] == "completed"

        resumed = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Continue with one more sentence."},
            headers=_headers(token),
        )
        resumed_run = resumed.json()["run_id"]
        resumed_events = await client.get(f"/api/runs/{resumed_run}/events")
        assert "event: run.completed" in resumed_events.text
        messages = (await client.get(f"/api/conversations/{conversation_id}/messages")).json()
        assert [item["role"] for item in messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]


@pytest.mark.asyncio
async def test_web_api_profile_bot_and_settings_workflows(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    app_instance = create_app(
        project_root=tmp_path,
        database_path=tmp_path / "web.sqlite3",
        database_synchronous="OFF",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        token = await _csrf(client)
        created = await client.post(
            "/api/profiles", json=_profile(), headers=_headers(token)
        )
        assert created.status_code == 201, created.text
        assert created.json()["editable"] is True

        profiles = (await client.get("/api/profiles")).json()
        assert profiles[0]["name"] == "reviewer"
        effective = (await client.get("/api/profiles/reviewer/effective")).json()
        assert effective["name"] == "reviewer"

        document = (await client.get("/api/profiles/reviewer")).json()
        document["spec"]["role"]["instructions"] = "Review evidence and report risk."
        updated = await client.put(
            "/api/profiles/reviewer", json=document, headers=_headers(token)
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["metadata"]["revision"] == 2

        settings = await client.patch(
            "/api/settings",
            json={
                "provider": "mock",
                "model": "mock-agent",
                "small_model": "mock-small",
                "default_profile": "reviewer",
            },
            headers=_headers(token),
        )
        assert settings.status_code == 200, settings.text
        assert settings.json()["agent_profiles"]["default_profile"] == "reviewer"

        bot_conversation = (
            await client.post(
                "/api/conversations",
                json={"profile_name": "reviewer"},
                headers=_headers(token),
            )
        ).json()
        assert bot_conversation["profile_name"] == "reviewer"
        assert bot_conversation["profile_revision"] == 2
        bot_run = await client.post(
            f"/api/conversations/{bot_conversation['id']}/messages",
            json={"content": "Review this request."},
            headers=_headers(token),
        )
        bot_events = await client.get(f"/api/runs/{bot_run.json()['run_id']}/events")
        assert "event: run.completed" in bot_events.text


@pytest.mark.asyncio
async def test_web_api_auth_and_static_frontend(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    app_instance = create_app(
        project_root=tmp_path,
        database_path=tmp_path / "web.sqlite3",
        auth_token="secret-token",
        database_synchronous="OFF",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        assert (await client.get("/api/session")).status_code == 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_instance),
        base_url="http://test",
        headers={"Authorization": "Bearer secret-token"},
    ) as authenticated:
        assert (await authenticated.get("/api/session")).status_code == 200
        root = await authenticated.get("/")
        assert root.status_code == 200
        assert "Loro · Local agent workspace" in root.text


def test_run_handle_approval_and_cancel() -> None:
    handle = RunHandle("run", "conversation")
    request = ApprovalRequest(
        action="shell.run",
        target="pytest",
        arguments={"command": "pytest"},
        identity_subject="user",
        identity_tenant="tenant",
        identity_session_id="session",
        policy_decision="ask",
        policy_version="local-v1",
        policy_source="test",
        policy_reason="interactive",
        risk_reason="process execution",
    )
    result: list[str | None] = []
    thread = threading.Thread(
        target=lambda: result.append(handle.approval_provider(request))
    )
    thread.start()
    with handle.condition:
        handle.condition.wait_for(lambda: request.request_id in handle.approvals, timeout=2)
    handle.resolve_approval(request.request_id, "once")
    thread.join(timeout=2)
    assert result == ["once"]
    assert any(item["event"] == "approval.requested" for item in handle.events)
    handle.cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        handle.check_cancelled()


def test_web_cli_help_and_doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["web", "--help"], terminal_width=160)
    assert result.exit_code == 0
    plain_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--no-open" in "".join(plain_output.split())
    monkeypatch.chdir(tmp_path)
    doctor = runner.invoke(app, ["web", "doctor"])
    assert doctor.exit_code == 0
    assert "Web UI ready" in doctor.output


def test_web_database_schema_is_versioned(tmp_path: Path) -> None:
    path = tmp_path / "web.sqlite3"
    ConversationStore(path, synchronous="OFF")
    import sqlite3

    with sqlite3.connect(path) as connection:
        version = connection.execute("SELECT version FROM webui_schema").fetchone()[0]
        columns = connection.execute("PRAGMA table_info(messages)").fetchall()
        conversation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(conversations)")
        }
    # Pinned to the module rather than a literal, so a future migration updates
    # this test by changing one constant instead of hunting for a stray number.
    assert version == SCHEMA_VERSION
    assert {item[1] for item in columns} >= {"conversation_id", "role", "metadata_json"}
    # v2 added group rosters.
    assert "participants" in conversation_columns


def test_tool_event_metadata_is_json_serializable(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "web.sqlite3", synchronous="OFF")
    conversation = store.create_conversation(workspace=str(tmp_path))
    message = store.add_message(
        conversation["id"], role="tool", content=json.dumps({"ok": True}), metadata={"step": 1}
    )
    assert message["metadata"] == {"step": 1}
