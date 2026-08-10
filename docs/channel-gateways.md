# Channel Gateways

Loro accepts remote agent tasks through authenticated Slack, Discord, Telegram, Microsoft Teams,
Signal-bridge, and generic signed-webhook endpoints. Gateway messages are untrusted input: they map
to a managed Loro identity but never carry approval authority.

## Install And Configure

Discord signature verification uses the optional gateway dependencies:

```bash
python -m pip install "loro-agent[gateway]"
loro credentials doctor
```

Store platform secrets in the operating-system vault. Values are entered through a hidden prompt;
only references appear in TOML:

```bash
loro credentials set vault://gateway/work-slack/signing-secret
loro credentials set vault://gateway/work-slack/bot-token
loro gateway setup --id work-slack --platform slack \
  --route /gateway/work-slack --user-id U123 --subject alex --tenant acme \
  --channel C123 --workspace T123 \
  --credential signing-secret=vault://gateway/work-slack/signing-secret \
  --credential bot-token=vault://gateway/work-slack/bot-token
loro gateway doctor
loro gateway serve
```

`loro setup gateway` runs the same wizard. The default listener is `127.0.0.1:8765`; expose it
through an authenticated, rate-limited TLS reverse proxy. Direct public binding is diagnosed as
unsafe and is not a substitute for TLS termination or network controls.

## Supported Adapters

| Platform | Inbound authentication | Reply path |
| --- | --- | --- |
| Slack | HMAC-SHA256 signing secret, timestamp freshness, event deduplication | `chat.postMessage` with a vaulted bot token |
| Discord | Ed25519 interaction signature | Deferred interaction followed by editing the original response |
| Telegram | `X-Telegram-Bot-Api-Secret-Token` | Bot API `sendMessage` with a vaulted bot token |
| Microsoft Teams | Outgoing-webhook HMAC | Immediate accepted message, then a vaulted Teams Workflow or approved outbound webhook URL |
| Signal | Signed Loro bridge envelope | Vaulted bridge callback URL |
| Generic | Signed Loro bridge envelope | Vaulted callback URL |

Signal does not provide a native bot API used directly by Loro. Run an approved Signal bridge such
as a managed `signal-cli` service and have it emit the generic signed envelope. The generic adapter
also keeps additional chat systems inexpensive to integrate without placing platform SDKs inside
the agent runtime.

The implementations follow the current official contracts for
[Slack request signing](https://api.slack.com/docs/verifying-requests-from-slack),
[Discord interactions](https://docs.discord.com/developers/interactions/receiving-and-responding),
[Telegram webhook secrets](https://core.telegram.org/bots/api#setwebhook), and
[Teams outgoing webhooks](https://learn.microsoft.com/en-us/microsoftteams/platform/sbs-outgoing-webhooks).

## Security Model

- Every endpoint has a unique route and platform credential set.
- Platform workspace/server, channel, and user identifiers are checked before scheduling work.
- Each allowed platform user maps to a tenant-scoped `IdentityContext`; unknown users fail closed.
- Slack timestamps are freshness-checked, and all message IDs are hashed into a bounded durable
  replay ledger. Duplicate deliveries are acknowledged without rerunning work; invalid replay
  state fails gateway startup closed.
- Request bodies, workers, pending tasks, response size, and HTTP client timeouts are bounded.
- Listener body reads have a bounded timeout; deploy the loopback service behind ingress connection
  and rate limits for complete slow-client protection.
- Remote content is labeled untrusted before it reaches `AgentRuntime`.
- Existing permissions, budgets, sandboxing, data protection, memory rules, and audit still apply.
- `ask`-gated actions cannot be approved by message text. Consequential remote work requires a
  separate trusted approval path.
- Bot tokens, signing secrets, public keys, and secret callback URLs stay in the credential vault.
- Outbound callbacks must use HTTPS, except loopback HTTP for local bridge testing, and embedded URL
  credentials are rejected.

Example configuration produced by the wizard:

```toml
[gateway]
enabled = true
host = "127.0.0.1"
port = 8765
state_path = ".loro/gateway-state.json"
max_pending_tasks = 32
max_workers = 4

[gateway.endpoints.work-slack]
platform = "slack"
route = "/gateway/work-slack"
allowed_workspaces = ["T123"]
allowed_channels = ["C123"]

[gateway.endpoints.work-slack.credentials]
signing-secret = "vault://gateway/work-slack/signing-secret" # pragma: allowlist secret
bot-token = "vault://gateway/work-slack/bot-token"

[gateway.endpoints.work-slack.identities.U123]
subject = "alex"
tenant = "acme"
roles = ["developer"]
```
