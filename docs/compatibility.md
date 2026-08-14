# Compatibility And Deprecation

## Pre-1.0 Contract

Loro follows semantic-versioning intent while the public surface is pre-1.0. The `0.11`
stabilization contract freezes supported CLI, schema, protocol, matrix, and deployment
surfaces. Minor releases may change experimental features, but supported surfaces receive a
migration path whenever practical. Patch releases do not intentionally break documented
supported behavior.

Configuration has an independent root schema version. Loro `0.5` supports schema `1.0` plus the
legacy unversioned shape emitted before `0.5.0`. Unknown future schemas fail closed. Stored audit,
approval, session, memory, gateway, Skill, MCP Task, and Agentic Graph records retain their own
documented schema or protocol versions.

The machine-readable [Support Matrix](support-matrix.json) is authoritative for whether a surface
is supported or experimental in a release line. Experimental does not mean ungoverned; policy,
approval, audit, data protection, and explicit-memory rules still apply.

## Deprecation Rules

- A deprecated CLI/configuration/API surface emits `LoroDeprecationWarning` when used through
  Python and a visible CLI warning when invoked directly.
- The warning names the replacement and planned removal version.
- Supported surfaces receive at least one minor release line of warning before removal unless a
  critical security issue requires immediate fail-closed behavior.
- Experimental surfaces may change without the full warning window, but the release notes and
  support matrix must identify the change.
- Loro never silently migrates to a weaker policy, provider residency, sandbox, identity, audit,
  or shared-memory write mode.
- Unknown configuration or persisted-record versions are rejected instead of guessed.

No surface is deprecated in `0.11.0`; the warning type and policy establish the contract for later
releases.

The generated [release contract](release-contract.json) is enforced in CI. After the `0.11`
stabilization freeze, changing a captured command, schema, protocol, support classification, matrix,
or reference deployment requires explicit regeneration and review.
