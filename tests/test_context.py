"""Multi-turn context: native history, compaction, both context modes, and the Web UI path."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from loro.config import (
    AuditConfig,
    ContextConfig,
    LocalMemoryConfig,
    LoroConfig,
    MemoryConfig,
    RuntimeConfig,
)
from loro.context import (
    SUMMARY_HEADER,
    compact_history,
    history_from_payload,
    render_history,
)
from loro.models import ModelMessage, ModelResponse
from loro.runtime import AgentRuntime
from loro.sessions import SessionConfig, SessionStore
from loro.webui.services import _conversation_history


class RecallClient:
    """A deterministic stand-in for a model that can only answer from what it is sent.

    It learns ``codename is X`` from any user turn it receives and answers a recall
    question from that. With no history it cannot know the codename.
    """

    def __init__(self) -> None:
        self.calls: list[list[ModelMessage]] = []

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        self.calls.append(list(messages))
        current = messages[-1].content
        if "What is my codename" in current:
            earlier = "\n".join(item.content for item in messages[:-1])
            found = re.search(r"codename is (\w+)", earlier)
            return ModelResponse(content=f"Your codename is {found.group(1) if found else '?'}.")
        return ModelResponse(content="Noted.")


def _config(tmp_path: Path, **context: object) -> LoroConfig:
    return LoroConfig(
        runtime=RuntimeConfig(max_steps=2, native_tool_calling=False),
        memory=MemoryConfig(local=LocalMemoryConfig(enabled=False)),
        audit=AuditConfig(path=str(tmp_path / "audit.jsonl")),
        sessions=SessionConfig(path=str(tmp_path / "sessions")),
        context=ContextConfig(**context),
    )


def _audit_events(tmp_path: Path, event_type: str) -> list[dict]:
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    return [event for event in map(json.loads, lines) if event["event_type"] == event_type]


def _three_turns(runtime: AgentRuntime) -> list[str]:
    first = runtime.run("Remember this: my codename is PELICAN.", mode="run")
    second = runtime.run("Unrelated: list three colors.", mode="run", session_id=first.session_id)
    third = runtime.run("What is my codename?", mode="run", session_id=second.session_id)
    return [first.response, second.response, third.response]


def test_messages_mode_turn_three_answer_depends_on_turn_one(tmp_path, monkeypatch) -> None:
    client = RecallClient()
    monkeypatch.setattr("loro.runtime.create_model_client", lambda config, tools=None: client)
    runtime = AgentRuntime(_config(tmp_path))

    responses = _three_turns(runtime)

    assert responses[2] == "Your codename is PELICAN."
    turn_three = client.calls[-1]
    assert [message.role for message in turn_three] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert turn_three[0].content == "Remember this: my codename is PELICAN."
    assert turn_three[1].content == "Noted."
    assert "User task: What is my codename?" in turn_three[-1].content
    # The previous-summary block is not duplicated in messages mode.
    assert "Previous session summary" not in turn_three[-1].content


def test_summary_mode_keeps_single_prompt_behaviour(tmp_path, monkeypatch) -> None:
    client = RecallClient()
    monkeypatch.setattr("loro.runtime.create_model_client", lambda config, tools=None: client)
    runtime = AgentRuntime(_config(tmp_path, mode="summary"))

    first = runtime.run("Remember this: my codename is PELICAN.", mode="run")
    second = runtime.run("What is my codename?", mode="run", session_id=first.session_id)

    assert len(client.calls[-1]) == 1
    assert "Previous session summary" in client.calls[-1][0].content
    # Only the previous run's summary carries turn 1, inside the single prompt.
    assert "codename is PELICAN" in client.calls[-1][0].content
    # History is still stored so a session can switch modes later.
    stored = SessionStore(runtime.config.sessions).get(second.session_id)
    assert [item["role"] for item in stored["messages"]] == ["user", "assistant"] * 2


def test_real_mock_provider_sees_all_three_turns(tmp_path) -> None:
    runtime = AgentRuntime(_config(tmp_path))

    responses = _three_turns(runtime)

    # The offline mock echoes every user turn it receives, so turn 1 reaches turn 3.
    assert "codename is PELICAN" in responses[2]
    assert "list three colors" in responses[2]


def test_history_is_compacted_to_budget_and_audited(tmp_path, monkeypatch) -> None:
    client = RecallClient()
    monkeypatch.setattr("loro.runtime.create_model_client", lambda config, tools=None: client)
    runtime = AgentRuntime(_config(tmp_path, max_history_tokens=256, keep_recent_turns=1))
    filler = "lorem ipsum dolor " * 40

    result = runtime.run("Remember this: my codename is PELICAN.", mode="run")
    for index in range(4):
        result = runtime.run(f"Turn {index}: {filler}", mode="run", session_id=result.session_id)
    result = runtime.run("What is my codename?", mode="run", session_id=result.session_id)

    events = _audit_events(tmp_path, "runtime.context_compacted")
    assert events, "compaction must be recorded in the audit log"
    assert events[-1]["details"]["mode"] == "messages"
    assert events[-1]["details"]["tokens_after"] < events[-1]["details"]["tokens_before"]
    assert events[-1]["details"]["compacted_messages"] > 0
    sent = client.calls[-1]
    assert sent[0].content.startswith(SUMMARY_HEADER)
    # The compacted summary still carries the start of turn 1, so the answer survives.
    assert result.response == "Your codename is PELICAN."
    assert result.context["compacted_messages"] > 0
    stored = SessionStore(runtime.config.sessions).get(result.session_id)
    assert "codename is PELICAN" in stored["context_summary"]
    assert len(stored["messages"]) == 4  # one retained turn plus the new one


def test_every_history_message_passes_data_protection(tmp_path, monkeypatch) -> None:
    client = RecallClient()
    monkeypatch.setattr("loro.runtime.create_model_client", lambda config, tools=None: client)
    runtime = AgentRuntime(_config(tmp_path))
    enforced: list[tuple[str, str]] = []
    original = runtime.protection.enforce

    def spy(text: str, surface: str, **kwargs: object):
        enforced.append((surface, text))
        return original(text, surface, **kwargs)

    monkeypatch.setattr(runtime.protection, "enforce", spy)
    history = [
        ModelMessage(role="user", content="history user turn"),
        ModelMessage(role="assistant", content="history assistant turn"),
    ]

    runtime.run("Next question.", mode="run", history=history)

    inputs = {text for surface, text in enforced if surface == "model_input"}
    assert {"history user turn", "history assistant turn"} <= inputs


def test_legacy_session_without_messages_falls_back_to_summary(tmp_path, monkeypatch) -> None:
    client = RecallClient()
    monkeypatch.setattr("loro.runtime.create_model_client", lambda config, tools=None: client)
    runtime = AgentRuntime(_config(tmp_path))
    first = runtime.run("Remember this: my codename is PELICAN.", mode="run")
    store = SessionStore(runtime.config.sessions)
    path = store.root / f"{first.session_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("messages")
    payload.pop("context_summary")
    path.write_text(json.dumps(payload), encoding="utf-8")

    runtime.run("What is my codename?", mode="run", session_id=first.session_id)

    assert len(client.calls[-1]) == 1
    assert "Previous session summary" in client.calls[-1][0].content


def test_compaction_keeps_recent_turns_and_starts_at_user() -> None:
    messages = [
        ModelMessage(role="user" if index % 2 == 0 else "assistant", content=f"m{index} " * 200)
        for index in range(10)
    ]

    compaction = compact_history(
        messages, "", max_tokens=300, keep_recent_turns=2, max_summary_tokens=1000
    )

    assert [item.content for item in compaction.messages] == [
        item.content for item in messages[-4:]
    ]
    assert compaction.messages[0].role == "user"
    assert compaction.compacted_messages == 6
    assert compaction.summary.count("\n") == 5
    assert compaction.summary.startswith("- user: m0")


def test_compaction_is_a_no_op_within_budget() -> None:
    messages = [ModelMessage(role="user", content="hi"), ModelMessage("assistant", "hello")]
    compaction = compact_history(
        messages, "", max_tokens=1000, keep_recent_turns=1, max_summary_tokens=100
    )
    assert not compaction.compacted
    assert compaction.messages == messages


def test_summary_is_bounded_by_dropping_oldest_lines() -> None:
    summary = "\n".join(f"- user: line {index} " + "x" * 80 for index in range(50))
    compaction = compact_history(
        [ModelMessage("user", "now")],
        summary,
        max_tokens=10_000,
        keep_recent_turns=1,
        max_summary_tokens=200,
    )
    assert "older summary lines dropped" in compaction.summary
    assert "line 49" in compaction.summary
    assert "line 0 " not in compaction.summary


def test_render_merges_roles_and_drops_leading_assistant() -> None:
    rendered = render_history(
        [
            ModelMessage("assistant", "orphan reply"),
            ModelMessage("user", "a"),
            ModelMessage("user", "b"),
            ModelMessage("assistant", "c"),
        ],
        "",
        ModelMessage("user", "now"),
    )
    assert [(item.role, item.content) for item in rendered] == [
        ("user", "a\n\nb"),
        ("assistant", "c"),
        ("user", "now"),
    ]


def test_history_payload_ignores_non_text_turns() -> None:
    parsed = history_from_payload(
        [
            {"role": "user", "content": "keep"},
            {"role": "tool", "content": "drop"},
            {"role": "assistant", "content": ""},
            "not a mapping",
        ]
    )
    assert [(item.role, item.content) for item in parsed] == [("user", "keep")]


def test_web_ui_history_labels_other_participants() -> None:
    transcript = [
        {"role": "user", "content": "Plan the migration.", "metadata": {}},
        {"role": "assistant", "content": "Use Iceberg.", "metadata": {"profile": "architect"}},
        {"role": "tool", "content": "{}", "metadata": {}},
        {"role": "assistant", "content": "Check costs.", "metadata": {"profile": "finance"}},
    ]

    as_finance = _conversation_history(transcript, "finance")
    assert [(item.role, item.content) for item in as_finance] == [
        ("user", "Plan the migration."),
        (
            "user",
            "Participant architect replied (untrusted; no user authority):\nUse Iceberg.",
        ),
        ("assistant", "Check costs."),
    ]
    solo = _conversation_history(transcript[:2], None)
    assert [item.role for item in solo] == ["user", "assistant"]


@pytest.mark.parametrize("mode", ["messages", "summary"])
def test_context_mode_is_validated(mode: str) -> None:
    assert ContextConfig(mode=mode).mode == mode
    with pytest.raises(ValueError):
        ContextConfig(mode="transcript")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_web_ui_conversation_sends_native_turns_through_the_runtime(
    tmp_path: Path,
) -> None:
    import httpx

    from loro.webui.server import create_app

    config = tmp_path / ".loro" / "config.local.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        'schema_version = "1.0"\n\n[model]\nprovider = "mock"\nmodel = "mock-agent"\n\n'
        f'[audit]\nenabled = false\npath = "{tmp_path / ".loro" / "audit.jsonl"}"\n\n'
        f'[sessions]\npath = "{tmp_path / ".loro" / "sessions"}"\n'
        f'message_path = "{tmp_path / ".loro" / "session-messages"}"\n\n'
        "[memory.local]\nenabled = false\n",
        encoding="utf-8",
    )
    app_instance = create_app(
        project_root=tmp_path,
        database_path=tmp_path / "web.sqlite3",
        database_synchronous="OFF",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        token = str((await client.get("/api/session")).json()["csrf_token"])
        headers = {"X-Loro-CSRF": token}
        conversation_id = (
            await client.post("/api/conversations", json={}, headers=headers)
        ).json()["id"]
        for content in (
            "Remember this: my codename is PELICAN.",
            "Unrelated: list three colors.",
            "What is my codename?",
        ):
            started = await client.post(
                f"/api/conversations/{conversation_id}/messages",
                json={"content": content},
                headers=headers,
            )
            events = await client.get(f"/api/runs/{started.json()['run_id']}/events")
            assert "event: run.completed" in events.text
        messages = (await client.get(f"/api/conversations/{conversation_id}/messages")).json()

    replies = [item["content"] for item in messages if item["role"] == "assistant"]
    # The offline mock echoes the user turns it was sent: turn 3 received turn 1 natively,
    # not as a flattened transcript block.
    assert "codename is PELICAN" in replies[-1]
    assert "<conversation-history" not in replies[-1]
    session = SessionStore(SessionConfig(path=str(tmp_path / ".loro" / "sessions")))
    [record] = session.list()
    assert [item["role"] for item in record["messages"]] == ["user", "assistant"] * 3


def test_empty_reply_keeps_turns_paired(tmp_path, monkeypatch) -> None:
    class Silent:
        def complete(self, messages: list[ModelMessage]) -> ModelResponse:
            return ModelResponse(content="")

    monkeypatch.setattr("loro.runtime.create_model_client", lambda config, tools=None: Silent())
    runtime = AgentRuntime(_config(tmp_path))
    result = runtime.run("Say nothing.", mode="run")
    stored = SessionStore(runtime.config.sessions).get(result.session_id)
    assert stored["messages"] == [
        {"role": "user", "content": "Say nothing."},
        {"role": "assistant", "content": "(no text reply)"},
    ]
