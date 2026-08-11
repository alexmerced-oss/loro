# Audit Event Inventory

Loro assigns every literal audit event to a governed family in
`src/loro/audit/inventory.py`. `scripts/check_audit_inventory.py` parses the Python source and
fails CI when a literal event has no registered family. Dynamic events are permitted only when
their producer is constrained to one of these registered prefixes.

All events use audit schema `1.0`, which promotes event id, timestamp, actor, tenant, session,
trace, action, target, policy, approval, result, and redaction fields. Identity and task context
are supplied by `AuditLogger`; policy and approval producers attach their exact decision data.
The detailed payload remains for family-specific metadata.

| Family | Consequential | Purpose |
| --- | --- | --- |
| `agraph.*` | Yes | Graph validation, policy, scheduling, gates, and execution. |
| `approval.*` | Yes | Approval request, grant, denial, expiry, and use. |
| `artifact.*` | Yes | Document, presentation, spreadsheet, and brief creation. |
| `config.*` | Yes | User-visible configuration mutations. |
| `data.*` / `polaris.*` | Yes | Governed data and Polaris operations. |
| `file.*` / `git.*` / `shell.*` | Yes | Local read, mutation, repository, and process tools. |
| `gateway.*` | Yes | Authenticated ingress, replay decisions, task, and delivery. |
| `mcp.*` / `skill.*` | Yes | Extension discovery, lifecycle, and execution. |
| `memory.*` | Yes | Local/shared retrieval, drafts, commits, and lifecycle. |
| `policy.*` / `safety.*` | Yes | Authorization and data-protection decisions. |
| `runtime.*` / `session.*` | Yes | Agent tasks, tools, budgets, and durable coordination. |
| `provider.*` | No | Explicit provider diagnostics; runtime provider use is under `runtime.*`. |

The inventory is a completeness control, not proof of a production immutable destination.
Collector deployment, retention, external anchors, access review, and outage exercises remain
in the [External Enterprise Requirements](external-enterprise-requirements.md).
