# Audit Events And Delivery

Loro emits versioned JSON audit events to a local JSONL file or an HTTP collector. JSONL remains
the default for local development. Enterprise deployments can use authenticated HTTP delivery
with bounded local buffering and deterministic failure behavior.

## Event Schema 1.0

Every event contains these top-level fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Event contract version, currently `1.0`. |
| `event_id` | Unique event identifier. |
| `event_type` | Stable event family such as `approval.used`. |
| `timestamp` | UTC ISO-8601 event time. |
| `actor`, `tenant_id`, `session_id` | Resolved identity attribution when available. |
| `trace_id` | Caller trace id or the event id when no wider trace exists. |
| `action`, `target` | Consequential operation and normalized target when supplied. |
| `policy` | Decision, version, source, and reason when supplied. |
| `approval` | Approval/request identifiers, scope, method, and status when supplied. |
| `result` | Outcome fields such as `ok`, `returncode`, `stop_reason`, or `error`. |
| `redaction` | Whether fields were previewed/redacted and the applied method. |
| `details` | Event-specific metadata retained for compatibility and investigation. |

The legacy `created_at` field remains as an alias of `timestamp`, and event-specific values
remain under `details`, so 0.1.x JSONL consumers can migrate incrementally. Collectors should
route by `schema_version` and tolerate additional fields.

## Local JSONL

```toml
[audit]
enabled = true
schema_version = "1.0"
sink = "jsonl"
path = ".loro/audit.jsonl"
include_prompt_preview = true
```

Each event is appended as one JSON object per line under a process-safe lock. New events contain
an SHA-256 integrity envelope binding canonical event content to the preceding hash. When Loro
encounters an older unchained file, the first chained event also binds the exact legacy prefix.

Verify a file and optionally compare its final hash with an externally retained anchor:

```bash
loro audit verify
loro audit verify --anchor sha256:<expected-final-hash>
```

The local chain detects content mutation, insertion, deletion, and reordering within the
retained chain. Only an external anchor or immutable destination can prove that the file's tail
was not truncated.

## HTTP Collector

```toml
[audit]
enabled = true
schema_version = "1.0"
sink = "http"
http_url = "https://audit.example.internal/v1/loro/events"
http_token_env = "LORO_AUDIT_TOKEN"
failure_mode = "fail"
buffer_path = ".loro/audit-buffer.jsonl"
max_buffer_events = 1000
max_retries = 2
backoff_seconds = 0.25
timeout_seconds = 10
include_prompt_preview = false
```

Loro sends one schema event per HTTP `POST` with `Content-Type: application/json`. When
`http_token_env` is configured, it sends that environment value as a bearer token. Tokens are
never written to config or events.

Retries use exponential backoff. After retries are exhausted, Loro appends the complete event to
the bounded local buffer:

- `failure_mode = "warn"` emits a visible `RuntimeWarning`, returns the event with delivery
  status `buffered`, and allows the caller to continue.
- `failure_mode = "fail"` buffers the event and raises `AuditDeliveryError`. Runtime task-start
  failure therefore stops before contacting the model.
- A full buffer evicts the oldest events to make room for the new one. Eviction is counted and
  reported by `loro audit doctor` as `evicted_events`, and each eviction emits a
  `RuntimeWarning`; the newest evidence is never the event that gets dropped.
- An invalid buffer still raises, because its contents cannot be parsed safely.

`loro audit flush` drains the buffer under a single lock across load, delivery, and rewrite, so a
concurrent writer cannot lose an event in the gap. When the sink supports it, buffered events are
delivered in batches of `http_batch_size`. The default is `1`, which preserves the one-event HTTP
collector contract. Set a larger value only after confirming that the collector accepts the
`{"events": [...]}` batch envelope.

Delivery is at least once. A collector should deduplicate by `event_id`, because a timeout after
server acceptance can cause a buffered retry.

## Setup And Operations

Run the wizard or configure non-interactively:

```bash
loro setup audit
loro setup audit \
  --sink http \
  --http-url https://audit.example.internal/v1/loro/events \
  --http-token-env LORO_AUDIT_TOKEN \
  --failure-mode fail
```

Inspect local configuration and backlog state:

```bash
loro audit doctor
```

The doctor validates schema support, required URL/token configuration, and local buffer
readability. It intentionally does not send a network probe or test event.

Retry buffered delivery:

```bash
loro audit flush
```

Flush preserves order and stops at the first failed event. It rewrites the buffer with that
event and all later events, reports attempted/delivered/remaining counts, and exits nonzero while
events remain.

## Collector And Operations Requirements

- Terminate TLS with enterprise trust and authenticate the collector endpoint.
- Deduplicate by `event_id` and retain the original `schema_version` and `timestamp`.
- Return a non-error HTTP status only after accepting responsibility for the event.
- Restrict and monitor local buffer permissions; back it up only under approved data policy.
- Alert on nonzero backlog, oldest-event age, repeated warnings, full-buffer errors, and flush
  failures.
- Configure destination immutability, retention, access review, and clock synchronization outside
  Loro.

## Current Limitations

- HTTP delivery is one event per request; batching and collector-specific signing are not yet
  implemented.
- Loro provides local tamper evidence, not destination immutability or independent timestamping.
- Bearer-token authentication is the reference method; mTLS and custom signing are future sink
  extensions.
- Schema completeness depends on call sites supplying action, normalized target, policy, approval,
  and result metadata. Batch 5 establishes the envelope; later audit review will tighten
  event-family-specific requirements.
