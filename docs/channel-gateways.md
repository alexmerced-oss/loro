# Channel Gateways

## Support Status

Loro 0.3.0 does **not** include Slack, Discord, or Telegram gateways. There is no bot listener,
webhook endpoint, socket-mode client, channel credential configuration, or mapping from a channel
account to a trusted Loro identity.

Loro currently exposes two different integration surfaces:

- Model-provider gateways, including configurable OpenAI-compatible endpoints, enterprise TLS,
  proxy, timeout, and retry settings.
- MCP client and read-only server transports over stdio and Streamable HTTP.

Neither surface turns Loro into a chat bot. Cross-session messaging is a local durable mailbox
between Loro sessions and does not connect to external messaging platforms.

## Required Gateway Architecture

A future channel gateway should be a separate, optional service around the Loro runtime. It must
not place bot tokens in project configuration or treat a platform username as enterprise identity.
At minimum, each adapter must provide:

- Environment- or secret-manager-backed credentials and webhook signature verification.
- An administrator-managed mapping from workspace/server, channel, and user identifiers to a
  tenant-scoped `IdentityContext`.
- Replay protection, deduplication, payload limits, rate limits, timeouts, and bounded queues.
- Explicit trust labeling for all inbound text, attachments, links, quoted messages, and commands.
- Independent Loro policy evaluation and identity-bound approval for consequential actions.
- Channel and direct-message allowlists, attachment scanning, output classification, and redaction.
- Durable delivery state, retry/dead-letter handling, audit correlation, and operator diagnostics.
- Platform-specific tests for forged signatures, replay, edited/deleted messages, thread routing,
  impersonation, bot mentions, and outage recovery.

Initial implementation should favor one adapter behind a common channel envelope rather than a
single process with three platform SDKs. Slack is the strongest first enterprise candidate because
its workspace administration and request-signing model are suitable for controlled pilots, but it
still requires organizational identity and retention decisions outside this repository.
