# Security And Privacy Review Guide

Review Loro as an orchestrator with consequential local tools, external model routes, durable
memory, extension protocols, and remote ingress. Start with the threat model and data
classification, then map every enabled surface to an owner, approved data class, retention rule,
credential principal, audit event family, and denial test.

## Required Decisions

- Corporate identity assertion format, validation, revocation, tenant binding, and attribution.
- Approved model providers/routes, residency, retention/training terms, TLS/proxy controls,
  budgets, and explicit fallback policy.
- Workspace, subprocess, network, environment, MCP, Skill, graph, and gateway boundaries.
- Local/shared-memory classification, retention, correction, deletion, legal hold, backup, and
  provenance.
- Prompt/model/tool/artifact/session/audit DLP actions and sensitive-override authority.
- Audit collection, minimization, immutable retention, external anchoring, access, and alerts.
- Telemetry notice and approval. Loro's operational metrics are content-free, but deployment
  dashboards and correlated identity data still require privacy review.

## Release Review

Verify the support matrices match enabled deployment features. Confirm all critical/high
findings are resolved or formally accepted, secret candidates are adjudicated, dependencies and
licenses pass policy, the SBOM/provenance/checksums verify, and the restricted-beta charter names
users, repositories, data classes, duration, success measures, support, incident escalation, and
stop conditions. Repository tests do not substitute for penetration testing, production DLP,
corporate identity, or legal/privacy approval.
