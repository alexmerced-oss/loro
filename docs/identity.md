# Identity Context

Loro resolves one typed identity context for runtime tasks, tool attribution, shared-memory
defaults, sessions, and audit events. Identity is loaded from a verified OpenID Connect token
(see [Verified Identity With OIDC](#verified-identity-with-oidc)), resolved configuration,
approved environment variables, or the local operating-system user fallback. Only the first is
verified: the context's `verified` field is `true` only for identities whose token signature and
claims Loro checked itself. Prompt text,
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

Because managed overlays load last, project/user configuration cannot remove that list. A
required field must be explicitly supplied by resolved configuration or the enabled identity
environment; local username, `default` tenant, inferred authentication/source labels, and random
session fallbacks do not satisfy the requirement. Agent runtime construction and audited
consequential commands fail when required fields are absent.
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

## Verified Identity With OIDC

Configure the issuer once; the CLI, the Web UI and gateways all use it:

```toml
[identity.oidc]
enabled = true
issuer = "https://login.example.com/realms/data"
client_id = "loro-web"          # Web UI sign-in client (public client with PKCE)
audience = "loro-api"           # expected `aud` for CLI and API bearer tokens
algorithms = ["RS256", "ES256"] # allowlist; "none" and HMAC algorithms are rejected
clock_skew_seconds = 60
tenant_claim = "tid"            # optional claim mappings
roles_claim = "roles"           # dotted paths such as "realm_access.roles" work
groups_claim = "groups"
required = true                 # fail closed when no valid token is present
```

Verification fetches the issuer's discovery document and JWKS over HTTPS (plain HTTP only for a
loopback issuer, which the tests use), caches the keys for `jwks_cache_seconds`, and refetches
once when a token names an unknown `kid` (key rotation, rate limited to one refetch per 30
seconds). Every token is checked for its signature, an allowed algorithm matching the key type,
`iss`, `aud`, `exp`, `nbf` and `iat` within the clock skew, and a subject claim. Browser sign-ins
also check `nonce` and, for multi-audience tokens, `azp`. RSA keys under 2,048 bits are refused.
Signature checks use the `cryptography` package (a default dependency on Linux; install
`loro-agent[oidc]` elsewhere).

- **CLI and runtime:** put an ID or access token for `audience` in `LORO_ID_TOKEN` (or the
  variable named by `token_env`). `loro identity show` then reports `auth_method = "oidc"`,
  `verified = true` and the mapped subject, tenant, roles and groups. With `required = true`, a
  missing or invalid token stops the run instead of falling back to environment assertions.
- **Web UI:** `loro web --auth oidc` signs users in with the authorization-code flow and PKCE
  (S256); register `http://HOST:PORT/auth/callback` as the redirect URI. API clients may instead
  send `Authorization: Bearer <token>`. Runs, approval decisions and audit events use the signed-in
  identity. See [Local Web UI](webui.md#sign-in-with-oidc).
- **Gateways:** set `oidc_audience` on an endpoint to require a bearer token from the chat bridge
  in addition to the platform signature. See [Channel Gateways](channel-gateways.md).

Loro does not perform device flow, token refresh, or directory group lookups; group and role
membership come only from token claims.

## Security Boundary And Current Limitations

Environment variables are assertions, not authentication by themselves. Enterprise deployments
must either inject them through a trusted managed launcher, workload environment, or gateway, or
use OIDC with `required = true` so only verified tokens are accepted. Identity is not
cryptographically bound to managed policy.

Identity supplies attribution and safe defaults; it is not by itself an authorization decision.
Approval records bind exact canonical arguments to subject, tenant, identity session, normalized
resource, policy version/decision, and expiration. Managed identity mode rejects caller-selected
tenant mismatches across shared-memory command, adapter, draft, and runtime-tool boundaries.
Cryptographic identity-to-policy binding remains an external deployment requirement.
