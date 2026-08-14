# Approvals

Loro uses trusted approval records for permission decisions that resolve to `ask`. Approval is
separate from model output: a model may request an action, but only the terminal user or an
explicitly enabled non-interactive user path can grant it.

## Interactive Flow

When an ask-gated action is requested, Loro displays:

- Action and resolved target.
- Canonical argument preview or command/diff inputs.
- Policy decision and reason.
- Risk reason.
- Active identity, tenant, and session.

The user chooses `once`, `session`, or `deny`. A session approval can be reused only for the
same identity session, action, target, canonical arguments, policy decision, policy source, and
policy version. Changed content,
paths, command arguments, Git messages, tenants, or other arguments create a different request
and require another approval.

```bash
# Prompts when shell policy is ask.
loro shell run -- python -c "print('hello')"

# Prompts before an executed shared-memory commit.
loro memory commit-draft <draft-id> --execute

# Governed metadata defaults to allow, but a managed ask policy prompts before Polaris runs.
loro data catalogs
```

Runtime file writes/replacements, shell commands, Git add/commit, and governed Polaris metadata
calls use the same prompt. Shared-memory draft staging remains explicit but does not write the
shared backend; the prompt occurs at `commit-draft --execute`.

## Approval Binding And Replay Protection

Each `ApprovalRequest` binds:

- Action and target.
- SHA-256 digest of canonical JSON arguments.
- Identity subject, tenant, and session.
- Policy decision and reason.
- Risk reason and request timestamp.

An `ApprovalRecord` adds scope, grant method, grant/expiry timestamps, status, use count, and a
random approval ID. One-time records become used after one action. Session records remain active
until expiry but only match the exact request fingerprint. Unknown, expired, consumed, changed-
argument, cross-actor, and cross-session uses fail.

## Model Boundary

Tool calls parsed from model text or native provider tool calls are marked with `origin=model`.
An `approved=true` argument from that origin is ignored as authority and fails unless the trusted
interactive provider independently asks the terminal user and receives approval.

Explicit user-authored `@tool` directives may still use `approved=true` when non-interactive
approvals are enabled. That path exists for local automation and tests; it is not an enterprise
approval ceremony.

## Non-Interactive Mode

Direct commands retain `--yes` for CI and controlled automation:

```bash
loro shell run --yes -- python -c "print('hello')"
loro memory commit-draft <draft-id> --execute --yes
loro data --yes catalogs
```

These still create and consume a one-time approval record with method `non_interactive`. Managed
enterprise configuration should disable them unless an approved automation identity and policy
provides equivalent authorization.

## Configuration

```toml
[approvals]
interactive = true
allow_non_interactive = true
allow_session_scope = true
once_ttl_seconds = 300
session_ttl_seconds = 900
store = "json"
store_path = "~/.local/state/loro/approvals.json"
max_store_bytes = 10000000
```

Use the wizard for local configuration:

```bash
loro setup approvals
```

Recommended managed pilot baseline:

```toml
# /etc/loro/managed.toml
[approvals]
interactive = true
allow_non_interactive = false
allow_session_scope = true
once_ttl_seconds = 120
session_ttl_seconds = 600
store = "json"
store_path = "/var/lib/loro/approvals.json"
```

Managed overlays load last, so project and user configuration cannot re-enable a disabled
non-interactive or session path.

## Audit Events

Loro emits `approval.requested`, `approval.granted`, `approval.denied`, `approval.expired`, and
`approval.used`. Events include identity attribution, action, target, argument digest, policy
decision/reason/version/source, record scope/method/status, and timestamps. Raw argument content is not written
to approval audit events.

## Current Limitations

- The default `memory` store is process-local. The optional `json` store persists metadata with
  owner-only permissions, process and file locking, atomic replacement, schema validation, size
  limits, and compare-and-set consumption so separate processes cannot consume one one-time
  approval twice. Raw approval arguments are never stored.
- The JSON implementation is a single-host store, not a distributed approval service. Session
  grants still require the exact identity session id, so a new CLI session cannot inherit them.
- Targets use typed normalized resources. Configured policy versions are fingerprint-bound, but
  policy artifacts are not yet signed or integrity-verified.
- Corporate identity assertion verification remains separate work; approval strength depends on
  the trustworthiness of the active identity context.
