from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from loro.config import LoroConfig
from loro.repl import _render_header, run_repl


def _render(config: LoroConfig, *, width: int, session_id: str | None = None) -> str:
    stream = StringIO()
    console = Console(file=stream, width=width, color_system=None)
    _render_header(config, console, session_id=session_id, agent_name="reviewer")
    return stream.getvalue()


def test_repl_header_keeps_parrot_and_metadata_in_one_responsive_panel() -> None:
    config = LoroConfig()

    wide = _render(config, width=96)
    narrow = _render(config, width=36)

    for rendered, width in ((wide, 96), (narrow, 36)):
        assert rendered.count("╭") == 1
        assert "Loro | interactive workspace" in rendered
        assert "/ 6  6" in rendered
        assert "Provider" in rendered
        assert "mock-agent" in rendered
        assert "SESSION NEW" in rendered
        assert "MEM LOCAL" in rendered
        assert max(len(line) for line in rendered.splitlines()) <= width


def test_repl_header_renders_dynamic_runtime_markers() -> None:
    config = LoroConfig()
    config.model.provider = "opencode-go"
    config.model.model = "glm-5.3"
    config.memory.shared.enabled = True
    config.memory.shared.backend = "iceberg"
    config.sandbox.enabled = False
    config.audit.sink = "http"

    rendered = _render(config, width=96, session_id="session-123")

    assert "SESSION ACTIVE" in rendered
    assert "MEM LOCAL+ICEBERG" in rendered
    assert "SANDBOX OFF" in rendered
    assert "AUDIT HTTP" in rendered
    assert "opencode-go" in rendered
    assert "glm-5.3" in rendered
    assert "session-123" in rendered
    assert "READY  session active" in rendered


def test_repl_state_commands_refresh_the_status_panel(monkeypatch) -> None:
    prompts = iter(["/resume session-123", "/agent reviewer", "/new", "/exit"])
    monkeypatch.setattr("loro.repl.typer.prompt", lambda *args, **kwargs: next(prompts))
    stream = StringIO()
    console = Console(file=stream, width=80, color_system=None)

    def unexpected_task(*args, **kwargs):
        raise AssertionError("State commands must not invoke the model")

    run_repl(LoroConfig(), unexpected_task, console=console)

    rendered = stream.getvalue()
    assert rendered.count("Loro | interactive workspace") == 4
    assert "SESSION ACTIVE" in rendered
    assert "session-123" in rendered
    assert "reviewer" in rendered
    assert rendered.endswith("Session closed.\n")


def test_repl_streams_chat_and_tool_activity_without_batch_report(monkeypatch) -> None:
    prompts = iter(["inspect the readme", "/exit"])
    monkeypatch.setattr("loro.repl.typer.prompt", lambda *args, **kwargs: next(prompts))
    stream = StringIO()
    console = Console(file=stream, width=90, color_system=None, force_terminal=False)

    def task_runner(prompt, session_id, agent_name, on_token, on_event):
        assert prompt == "inspect the readme"
        on_event("model.started", {"step": 1})
        on_event(
            "tool.started",
            {"step": 1, "tool": "file.read", "args": {"path": "README.md", "limit": 100}},
        )
        on_event(
            "tool.completed",
            {"step": 1, "tool": "file.read", "ok": True, "latency_ms": 12.4},
        )
        on_event("model.started", {"step": 2})
        on_token("The README ")
        on_token("is ready.")
        on_event("model.completed", {"step": 2, "latency_ms": 45.0})
        return SimpleNamespace(
            summary="Loro run mode completed. Model response: The README is ready.",
            response="The README is ready.",
            session_id="12345678-abcd-efgh-ijkl-123456789012",
            stop_reason="completed",
            steps=2,
            usage={"input_tokens": 30, "output_tokens": 10, "tool_calls": 1},
        )

    run_repl(LoroConfig(), task_runner, console=console)

    rendered = stream.getvalue()
    assert "assistant\nThe README is ready." in rendered
    assert "tool file.read path=README.md" in rendered
    assert "done file.read 12 ms" in rendered
    assert "2 steps · 1 tool · 40 tokens · session 12345678" in rendered
    assert "Loro run mode completed" not in rendered
    assert "Model response:" not in rendered
