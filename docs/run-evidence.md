# Run Evidence Bundles

`loro run export` packages the evidence for one agent run into a zip file that another person
can check offline with `loro run verify`. It is meant for reviews, incident follow-up, and
audits that need "what did the agent do, under which configuration, with whose approval" for a
single run, without handing over the whole audit log.

Status: experimental in 0.22. The bundle format is versioned (`loro.run-evidence.v1`) and a v1
verifier rejects any other format.

## Commands

```bash
loro run list                                   # recent runs, newest first
loro run list --limit 5 --json
loro run export RUN_ID --out run.zip            # prints the bundle digest
loro run export RUN_ID --out run.zip --force    # overwrite an existing file
loro run verify run.zip
loro run verify run.zip --expect-digest sha256:...    # anchor to the digest printed at export
loro run verify run.zip --audit-log ~/.local/state/loro/audit.jsonl
```

A **run** is one call of the agent runtime: one `loro run`, `loro plan`, REPL turn, or Web UI
reply. Its **run id** is the audit `trace_id` that every audit event written during that run
carries. `loro run` prints the id after each task, and `loro run list` reads recent ids from the
local audit log. Subagent runs have their own ids.

`loro run list`, `loro run export` and `loro run verify` are subcommands of `loro run`. A task
whose whole prompt is one of those words needs `--`: `loro run -- export`.

Exit codes: `0` success; `1` verification found a problem; `2` a usage error such as an unknown
run id, a missing audit log, or an existing output file without `--force`. Every command takes
`--json` for machine-readable output.

## Requirements

- Audit must be enabled with the local JSONL sink (`[audit] sink = "jsonl"`, the default). With
  the HTTP sink, export from your collector instead.
- The run's events must be hash-chained (every event written by Loro 0.10 or later is).
- Full tool arguments and results come from the run's session record. A session record keeps
  only its latest run, so after a session is resumed, exporting an earlier run of that session
  includes the audited tool metadata (tool, status, step, latency, sandbox status) but not the
  arguments and results, and the export says so in a warning.

## Bundle contents

The zip holds exactly these members, in this order, stored without compression:

| Member | Contents |
| --- | --- |
| `manifest.json` | Format, run id, export time, Loro version, the SHA-256 and size of every other member, and export warnings. |
| `manifest.sha256` | The SHA-256 of `manifest.json`: the **bundle digest**. |
| `run.json` | Run id, session id, mode, start and completion times, provider and model, stop reason, steps, token and cost usage, latency, actor and tenant. |
| `audit-slice.jsonl` | Every audit event carrying the run id, byte-for-byte as the audit log holds them, including each event's `integrity` block. |
| `audit-chain.json` | The contiguous audit-log segment the run spans: for each event in it, the log line, `event_hash`, `previous_hash`, and whether it belongs to the run. Other runs' events contribute hashes only, never content. Also records the audit log path and a verification of the whole log at export time. |
| `approvals.json` | Approval request ids from the run's `approval.*` audit events and, for each, the AAIS request, decision and resolution envelopes still held by the project's AAIS store. Approvals answered at a terminal prompt appear in the audit slice only. |
| `tool-calls.json` | The audited `runtime.tool_executed` metadata and, when the session record still belongs to this run, each tool call's arguments, origin, status, output and metadata. |
| `digests.json` | The resolved configuration digest recorded when the run started, the digest at export time, whether they differ, and the agent profile name, revision, OAP spec digest and trust when a profile was used. |
| `sandbox.json` | Per tool call, the sandbox profile and whether it was OS-enforced, as audited during the run; plus the sandbox diagnosis of the exporting machine (labelled as export-time, not run-time). |

The configuration digest is SHA-256 over the canonical JSON of the resolved configuration. The
configuration names credential environment variables and vault references but never contains
their values.

## Redaction

Audit events were already protected by the `audit` data-protection surface when they were
written. Tool arguments and results pass through the same surface again on export: text above
the surface's maximum classification (`internal` by default) is redacted, and text the policy
blocks is replaced with `[withheld by data-classification policy]`. `tool-calls.json` records each
redaction decision (classification, maximum, action, finding kinds), never the redacted text.
Change what the bundle may contain with `[safety.surfaces.audit]`; see
[Managed Data Protection](data-protection.md).

## What verification checks

`loro run verify` reports every problem it finds, not only the first:

1. The archive opens, contains exactly the v1 members, and its bytes equal the canonical v1
   layout rebuilt from those members, so header and ordering changes are caught.
2. `manifest.sha256` matches `manifest.json`, and every member matches its manifest digest.
3. Each audit-slice event's hash, recomputed from its content and recorded previous hash, matches
   its `event_hash`, and every event carries the manifest's run id.
4. The chain segment links: each entry's `previous_hash` equals the prior entry's `event_hash`,
   and the run's entries match the slice events in order.
5. `run.json` names the manifest's run, and its usage equals the audited completion event.
6. With `--expect-digest`, the bundle digest equals the value you recorded at export.
7. With `--audit-log`, that log verifies as a whole and contains the bundle's chain segment at
   the same lines with the same hashes.

A change to any single byte of the bundle fails verification.

## Limits

- The bundle is not signed. Checks 1 to 5 prove the bundle is internally consistent. Someone
  able to rewrite the entire bundle consistently would pass them, so record the printed digest
  somewhere independent (a ticket, a signed commit, a message) and verify with
  `--expect-digest`, or check against the original audit log with `--audit-log`.
- The audit log's own guarantees apply: it is tamper-evident, not tamper-proof. See
  [Audit](audit.md) for anchoring the log's final hash.
- AAIS receipts are read from the project's store (`.loro/aais-pending.json`), which keeps a
  bounded history. Very old approvals may be listed by id without receipts.
