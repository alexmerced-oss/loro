"""`loro sessions`: saved sessions and cross-session messages."""

from __future__ import annotations

from typing import Annotated

import typer

from loro.cli._common import _audit, _authorize_cli_action, _enforce_safe_content, _runtime, console
from loro.config import (
    load_config,
)
from loro.resources import (
    session_message_resource,
)
from loro.session_messages import SessionMailbox, message_digest
from loro.sessions import SessionStore

sessions_app = typer.Typer(help="Inspect saved Loro sessions.")


@sessions_app.command("list")
def sessions_list() -> None:
    """List saved sessions."""
    store = SessionStore(load_config().sessions)
    records = store.list()
    if not records:
        console.print("No saved sessions yet.")
        return
    for record in records:
        console.print(
            f"- [bold]{record['session_id']}[/bold] "
            f"({record['mode']}, {record['created_at']}): {record['prompt']}"
        )


@sessions_app.command("show")
def sessions_show(session_id: Annotated[str, typer.Argument(help="Session ID.")]) -> None:
    """Show a saved session."""
    store = SessionStore(load_config().sessions)
    try:
        record = store.get(session_id)
    except FileNotFoundError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=record)


@sessions_app.command("send")
def sessions_send(
    sender_session_id: Annotated[str, typer.Argument(help="Sending session ID.")],
    recipient_session_id: Annotated[str, typer.Argument(help="Recipient session ID.")],
    content: Annotated[str, typer.Argument(help="Coordination message.")],
    yes: Annotated[
        bool, typer.Option("--yes", help="Use an allowed non-interactive approval.")
    ] = False,
    allow_sensitive: Annotated[
        bool, typer.Option("--allow-sensitive", help="Allow policy-approved sensitive content.")
    ] = False,
) -> None:
    """Queue an untrusted, non-authoritative message for another session."""
    _enforce_safe_content(content, "session message", allow_sensitive)
    resource = session_message_resource(
        operation="send",
        sender_session_id=sender_session_id,
        recipient_session_id=recipient_session_id,
        message_digest=message_digest(content),
    )
    _authorize_cli_action(
        tool="session_message",
        action="send",
        target=resource.target,
        arguments={
            "sender_session_id": sender_session_id,
            "recipient_session_id": recipient_session_id,
            "content_digest": message_digest(content),
        },
        risk_reason="Send untrusted coordination context to another Loro session.",
        non_interactive_approved=yes,
        resource=resource,
    )
    try:
        config = load_config()
        message = SessionMailbox(config.sessions, config.safety).send(
            sender_session_id=sender_session_id,
            recipient_session_id=recipient_session_id,
            content=content,
            allow_sensitive=allow_sensitive,
        )
    except (FileNotFoundError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write(
        "session.message_queued",
        message_id=message.message_id,
        sender_session_id=sender_session_id,
        recipient_session_id=recipient_session_id,
        content_digest=message_digest(content),
        carries_user_authority=False,
    )
    console.print_json(data=message.to_payload())


@sessions_app.command("inbox")
def sessions_inbox(
    session_id: Annotated[str, typer.Argument(help="Recipient session ID.")],
    include_acknowledged: Annotated[
        bool, typer.Option("--all", help="Include acknowledged messages.")
    ] = False,
) -> None:
    """Inspect queued and delivered cross-session messages."""
    messages = SessionMailbox(load_config().sessions).list(
        session_id, include_acknowledged=include_acknowledged
    )
    console.print_json(data=[message.to_payload() for message in messages])


@sessions_app.command("ack")
def sessions_ack(
    session_id: Annotated[str, typer.Argument(help="Recipient session ID.")],
    message_id: Annotated[str, typer.Argument(help="Delivered message ID.")],
) -> None:
    """Acknowledge a delivered cross-session message."""
    try:
        message = SessionMailbox(load_config().sessions).acknowledge(session_id, message_id)
    except (FileNotFoundError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=message.to_payload())


@sessions_app.command("wake")
def sessions_wake(
    session_id: Annotated[str, typer.Argument(help="Saved recipient session ID.")],
    prompt: Annotated[
        str,
        typer.Option(help="Trusted user instruction accompanying queued messages."),
    ] = "Process the queued coordination messages and continue safely.",
) -> None:
    """Explicitly resume a stopped session and deliver its queued messages."""
    try:
        result = _runtime().run(prompt, mode="run", session_id=session_id)
    except FileNotFoundError as error:
        raise typer.BadParameter(str(error)) from error
    _audit().write("session.woken", session_id=session_id, stop_reason=result.stop_reason)
    console.print(result.summary)
