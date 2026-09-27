# Observability: OpenTelemetry And SIEM Forwarding

Status: experimental in 0.22.

## OpenTelemetry Traces And Metrics

Install the optional extra and enable telemetry:

```bash
python -m pip install "loro-agent[otel]"
```

```toml
[telemetry]
enabled = true
exporter = "otlp"                          # otlp, console, or none
otlp_endpoint = "http://collector:4318"    # OTLP/HTTP base URL; /v1/traces and /v1/metrics
otlp_headers_env = { "Authorization" = "OTLP_AUTH_HEADER" }  # header -> env var name
service_name = "loro"
resource_attributes = { "deployment.environment" = "prod" }
metric_interval_seconds = 60
```

Without the extra, or with `enabled = false`, every telemetry call is a no-op and nothing is
imported. Leaving `otlp_endpoint` unset lets the standard `OTEL_EXPORTER_OTLP_*` environment
variables decide. Loro creates its own tracer and meter providers rather than installing global
ones, so an application embedding Loro keeps its own OpenTelemetry setup.

**Spans**

| Span | Attributes |
| --- | --- |
| `loro.run` | `loro.mode`, `gen_ai.system`, `gen_ai.request.model`, `loro.agent`, `loro.run_id`, `loro.stop_reason`, `loro.steps`, `loro.tool_calls`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`; status `ERROR` on a provider failure |
| `loro.model.call` | `loro.step`, `gen_ai.system`, `gen_ai.request.model` (child of `loro.run`) |
| `loro.tool` | `loro.tool`, `loro.step`, `loro.tool.ok`; status `ERROR` when the tool reports an error |

**Metrics**

| Instrument | Type | Attributes |
| --- | --- | --- |
| `loro.runs` | counter | `loro.mode`, `gen_ai.system`, `loro.stop_reason` |
| `loro.model.tokens` | counter | `gen_ai.token.type` (`input`/`output`), `loro.mode`, `gen_ai.system` |
| `loro.model.duration` | histogram (ms) | `gen_ai.system` |
| `loro.tool.calls` | counter | `loro.tool`, `loro.tool.ok` |
| `loro.tool.duration` | histogram (ms) | `loro.tool` |
| `loro.approvals` | counter | `loro.approval.event` |

Telemetry never records prompts, model output, tool arguments, tool results or file paths. The
run id in `loro.run` matches the audit `trace_id`, so a trace can be joined to
`loro run export RUN_ID`.

## Forwarding Audit Events To A SIEM

Live forwarding copies every audit event to a syslog collector after it is written locally:

```toml
[audit.forward]
enabled = true
format = "ocsf"       # or "cef"
protocol = "tcp"      # tcp (RFC 6587 octet counting) or udp
host = "siem.example.internal"
port = 6514
facility = 13         # log audit
app_name = "loro"
```

Messages are RFC 5424 syslog lines whose message part is one OCSF JSON object or one CEF record.
Forwarding is best effort: when the collector is unreachable Loro warns and carries on, and the
local hash-chained log remains the record of truth. Plain TCP and UDP are unencrypted; send to a
local relay (for example rsyslog or a collector sidecar) that forwards over TLS. Loro does not
speak syslog over TLS itself.

Backfill or ad-hoc exports read the local log:

```bash
loro audit export --format ocsf -o loro-events.jsonl
loro audit export --format cef --since 2026-09-01T00:00:00Z
```

**OCSF mapping.** Each event becomes an OCSF 1.3.0 `API Activity` event (`class_uid` 6003,
`category_uid` 6, `activity_id` 99 "Other" with `activity_name` set to the Loro event type):
`time` from the event timestamp, `metadata.uid` = event id, `metadata.correlation_uid` = trace
(run) id, `actor.user` from the audit actor and identity (display name, groups),
`actor.session.uid`, `api.operation` = action, `resources[].name` = target, `status_id` from the
result (or `Failure` for denied/failed/blocked events), and the complete Loro payload under
`unmapped` (tenant, policy, approval, result, details, `event_hash`, `identity_verified`). The
mapping is Loro's own; validate it against your SIEM's OCSF parser before relying on specific
fields.

**CEF mapping.** `CEF:0|Alex Merced|Loro|<version>|<event type>|<event type>|<severity>|` with
severity 7 for failures and 3 otherwise, and extensions `rt`, `suser`, `act`, `outcome`,
`request`, `externalId`, `cs1` tenant, `cs2` trace id, `cs3` session id and `cs4` event hash.
Header pipes and backslashes, and extension equals signs, backslashes and newlines, are escaped.
