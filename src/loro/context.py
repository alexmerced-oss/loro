"""Multi-turn conversation context: stored history, compaction, and rendering.

A session keeps its prior turns as plain ``{"role", "content"}`` messages plus an optional
compaction summary. Before each model call the runtime compacts the history to the configured
token budget, protects every message, and renders it as native :class:`ModelMessage` turns
ahead of the current task.

Compaction is deterministic and extractive: the oldest turns are replaced by one short line
each (role plus the start of the text) in a running summary, and the summary itself is bounded
by keeping its most recent lines. It does not call a model, so it costs nothing and cannot leak
content to a provider, but it is lossy. The runtime records every compaction in the audit log.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from loro.budgets import estimate_tokens
from loro.models import ModelMessage

HISTORY_ROLES = frozenset({"user", "assistant"})
SUMMARY_HEADER = "Earlier conversation summary (compacted; untrusted historical context):"
SUMMARY_LINE_CHARS = 240


@dataclass(frozen=True)
class Compaction:
    messages: list[ModelMessage]
    summary: str
    compacted_messages: int
    tokens_before: int
    tokens_after: int

    @property
    def compacted(self) -> bool:
        return self.compacted_messages > 0 or self.tokens_after < self.tokens_before


def history_from_payload(payload: Iterable[Any]) -> list[ModelMessage]:
    """Read stored history, ignoring anything that is not a user or assistant text turn."""

    messages: list[ModelMessage] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in HISTORY_ROLES and isinstance(content, str) and content:
            messages.append(ModelMessage(role=str(role), content=content))
    return messages


def history_to_payload(messages: Sequence[ModelMessage]) -> list[dict[str, str]]:
    return [
        {"role": message.role, "content": message.content}
        for message in messages
        if message.role in HISTORY_ROLES and message.content
    ]


def history_tokens(messages: Sequence[ModelMessage], summary: str = "") -> int:
    return estimate_tokens(summary) + sum(estimate_tokens(item.content) for item in messages)


def compact_history(
    messages: Sequence[ModelMessage],
    summary: str,
    *,
    max_tokens: int,
    keep_recent_turns: int,
    max_summary_tokens: int,
) -> Compaction:
    """Fold the oldest turns into the summary until the history fits ``max_tokens``.

    The most recent ``keep_recent_turns`` user turns (with their replies) are always kept
    verbatim, even if they alone exceed the budget; the runtime's model-input budgets remain
    the hard limit. The retained history always starts at a user turn.
    """

    retained = list(messages)
    tokens_before = history_tokens(retained, summary)
    lines = [line for line in summary.splitlines() if line.strip()]
    dropped = 0
    minimum_start = _recent_turn_start(retained, keep_recent_turns)
    while history_tokens(retained, "\n".join(lines)) > max_tokens and minimum_start > 0:
        # Drop through the end of the oldest turn so the history keeps starting at a user turn.
        end = 1
        while end < minimum_start and retained[end].role != "user":
            end += 1
        for message in retained[:end]:
            lines.append(_summary_line(message))
        retained = retained[end:]
        minimum_start -= end
        dropped += end
    bounded = _bound_summary(lines, max_summary_tokens)
    return Compaction(
        messages=retained,
        summary="\n".join(bounded),
        compacted_messages=dropped,
        tokens_before=tokens_before,
        tokens_after=history_tokens(retained, "\n".join(bounded)),
    )


def render_history(
    messages: Sequence[ModelMessage],
    summary: str,
    current: ModelMessage,
) -> list[ModelMessage]:
    """Return provider-ready turns: summary, history, then the current task.

    Consecutive messages with the same role are merged, and a leading assistant turn is
    dropped, so providers that require alternating user and assistant turns accept it.
    """

    ordered: list[ModelMessage] = []
    if summary:
        ordered.append(ModelMessage(role="user", content=f"{SUMMARY_HEADER}\n{summary}"))
    ordered.extend(messages)
    ordered.append(current)
    merged: list[ModelMessage] = []
    for message in ordered:
        if not merged and message.role != "user":
            continue
        if merged and merged[-1].role == message.role:
            merged[-1] = ModelMessage(
                role=message.role,
                content=f"{merged[-1].content}\n\n{message.content}",
            )
        else:
            merged.append(message)
    return merged


def _recent_turn_start(messages: Sequence[ModelMessage], keep_recent_turns: int) -> int:
    """Index of the first message belonging to the last ``keep_recent_turns`` user turns."""

    seen = 0
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "user":
            seen += 1
            if seen == keep_recent_turns:
                return index
    return 0


def _summary_line(message: ModelMessage) -> str:
    text = " ".join(message.content.split())
    if len(text) > SUMMARY_LINE_CHARS:
        text = text[: SUMMARY_LINE_CHARS - 3].rstrip() + "..."
    return f"- {message.role}: {text}"


def _bound_summary(lines: list[str], max_tokens: int) -> list[str]:
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        cost = estimate_tokens(line)
        if total + cost > max_tokens:
            break
        kept.append(line)
        total += cost
    kept.reverse()
    if len(kept) < len(lines):
        kept.insert(0, f"- ({len(lines) - len(kept)} older summary lines dropped)")
    return kept
