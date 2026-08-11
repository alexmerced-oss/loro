# Reference Audit Collector

Loro includes a small reference collector for testing and restricted single-node deployments.
It accepts schema `1.0` events at `POST /events`, including the batch envelope used by
`loro audit flush`.

## Start Locally

```bash
export LORO_AUDIT_COLLECTOR_TOKEN="$(openssl rand -hex 32)"
loro audit collect --path .loro/audit-collector.sqlite3
```

Point clients at `http://127.0.0.1:8788/events` and place the same token in the environment
named by `audit.http_token_env`. The collector also exposes `GET /health` and content-free
Prometheus metrics at `GET /metrics`.

Container deployment is in `deploy/audit-collector/compose.yaml`:

```bash
export LORO_AUDIT_COLLECTOR_TOKEN="$(openssl rand -hex 32)"
docker compose -f deploy/audit-collector/compose.yaml up --build
```

The Compose service runs without Linux capabilities, with a read-only root filesystem and a
dedicated persistent volume. It binds only to loopback. Put an approved TLS/mTLS reverse proxy
in front of it before remote or production use.

## Delivery Contract

- Bearer authentication is checked with a constant-time comparison.
- Invalid schema, event IDs, event types, timestamps, oversized bodies, and conflicting replay
  content are rejected before acknowledgment.
- A SQLite `BEGIN IMMEDIATE` transaction accepts the entire batch or none of it.
- `event_id` is unique. Exact retries are acknowledged as duplicates; different content with the
  same ID is rejected.
- Every committed row binds canonical event JSON to the preceding SHA-256 hash.
- HTTP success is returned only after the transaction commits.

Verify durable state:

```bash
loro audit collector-verify --path .loro/audit-collector.sqlite3
```

Run the outage/recovery proof:

```bash
PYTHONPATH=src python scripts/audit_outage_drill.py --events 1000
```

## Metrics Privacy

Enable Loro-side operational metrics independently of collector metrics:

```toml
[audit]
metrics_enabled = true
metrics_path = ".loro/operational-metrics.json"
```

`loro audit metrics` exports bounded counters for event families, delivery status, approvals,
memory operations, gateway operations, task duration, token usage, cost, and observed queue
depth. It does not persist prompts, responses, tool arguments/results, memory text, artifact
content, chat messages, identities, tenants, provider request bodies, or model output.

## Production Boundary

The reference collector is not a claim of enterprise immutability or high availability.
Production operators must provide TLS, credential rotation, replicated durable storage,
retention locks, external hash anchoring, backup, access review, alert routing, and capacity
testing. These are tracked as external evidence in the enterprise register.
