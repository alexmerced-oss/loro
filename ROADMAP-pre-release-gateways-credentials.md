# Pre-Release Gateway And Credential Roadmap

Status: implementation complete; release qualification in progress for Loro 0.3.0.

## Credential Vault

- [x] Strict `vault://namespace/profile/key` references.
- [x] macOS Keychain, Windows Credential Locker, and Linux Secret Service through Python Keyring.
- [x] Fail closed when no secure backend exists; no plaintext fallback.
- [x] Hidden prompt and environment import without command-line secret arguments.
- [x] Named accounts per provider and project-selectable `credential_ref`.
- [x] Environment values remain higher-priority automation overrides.
- [x] Mode-`0600` non-secret metadata index and value-free list/delete/doctor commands.
- [x] Provider clients, checks, configuration serialization, model tiers, docs, and tests.

## Channel Gateways

- [x] Common signed HTTP envelope and bounded asynchronous dispatcher.
- [x] Slack Events request signing, freshness checks, and bot replies.
- [x] Discord Ed25519 interactions, deferred acknowledgement, and safe follow-up editing.
- [x] Telegram secret-token webhooks and Bot API replies.
- [x] Teams outgoing HMAC plus Workflow/outbound webhook replies.
- [x] Signal and additional-chat support through a generic signed bridge contract.
- [x] Workspace/server, channel, and user allowlists with tenant-scoped identity mapping.
- [x] Durable hashed replay suppression, body/queue/worker/response/time bounds.
- [x] Untrusted-message labeling; remote text cannot approve consequential actions.
- [x] Setup wizard, diagnostics, architecture, threat model, release docs, and hermetic tests.

## External Release Evidence

- [ ] Deploy an authenticated TLS reverse proxy and retain ingress configuration evidence.
- [ ] Register test apps/bots in approved Slack, Discord, Telegram, and Teams tenants.
- [ ] Rotate test credentials through the target OS keyring and prove revocation.
- [ ] Run signed live webhook, rate-limit, retry, duplicate, outage, and reply-delivery tests.
- [ ] Approve platform retention, attachment, DLP, channel, tenant, and identity-mapping policy.
- [ ] Provide a trusted out-of-band approval service before remote consequential actions are enabled.

The unchecked items require platform tenants and enterprise infrastructure; they are deployment
evidence, not secret values or application code that should be committed to this repository.
