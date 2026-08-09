# Cross-Session Messaging

Loro sessions can send durable coordination messages to another saved session. Messages queue
while the recipient is stopped, are inserted automatically when it resumes, and are
acknowledged after the resumed run persists. The mailbox does not copy conversation history or
filesystem state.

## Claude Code Research

Claude Code's current `SendMessage` tool sends messages to an Agent Teams teammate or resumes a
stopped subagent by agent ID. Agent Teams provide automatic delivery between independent context
windows and a local mailbox. Anthropic subsequently hardened cross-session delivery so relayed
messages do not carry user authority and cannot relay permission approval. See Anthropic's
[tools reference](https://code.claude.com/docs/en/tools-reference),
[Agent Teams documentation](https://code.claude.com/docs/en/agent-teams), and
[Claude Code changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md).

Loro adopts the durable mailbox and explicit addressing ideas, but not implicit authority or a
team/task scheduler. A Loro message is coordination context only.

## Commands

```bash
loro sessions send SENDER_SESSION RECIPIENT_SESSION \
  "The parser tests pass; review commit abc123."
loro sessions inbox RECIPIENT_SESSION
loro sessions wake RECIPIENT_SESSION
loro run --resume-session RECIPIENT_SESSION "Continue with the new context."
loro sessions inbox RECIPIENT_SESSION --all
```

`session.send` and `session.inbox` are also runtime tools. Model-originated sends enter the
ordinary `session_message` permission and approval path. A model-provided `approved=true` value
does not authorize delivery. `sessions wake` is intentionally a user-facing command: a sending
model may queue context but cannot silently start another model session or spend its budget.

## Delivery Contract

- Both CLI sender and recipient must identify existing saved sessions.
- Messages are stored atomically as individual JSON records under
  `[sessions].message_path`.
- Content is bounded by `[sessions].max_message_bytes` and passes the safety scanner.
- Every record permanently contains `carries_user_authority = false` and the trust label
  `untrusted-cross-session`.
- Resume injects outstanding messages under a distinct untrusted-context heading. Message text
  is never parsed as a user-authored `@tool` directive.
- Delivery and consumption are audited without copying raw content into the audit event.
- A recipient's own policy and approval controls apply to every action it considers after
  reading a message.

The local mailbox is restart-safe but not a distributed message broker. Multi-host routing,
operator retention policy, locking for high-concurrency writers, notifications, and a shared
team task list remain separate enterprise-operational concerns.
