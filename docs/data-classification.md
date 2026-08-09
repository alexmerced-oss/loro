# Enterprise Data Classification

## Purpose And Status

This draft defines the minimum data-handling contract for Loro's first enterprise pilot. The
enterprise security and privacy owners must map these generic classes to corporate policy
before deployment. When labels conflict or are unknown, use the more restrictive class.

Loro 0.1.x records classification on shared-memory rows but does not yet enforce this complete
matrix at every data flow. The [enterprise evidence register](enterprise-evidence.md) tracks
that enforcement work.

## Classification Levels

| Level | Examples | Default handling |
| --- | --- | --- |
| Public | Published documentation, approved public source code, public product facts. | May use approved providers and normal project storage. |
| Internal | Non-public procedures, ordinary internal code, project notes, low-sensitivity metadata. | Approved enterprise endpoints and access-controlled storage only. This is the default when no label is supplied. |
| Confidential | Customer/business data, unreleased strategy, security design, private source code, governed table schemas. | Need-to-know access, approved provider/gateway, encryption, explicit retention, and auditable handling. |
| Restricted | Secrets, credentials, authentication material, regulated identifiers, highly sensitive personal/financial/health data, legal privilege, production data extracts. | Deny by default. Use only through a specifically approved workflow with managed DLP and data-owner authorization. Never place credentials or private keys in prompts or memory. |

`public-internal`, currently used by memory defaults, must be mapped to either Public or
Internal by enterprise policy before pilot use. Until that mapping exists, treat it as Internal.

## Data-Flow Rules

| Destination or flow | Public | Internal | Confidential | Restricted |
| --- | --- | --- | --- | --- |
| Model prompt/request | Allowed to approved provider. | Allowed to approved enterprise provider/gateway. | Only to a provider/gateway explicitly approved for the class, residency, and retention. | Denied by default; credentials always denied. |
| Model response/tool arguments | Allowed; still untrusted. | Allowed and access controlled. | Allowed only within the approved task and retention boundary. | Block or redact unless a dedicated workflow is approved. |
| Local memory | Allowed. | Allowed on a managed endpoint. | Only with explicit user intent, encryption/retention controls, and need-to-know endpoint access. | Denied by default; credentials prohibited. |
| Shared memory | Allowed with explicit dictation. | Allowed with explicit dictation, tenant/scope, author, source, and classification. | Requires explicit dictation, approved scope, reviewer/lifecycle policy, and backend encryption. | Denied by default; exception requires data-owner and security approval. |
| Session records | Allowed. | Allowed with managed permissions and retention. | Prompt/body capture minimized or disabled; approved encrypted storage required. | Content capture denied; store only redacted metadata when required. |
| Audit log | Metadata allowed. | Redacted previews allowed only if policy permits. | Metadata preferred; content previews disabled or irreversibly redacted. | Never log content or secrets; record classification and redaction action only. |
| Generated artifacts | Allowed. | Allowed in approved output roots with provenance. | Access-controlled output, classification marking, scanning, and review required. | Denied by default; dedicated protected workflow required. |
| Artifact provenance sidecar | Public metadata allowed. | Prompt preview permitted only by policy. | No raw prompt preview; use hashes/IDs and redaction metadata. | No content preview. |
| Governed-data metadata/summary | Allowed if source permits. | Must retain catalog/namespace/table provenance. | Authorization, purpose, classification, and approved query/summary path required. | Raw records denied by default; aggregate/summary only under a separately approved policy. |
| Shell/tool subprocess input and environment | Public task data only as needed. | Minimize and allowlist. | Explicit tool policy and isolated environment required. | Secrets passed only through a dedicated credential broker, never prompt-derived arguments. |
| MCP request/resource/prompt/result | Allowed from trusted servers; content remains untrusted. | Approved server and minimum necessary data only. | Requires managed endpoint, authentication, classification-aware policy, and isolated execution. | Denied by default until a dedicated credential/data workflow is approved. |

## Memory-Specific Rules

- Local memory may adapt to a user's workflow, but sensitive persistence remains subject to
  classification, DLP, retention, and user visibility.
- Shared memory is never autonomous. The user must explicitly dictate the content; proposals
  and accepted proposals do not bypass the shared draft and explicit commit boundary.
- Every shared record must have tenant, scope, classification, source, creator, timestamps,
  status, and stable memory ID. Confidential records additionally require an approved retention
  date or policy and review metadata.
- Retrieved memory is untrusted context, must include provenance/citation, and cannot grant
  permissions or approvals.
- Correction, supersession, expiration, deletion, legal hold, and quarantine requirements must
  be implemented before Confidential shared memory is piloted.

## Credentials And Authentication Material

Provider keys, database DSNs, Polaris and MCP credentials/tokens, cloud credentials, session tokens,
private keys, passwords, and recovery codes are Restricted regardless of surrounding data.
They must remain in approved environment/credential facilities, must not be written to TOML,
prompts, memory, artifacts, sessions, provenance previews, or audit details, and must not be
inherited by tool subprocesses unless the tool has a documented need.

The current scanner catches only a small set of obvious patterns. `--allow-sensitive` is a
development escape hatch, not an enterprise authorization mechanism, and managed policy should
prohibit it unless a specific workflow permits it.

## Provider Decision Requirements

Before enabling a provider or gateway, record its approved classifications, regions, retention
and training terms, subprocessors, transport controls, authentication method, rate/cost limits,
incident path, and whether prompts may contain customer or regulated data. Provider fallback
must never silently route data to a provider with a lower classification ceiling or different
residency policy.

## Minimization, Retention, And Deletion

- Send the smallest relevant prompt/context window and avoid entire repositories or table
  extracts when targeted retrieval is sufficient.
- Disable prompt previews for Confidential workflows until class-aware redaction is enforced.
- Define retention by data class for sessions, local memory, drafts, shared memory, artifacts,
  provenance, local audit buffers, and external audit destinations.
- Deletion must cover primary records, indexes/embeddings, caches, drafts, superseded records,
  artifacts, and documented backup expiration. Legal hold overrides deletion only through an
  accountable enterprise process.
- Audit the lifecycle action without reproducing deleted sensitive content in the event.

## Required Enforcement Evidence

The pilot must demonstrate classification-aware provider routing, negative tests for prohibited
flows, redaction tests, tenant isolation, retention/deletion behavior, and managed-policy
precedence. Until that evidence exists, use only Public or enterprise-approved Internal data in
a restricted evaluation environment.
