# Multi-User Server Mode

Status: experimental in 0.22. It combines three pieces that each work on their own:

1. **Verified identity**: `loro web --auth oidc` signs users in with your identity provider
   (see [Verified Identity With OIDC](identity.md#verified-identity-with-oidc)).
2. **Role-based access control** on every Web UI API route, from verified claims.
3. **A shared approval authority in Postgres**, so approvals raised by the Web UI, the CLI
   (`--approval-stdio`) and other hosts land in one queue.

## Roles

| Role | Can |
| --- | --- |
| `viewer` | View conversations, runs, graphs, memory and governance evidence |
| `operator` | Everything a viewer can, plus start conversations and runs, upload files, run graphs, use WebMCP |
| `approver` | Everything a viewer can, plus approve or deny protected actions and graph gates |
| `admin` | Everything, including settings, profiles, extensions, schedules, memory curation and access rules |

Operators cannot decide approvals unless they also hold `approver` (separation of duties). Every
API route is classified; a route nobody classified requires `admin`, so new endpoints are closed
by default. Refused requests return `403` with the missing permission and are audited as
`policy.access_denied`.

Roles come from the verified token's role and group claims (`identity.oidc.roles_claim` and
`groups_claim`):

```toml
[webui.rbac]
enabled = true
admins = ["alex@example.com"]      # bootstrap the first administrator by subject
default_role = "viewer"            # unset (default) means users without a rule get no access
accept_role_names = true           # claims literally named viewer/operator/approver/admin count

[webui.rbac.mappings]
"data-platform-admins" = "admin"
"data-engineers" = "operator"
"sre-oncall" = "approver"
```

The **Access** view (shown in OIDC mode) lists your roles and permissions and the role matrix.
Admins can edit the mappings, bootstrap admins and default role there; changes are written to
`.loro/config.local.toml`, audited as `config.access_rules_changed`, and a change that would
remove the editor's own admin access is refused. The UI hides actions your role cannot take: the
composer for viewers, approval buttons for non-approvers, and saving settings for non-admins.

Launch-token mode (`loro web` without `--auth oidc`) is a single local user with every permission.

## Shared Approval Authority In Postgres

```toml
[approvals]
authority = "postgres"
authority_dsn_env = "LORO_APPROVALS_DSN"   # environment variable holding the DSN
```

Loro creates one table, `loro_aais_state`, and keeps each authority stream's state in one row.
Every operation locks that row (`SELECT ... FOR UPDATE`) for its whole transaction, so sequence
numbers, pending requests, decisions and receipts never interleave across processes or hosts.
Semantics are those of the AAIS file store: exact action digests, offered choices only, expiry,
idempotent replay, retention and gap-aware replay. Owners on another host are reported as
unverified and never treated as stopped. There is no corruption quarantine in this backend; the
database guarantees a well-formed row. Install `loro-agent[data]` for `psycopg`.

## Limits In 0.22

- Web sign-in sessions live in the memory of one `loro web` process. Run one process, or use
  sticky sessions; API clients with bearer tokens work against any process.
- Conversations are stored per workspace in SQLite and are shared by everyone with access to that
  workspace. They are not partitioned per user; the audit log attributes every run and decision
  to the signed-in subject.
- The Postgres backend reuses internals of `agent-approval-interchange` 0.2 (pinned `<0.3`); its
  integration tests (`tests/integration/test_aais_postgres_integration.py`, run in the Integration
  workflow against Postgres 16) guard that contract.
- No independent security review of this mode exists yet. See
  [Promotion Gates](project-status.md#promotion-gates-022).
