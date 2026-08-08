# Identity Context

Loro resolves one typed identity context for runtime tasks, tool attribution, shared-memory
defaults, sessions, and audit events. Identity is loaded only from resolved configuration,
approved environment variables, or the local operating-system user fallback. Prompt text,
model output, repository content, memories, and tool results are never identity sources.

## Commands

```bash
loro identity show
loro identity doctor
loro setup identity
loro doctor
```

`identity show` prints the resolved non-secret context. `identity doctor` reports required and
missing fields and exits nonzero when requirements are not met. The general `loro doctor`
includes the same readiness check.

## Identity Fields

| Field | Meaning | Local fallback |
| --- | --- | --- |
| `subject` | Stable actor identifier used for attribution. | Operating-system username. |
| `display_name` | Human-readable name. | Resolved subject. |
| `organization` | Enterprise organization identifier. | None. |
| `tenant` | Default shared-memory tenant. | `default`. |
| `groups` | Group memberships asserted by the configured source. | Empty. |
| `roles` | Role memberships asserted by the configured source. | Empty. |
| `auth_method` | Authentication method label such as `oidc` or `workload_identity`. | `os_user`. |
| `session_id` | Identity session/correlation identifier. | Random ID for the CLI process. |
| `source` | Assertion source such as `managed-env`, `gateway`, or `local`. | `local`. |

## Configuration

```toml
[identity]
subject = "user-123"
display_name = "Alex Merced"
organization = "acme"
tenant = "platform"
groups = ["engineering", "data-platform"]
roles = ["developer", "memory-reader"]
auth_method = "oidc-device-flow"
source = "managed-launcher"
environment_enabled = true
environment_prefix = "LORO_IDENTITY_"
required_fields = ["subject", "organization", "tenant", "auth_method", "source"]
```

Resolved configuration values take precedence over environment values. This allows a managed
overlay to lock selected fields while leaving dynamic fields unset for a managed launcher to
supply. Environment values fill only fields that remain unset in resolved configuration.

## Environment Variables

With the default prefix, Loro recognizes:

```text
LORO_IDENTITY_SUBJECT
LORO_IDENTITY_DISPLAY_NAME
LORO_IDENTITY_ORGANIZATION
LORO_IDENTITY_TENANT
LORO_IDENTITY_GROUPS
LORO_IDENTITY_ROLES
LORO_IDENTITY_AUTH_METHOD
LORO_IDENTITY_SESSION_ID
LORO_IDENTITY_SOURCE
```

Groups and roles are comma-separated. Change `environment_prefix` when a managed launcher uses
a corporate naming convention, or set `environment_enabled = false` when all fields must come
from managed configuration.

Example managed-launcher values:

```bash
export LORO_IDENTITY_SUBJECT="user-123"
export LORO_IDENTITY_ORGANIZATION="acme"
export LORO_IDENTITY_TENANT="platform"
export LORO_IDENTITY_GROUPS="engineering,data-platform"
export LORO_IDENTITY_ROLES="developer,memory-reader"
export LORO_IDENTITY_AUTH_METHOD="oidc-device-flow"
export LORO_IDENTITY_SOURCE="managed-launcher"
loro identity doctor
```

## Managed Fail-Closed Policy

Enterprise administrators can require fields in the non-overridable managed overlay:

```toml
# /etc/loro/managed.toml
[identity]
required_fields = ["subject", "organization", "tenant", "auth_method", "source"]
```

Because managed overlays load last, project/user configuration cannot remove that list. Agent
runtime construction and audited consequential commands fail when required fields are absent.
Diagnostic commands remain available so operators can see what is missing.

## Propagation

- Every audit event created through Loro's CLI/runtime logger includes `actor`, `tenant_id`, and
  the full non-secret identity context.
- Runtime session JSON stores the identity context alongside prompt, tools, memory citations,
  and stop reason.
- Runtime shared-memory recall defaults to the identity tenant.
- Shared-memory CLI search, proposal acceptance, and draft staging default tenant and author to
  the active identity when flags are omitted.
- Runtime tool events carry subject, tenant, and identity session correlation.

## Security Boundary And Current Limitations

Environment variables are assertions, not authentication by themselves. Enterprise deployments
must inject them through a trusted managed launcher, workload environment, or gateway and stop
untrusted users from replacing that launch context. Loro does not currently validate OIDC/JWT
signatures, perform device flow, fetch directory groups, or cryptographically bind identity to
managed policy.

Identity supplies attribution and safe defaults; it is not by itself an authorization decision.
Approval records now bind exact canonical arguments to subject, tenant, identity session, policy
decision, and expiration. An explicit CLI tenant argument can still select another tenant, and
policy-version/resource normalization remains Batch 4 work.
