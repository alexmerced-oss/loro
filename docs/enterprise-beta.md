# Enterprise Beta Guide

Loro `0.9` carries the restricted enterprise beta deployment into its release-candidate line. The
supported reference shape is a managed Linux endpoint, Python 3.11-3.14, an explicitly approved
model route, Postgres shared memory, and the authenticated audit collector. Iceberg memory,
Polaris, MCP, graphs, and remote gateways retain the stability labels in the machine-readable
support matrix.

## Administrator Checklist

1. Assign the product, runtime, identity/policy, memory/data, security/privacy, release, and
   operations owners listed in the reference deployment.
2. Mirror and verify the exact wheel, checksum, SBOM, provenance, and support matrices.
3. Review `deploy/reference/manifest.json`, then provision the organization-owned controls in
   `external-enterprise-requirements.md`.
4. Copy `deploy/reference/managed.toml` into controlled configuration management, tailor paths
   and approved tools, and distribute it with `LORO_MANAGED_CONFIG_REQUIRED=true` and a pinned
   aggregate digest.
5. Bind subject and tenant to a verified corporate assertion. Never accept identity from a
   prompt, repository, or remote-message body.
6. Provision TLS Postgres with least privilege and forced RLS, immutable audit delivery, approved
   model routing, endpoint isolation, retention, monitoring, and support ownership.
7. Run the acceptance commands and attach content-free results to the evidence register.

## Clean Installation And Acceptance

```bash
python -m venv /opt/loro/venv
/opt/loro/venv/bin/python -m pip install --require-hashes -r approved-loro-requirements.txt
export LORO_MANAGED_CONFIG_REQUIRED=true
export LORO_MANAGED_CONFIG=/etc/loro/managed.toml
export LORO_MANAGED_CONFIG_SHA256=sha256:<approved-aggregate-digest>
loro --version
loro config check --strict
loro doctor
loro memory migration-status
loro operations benchmark --strict --output loro-benchmark.json
```

The adopting organization creates `approved-loro-requirements.txt` from the verified release
artifact and transitive dependency lock used by its package mirror. The repository does not
publish an organization-specific lock file or trust root.

## Upgrade And Rollback

Before upgrading, stop new gateway work, drain or record audit backlog, record the active config
digest, back up Postgres shared memory, and verify the backup. Install `0.9` in a new virtual
environment, run configuration checks and migrations, then exercise a synthetic read, explicit
shared-memory write, audit delivery, and rollback test before admitting users.

Rollback application code by restoring the previous environment and managed configuration.
Use `loro memory migrate --target VERSION --apply` only when the migration status and release
notes explicitly permit it. Restore a verified database backup only after stopping writers and
recording the incident/change authorization. Never silently discard approvals, audit backlog,
sessions, graph records, or committed memory to make a rollback pass.

## Uninstall And Offboarding

Disable ingress and provider routes, revoke vault entries and workload credentials, drain and
verify audit delivery, export evidence required by retention policy, retire tenant memory through
the governed lifecycle, and remove user configuration/state according to legal hold and records
policy. Uninstalling the wheel does not delete Postgres, audit collector, object-store, or user
state. Operators must verify each store separately.

## Supported And Unsupported Use

Approved beta use is bounded coding and productivity work with Public or explicitly approved
Internal synthetic/non-production data. Autonomous shared-memory commits, unrestricted tools,
prompt-selected identity/tenant, Restricted data, silent provider fallback, governed-data
mutation, and production Iceberg memory are unsupported. See the support matrix for the exact
technical boundary and the reference deployment for the full limitations.
