# Loro Audit & Roadmap — August 10, 2026

## Overview

Loro (`loro-agent` 0.3.0, alpha) is a Python 3.11+ Typer CLI agent harness for
enterprise coding, governed data work, and productivity artifacts. It is a large,
well-structured codebase (~20k lines under `src/loro`) with a genuine security posture:
layered configuration with managed enterprise overlays, identity-bound approval records
with replay protection, an `allow/ask/deny` permission engine over normalized resources,
a managed data-protection/DLP layer, subprocess sandbox profiles (process + Bubblewrap),
a versioned tamper-evident audit chain, dual-era MCP client/server support, an Agentic
Graph (AGS) execution engine, and signed channel gateways.

State of health:

- **Tests:** `398 passed, 3 skipped` in ~21s. `ruff check` is clean. Good breadth
  (37 test files, ~383 test functions), but coverage skews to happy paths; several bugs
  below survive precisely because the assertions are shallow (e.g. the spreadsheet test
  only checks the sheet name, the parallel-graph test uses cloned nodes).
- **Packaging:** dependencies are pinned with floors; `fail_under = 70` coverage gate.
- **Not yet wired:** despite `models.py` parsing native provider tool-calls, the runtime
  never sends tool *schemas* to any provider (`grep` for `"tools"`/`toolConfig`/
  `functionDeclarations` in `models.py`/`runtime.py` returns nothing) — the loop relies
  entirely on textual `@tool {json}` directives. That is a design gap, not a bug, but it
  is the single biggest lever for making `loro run` behave like a real agent.

The bugs below range from run-crashing (unhandled exceptions on realistic model output)
to security-relevant (redaction that leaves secrets intact, a permission/workspace bypass
in artifact writes, tenant-isolation filters built by string concatenation) to
governance-integrity (retention that never expires, an audit-buffer that refuses new
events instead of evicting old ones). Bugs marked **[verified]** were reproduced directly
against the code during this audit.

---

## Bugs

### Runtime & tool loop

**B1. Malformed model tool directive crashes the entire run** — `src/loro/runtime.py:191-194`
**[verified]**
The loop parses the model's textual directives with
`parse_tool_calls(model_response_content, origin="model")`. `parse_tool_calls`
(`tool_runtime.py:863`) calls `json.loads` with no guard, so a model reply containing
`@tool {bad json` raises `JSONDecodeError`. That call sits *outside* the `try/except`
that wraps `client.complete` (lines 156-183), so the exception propagates out of `run()`
and aborts the whole task with a traceback (no session saved, no `task_completed` audit).
Reproduced: `parse_tool_calls('@tool {"name":"file.read","args":{bad}')` raises
`JSONDecodeError`. **Fix:** wrap the two-line `tool_calls = [...]` construction in
`try/except (ValueError, json.JSONDecodeError)`, and on failure feed the parse error back
to the model as a tool-result turn instead of crashing (models emit malformed JSON
routinely).

**B2. `artifact.create` bypasses the permission engine and workspace-root confinement** —
`src/loro/tool_runtime.py:399-426` **[verified]**
Every other write-like tool builds a `filesystem_resource(...)` (which enforces
`workspace_roots`) and calls `self._authorize(...)`. `_create_artifact` does neither: it
takes `output_dir` straight from `call.args`, runs only the data-protection content scan,
and writes files (plus a `.provenance.json`) to any path on disk. Confirmed by inspection
— the method references neither `permissions` nor `_authorize` nor `filesystem_resource`.
A model-issued `artifact.create` with `output_dir: "/etc/loro"` (or any path outside the
workspace) is written with no approval. **Fix:** normalize `output_dir` through
`filesystem_resource(..., operation="write", workspace_roots=...)` and route it through
`self._authorize(...)` like `_write_file` does.

### Governed data / Polaris

**B3. Polaris resource normalization misses `--flag=value` syntax, defeating catalog-scoped
policy rules** — `src/loro/resources.py:263-273` **[verified]**
`_option_values` only records an option when it is followed by a *separate* token
(`item.startswith("--") and index+1 < len(args)`). For `--catalog=prod` the whole token
is skipped, so the normalized resource's `catalog` field is `""`. Reproduced:
`polaris_resource(["catalogs","list","--catalog=secret-prod"]).fields["catalog"] == ""`
while the space form yields `"secret-prod"`. Any permission rule that scopes governed-data
access by catalog/namespace is silently not matched when the model uses `=` syntax.
**Fix:** split each `--key=value` token on the first `=` before the lookahead branch.

**B4. Polaris read-only guarantee only checks the first two argv tokens (argument
injection)** — `src/loro/polaris.py:191-208`
`_validate_readonly` validates `args[0]` (resource) and `args[1]` (action) against
allowlists but passes `args[2:]` through unchecked, and `tool_runtime.py:492-511` hands
the model's raw `args` list straight to `run_readonly`. The model can append arbitrary
Polaris CLI flags (`--profile`, output-file options, etc.), and any table/namespace value
beginning with `-` is consumed as a flag because there is no `--` separator. **Fix:**
per-subcommand allowlist of permitted flags; reject positional values starting with `-`;
insert `--` before positionals.

### Data protection / secret scanning

**B5. Private-key redaction leaves the entire key body in place** —
`src/loro/data_protection.py:78` **[verified]**
The builtin pattern is only `-----BEGIN [A-Z ]*PRIVATE KEY-----`, so the finding span
covers just the header line. On `redact` surfaces (`tool_output` and `model_output` are
`redact` by default) only that header is replaced. Reproduced — input
`-----BEGIN RSA PRIVATE KEY-----\nMIIEow...KEYMATERIAL\n-----END...` redacts to
`[redacted]\nMIIEow...KEYMATERIAL\n-----END...`; the key material survives into model
output and session records. **Fix:** span the whole block:
`r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"`.

**B6. Assignment-secret pattern misses every `PREFIX_API_KEY=` form** —
`src/loro/data_protection.py:81` **[verified]**
`\b(api[_-]?key|secret|token|password)\b` requires a word boundary, but `_` is a word
character, so `OPENAI_API_KEY=...`, `AWS_SECRET_ACCESS_KEY=...`, `GITHUB_TOKEN=...`,
`MY_PASSWORD=...` never match — reproduced, all return `action=allow, findings=[]`. This
is the dominant real-world shape of leaked credentials (env dumps, `.env` reads, CI logs)
flowing through the `tool_output` surface, and `docs/safety.md` advertises these as
covered. **Fix:** replace the leading `\b` with an `_`-tolerant boundary, e.g.
`(?i)(?:^|[^A-Za-z0-9])[A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|passwd|password|credential)\s*[:=]\s*['"]?[^'"\s]{8,}`.

**B7. Finding preview leaks the first and last 4 characters of every secret** —
`src/loro/data_protection.py:243-246` **[verified]**
`_preview` returns `f"{value[:4]}...{redaction_text}...{value[-4:]}"`. For
`api_key = 'SUPERsecretVALUE12345'` the snippet is `'api_...[redacted]...2345'`. These
snippets are attached to `DataFinding` objects surfaced by `SafetyScanner.scan()` and any
caller that displays findings — 8 characters of a credential is a meaningful disclosure.
**Fix:** return only the redaction text (or `kind` + length), never source characters.

**B8. Bare 40-char AWS secret keys are not detected** —
`src/loro/data_protection.py:85` **[verified]**
Only `AKIA[0-9A-Z]{16}` (access-key id) is matched; a bare secret access key such as
`wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY` in tool output is not flagged (reproduced:
`[]`), despite `docs/data-protection.md` listing "AWS access and secret keys". **Fix:**
add an entropy/shape check for 40-char base64-ish tokens near an assignment operator, or
correct the doc to state the limitation.

### Artifacts

**B9. Spreadsheet variance formulas reference the wrong rows and error on the header** —
`src/loro/artifacts/spreadsheets.py:26-28, 37-40` **[verified]**
Data rows are written starting at `start_row = 4` (header at row 4, Scope at row 5, etc.),
but the formula literals are `=C2-B2`, `=C3-B3`, `=C4-B4`. So D5 computes against empty
cells, and D7 (`=C4-B4`) subtracts the two text headers → `#VALUE!`. `test_spreadsheet_artifact`
only asserts the sheet name, so it is uncaught. **Fix:** build formulas from the row index:
`f"=C{start_row+1+i}-B{start_row+1+i}"`.

**B10. User prompt written as a live formula (formula/CSV injection)** —
`src/loro/artifacts/spreadsheets.py:36`
`sheet["B2"] = prompt.strip()` — openpyxl treats a leading `=`/`+`/`-`/`@` as a formula, so
a prompt starting with one becomes an executable cell in a file the user opens in Excel.
**Fix:** force text (`cell.data_type = "s"` or prefix a `'`), or escape leading formula
characters.

**B11. Artifacts written in the same second overwrite each other** —
`src/loro/artifacts/common.py:17-19` + generators
Filenames are `slugify(title)` only (no timestamp/uuid); two artifacts generated from
similar prompts silently overwrite, including the `.provenance.json` sidecar. **Fix:**
add a short uuid or timestamp suffix, or refuse to overwrite an existing path.

### Memory (retention, isolation, error handling)

**B12. Staged shared-memory drafts silently lose `expires_at` — retention never applies** —
`src/loro/memory/drafts.py:19-34, 49-63` **[verified]**
`SharedMemoryDraft` carries `expires_at` (base.py:26), and `create_shared_memory_draft`
sets it from `retention_days` (operations.py:141). But `stage()` serializes 11 fields and
omits `expires_at`, and `list()` reconstructs the draft without it (defaults to `None`).
Every committed shared memory therefore gets `expires_at = NULL` and never expires,
contradicting `docs/memory.md` ("`retention_days` assigns an expiry when a shared draft is
staged"). **Fix:** add `expires_at` to the staged payload and parse it back in `list()`.

**B13. Iceberg tenant-isolation row filters are built by string concatenation** —
`src/loro/memory/iceberg.py:362-368, 419-421` **[verified]**
`row_filter=f"tenant_id = '{tenant_id.replace(chr(39), chr(39)*2)}' AND ..."` hand-rolls
quote-doubling into the PyIceberg filter-DSL parser — the one place this codebase's own
convention (bound parameters everywhere else) is abandoned, and it is the tenant-isolation
boundary. A value that exploits any quoting quirk of the parser becomes a cross-tenant
read/write. **Fix:** use typed expressions —
`from pyiceberg.expressions import And, EqualTo; And(EqualTo("tenant_id", tenant_id), EqualTo("memory_id", memory_id))` — no string in the trust path.

**B14. Postgres search driver errors escape the "render SQL instead" fallback** —
`src/loro/memory/operations.py:60, 97`
Both branches catch only `RuntimeError`. `PostgresSharedMemoryStore.search` wraps missing
DSN/psycopg as `RuntimeError`, but a bad DSN, unreachable host, auth failure, or missing
table raises `psycopg.Error` (subclass of `Exception`, not `RuntimeError`), producing a
raw traceback out of `loro memory search`. **Fix:** catch `Exception` (or lazily import and
catch `psycopg.Error`) inside the store methods and wrap as `RuntimeError`.

**B15. Postgres `ILIKE` search does not escape `%`/`_` metacharacters** —
`src/loro/memory/postgres.py:209, 215` **[verified]** (`f"%{query}%"`)
Parameterization blocks SQL injection, but the query term itself is a LIKE pattern: `%`
matches all rows and `_` matches any character. **Fix:** escape `\`, `%`, `_` in the term
and add `ESCAPE '\'`.

**B16. Iceberg backend `check()` reports healthy with no catalog configured** —
`src/loro/memory/iceberg.py:272-302`
`ok` is `pyiceberg_available` alone, whereas the Postgres sibling requires a DSN. `loro
memory backend-check` reports the Iceberg backend ready when `LORO_ICEBERG_CATALOG_URI` is
unset. **Fix:** `ok = pyiceberg_available and bool(catalog_props or config.iceberg_warehouse)`.

### Audit chain

**B17. A full audit buffer refuses new events instead of evicting oldest** —
`src/loro/audit/sinks.py:114-123` + `audit/__init__.py:90-92`
When the HTTP sink is down and the JSONL buffer hits `max_buffer_events`, `append` raises
`AuditBufferFullError`, which becomes `AuditDeliveryError` — the *newest* event is dropped
(or the run fails under `failure_mode="fail"`), while stale buffered events are retained.
`docs/audit.md` describes bounded buffering; there is no oldest-first eviction at all.
**Fix:** rotate — drop the oldest line(s), append the new event, and count evictions in
`doctor()`/diagnostics.

**B18. `flush()` load/replace race silently deletes events** —
`src/loro/audit/__init__.py:102-116`
`self.buffer.load()` locks and releases, then `self.buffer.replace([])` locks again. Any
event appended by another process/thread between those two lock windows is deleted by
`replace([])`. `AuditBuffer.append` explicitly supports multi-writer use, so this is a
real loss of audit evidence. **Fix:** add a single-lock `drain()` that loads, delivers,
and rewrites the remainder atomically; only remove payloads actually delivered.

**B19. Legacy audit files with a blank line fail verification untampered** —
`src/loro/audit/sinks.py:303-317 vs 184-217`
`_chain_state` computes the legacy anchor as a hash of the whole file bytes, while
`verify_jsonl_audit` rebuilds it from non-blank raw lines only. Any blank line in the
pre-chain portion (including a trailing `\n\n`) makes the two disagree and reports "Legacy
audit prefix hash does not match" on a file nobody touched. **Fix:** hash the same
normalized (non-blank-line) byte stream in both places.

### Gateway (unauthenticated attack surface)

**B20. Non-ASCII signature header crashes the gateway pre-auth** —
`src/loro/gateway/adapters.py` (Slack/Telegram/bridge verifiers)
`hmac.compare_digest(expected_str, supplied_str)` is called with the raw signature header;
HTTP headers decode as latin-1, so any byte ≥ 0x80 raises
`TypeError: comparing strings with non-ASCII characters` — not a `GatewayAdapterError`, so
it escapes `parse_inbound` and the request handler, giving an unauthenticated attacker a
dropped connection + traceback. **Fix:** compare bytes
(`hmac.compare_digest(expected.encode(), supplied.encode("latin-1", "ignore"))`) or wrap
each verifier in `except (TypeError, ValueError): raise GatewayAdapterError`.

**B21. Request body is JSON-parsed before signature verification, with unbounded
recursion** — `src/loro/gateway/adapters.py:52` + `_discord_text` recursion
`_payload(body)` runs before every platform's verification branch and only catches
`UnicodeDecodeError`/`JSONDecodeError`; deeply nested JSON (well within the 1 MB body cap)
raises `RecursionError`, unauthenticated. Discord option nesting (`collect()`) is
separately unbounded even for signed payloads. **Fix:** verify signature before parsing;
add `RecursionError` to `_payload`'s handler; convert `collect()` to an explicit
depth-capped stack.

**B22. No top-level exception guard in the gateway request path** —
`src/loro/gateway/service.py:88-99, 284-308`
Only `GatewayAdapterError` is handled around `parse_inbound`, and `do_POST` only guards
`rfile.read`. Any other exception (B20/B21, a leaking `CredentialError`) kills the request
thread with no response. **Fix:** add `except Exception` returning `500` plus an audit
event, and wrap the `do_POST` body.

**B23. Gateway-initiated runs drop managed `required_fields`** —
`src/loro/gateway/service.py:145-150`
The gateway rebuilds `IdentityConfig(**gateway_identity.model_dump(), ...)`, but
`GatewayIdentityConfig` carries only subject/tenant/etc., so `required_fields` reverts to
`[]`. An operator's `[identity] required_fields` fail-closed check applies to local runs
but is silently bypassed for every gateway run. **Fix:** carry
`required_fields=self.config.identity.required_fields` (and `environment_prefix`) forward.

**B24. Replay freshness enforced only for Slack** —
`src/loro/gateway/adapters.py:54, 178-209`
`max_age_seconds` is passed only to `_verify_slack`. Discord signs over its timestamp but
never checks recency; the teams/signal/generic bridge HMACs the body with no timestamp at
all. The only remaining defense is the `maxlen`-bounded `_seen` deque (evicts at 10k), so
a captured request replays indefinitely once evicted. `docs/threat-model.md` TM-18 claims
"timestamp checks" generally. **Fix:** enforce freshness on the Discord timestamp and
require a signed timestamp in the bridge envelope, or narrow the TM-18 wording.

**B25. Route matching includes the query string** —
`src/loro/gateway/service.py:64-71`
`endpoint.route == path` compares against `BaseHTTPRequestHandler.path`, which includes
`?query`; configured routes forbid `?`, so any webhook registered with a query string
(common for Telegram) 404s silently. **Fix:** `path = urlsplit(path).path` at the top of
`handle()`.

### Credentials & session messaging

**B26. Malformed credential index raises `AttributeError`, not `CredentialError`** —
`src/loro/credentials.py:147`
`raw.items()` is called before checking `raw` is a mapping; a non-dict index
(`json.loads("[]")` etc.) raises `AttributeError`, which is outside the
`except (OSError, ValueError, TypeError)`. Every CLI entry point catches only
`CredentialError`, so `loro credentials list` tracebacks on a corrupt index. **Fix:**
`if not isinstance(raw, dict): raise CredentialError(...)` before the comprehension.

**B27. Session-message `content_digest` is written but never verified** —
`src/loro/session_messages.py:37, 47-59, 156-182`
`to_payload` stores `sha256:` of the content on save, and `_read` tamper-checks status,
recipient, ids, and size — but never recomputes the digest, and `from_payload` never reads
it. An attacker with write access to `.loro/session-messages/` can rewrite `content` (which
reaches `AgentRuntime` as cross-session context) and leave the stale digest. **Fix:** in
`_read`, compare `message_digest(message.content)` to the stored digest and raise on
mismatch.

### MCP

**B28. MCP server mode `agraph.validate`/`agraph.plan` fail open with empty
`workspace_roots`** — `src/loro/mcp/server.py:116`
The confinement check is `if roots and not any(...)`; `workspace_roots` defaults to `[]`,
so with the default config these exported tools read and return the parsed contents of any
YAML/JSON file on the host — on a surface whose own manifest declares
`"authority": "none"`. **Fix:** in the catalog constructor, refuse to export `agraph.*`
(or file) tools when `workspace_roots` is empty (fail closed).

**B29. Task store read-modify-write is unsynchronized with a shared temp path** —
`src/loro/mcp/tasks.py:48-76`
`save()` does `find()` → merge → `write_text(<sha256>.tmp)` → `replace()`. The temp path
is deterministic and shared by every writer of the same task, so two processes interleave,
and the terminal-status regression guard (tested by
`test_task_store_rejects_terminal_status_regression`) is a TOCTOU a concurrent writer
defeats; `answered_input_keys` merges can also be lost. **Fix:** unique temp name
(`tempfile.mkstemp` in `self.root`) under a lock held across find+write, or an `O_EXCL`
compare-and-swap.

**B30. `inputRequests`/`input_requests` key mismatch makes snake_case tasks
unanswerable** — `src/loro/mcp/tasks.py:147 vs 324-326`
`_validate_detailed_task` accepts either spelling, but `validate_response_keys` reads only
`inputRequests`. A server emitting `input_requests` reaches `input_required` status and
then every `update_task` fails "input keys are not outstanding" — a dead-end task. **Fix:**
normalize to one canonical key in `record_remote`, or check both spellings.

**B31. `connect()` context manager rewrites caller-body exceptions** —
`src/loro/mcp/client.py:417-420`
`connect()` is an `@asynccontextmanager`; exceptions from the caller's `async with` body
are thrown into the generator at `yield` and caught by an `except Exception` that does not
pass through `MCPTaskError`, so task errors surface as a misleading
`MCPClientError("server ... failed")` (cli.py catches the two separately, proving they are
meant to stay distinct). **Fix:** wrap only connection setup in the `try`; add
`MCPTaskError` to the pass-through tuple.

### Agentic Graph (AGS)

The expression engine (`agraph/expressions.py`) deviates from its own reference spec in
several ways; each deviation load-validates and then misbehaves at runtime. Verified
against the vendored `skills/agentic-graph/references/expressions.md`:

**B32. Documented `self` / `nodes.self` / `loop` namespaces are never bound at runtime** —
`src/loro/agraph/execute.py:252-259, 468-473, 733-734, 801-802`
The validator accepts `self.outputs.*`, `nodes.self.*`, and `loop.index/iteration/previous`,
but the executor's scope never contains them (a loop body gets `iteration`, not a `loop`
object). Every documented criterion like `expr: self.outputs.coverage >= 85` validates and
then always fails with "unknown binding 'self'", failing the node for the wrong reason.
**Fix:** bind `self`/`nodes.self` in `_evaluate_criteria`/`_run_task` and a `loop` object
in `_run_composite`.

**B33. Reading a missing member raises instead of yielding `null`** —
`src/loro/agraph/expressions.py:248-253`
Spec: an absent declared value yields `null`; use `default(x, fallback)`. Instead the
documented `default(nodes.apply_fix.outputs.summary, "no fix applied")` raises "object has
no member 'summary'", making the `default()` escape hatch unreachable for its own use case.
**Fix:** return `None` for a missing key on a known namespace mapping.

**B34. `//` and `%` by zero raise raw `ZeroDivisionError`** —
`src/loro/agraph/expressions.py:118-124`
Only `/` is guarded; `1 % params.zero` propagates an unhandled `ZeroDivisionError` out of
`evaluate_expression` (the executor only catches typed `ExpressionError`), crashing the
scheduler. **Fix:** extend the zero-divisor guard to `FloorDiv` and `Mod`.

**B35. `==`/`!=` across types raises instead of returning `false`** —
`src/loro/agraph/expressions.py:76-83`
Spec: different types are never equal, and comparing them is *not* an error. `_same_type`
is applied to equality too, so `1 == true` raises. Note `tests/test_agraph.py:85` currently
asserts the raising behavior, so the test encodes the deviation — resolve the spec question
before "fixing". **Fix:** call `_same_type` only from ordering comparisons; equality
returns `False` on type mismatch.

**B36. `&&`/`||` do not short-circuit; `+` has no array concat; `get()` is not a dotted
path; `interpolate` uses Python `repr`** — `src/loro/agraph/expressions.py:53-65, 103-115,
239-241, 274`
All four deviate from the spec: guards like `succeeded('x') && nodes.x.outputs.y > 0` error
when both sides always evaluate; `params.a + params.b` on lists raises; `get(obj,'k.j','d')`
returns the default instead of walking the path; and interpolation renders `None`/`True`/
`[1, 2]` (Python repr) rather than `""`/`true`/compact JSON. **Fix:** implement
short-circuit evaluation, list concatenation for `+`, dotted-path walking in `get`, and a
spec-compliant stringifier.

**B37. `re.error` from a bad `matches()` pattern escapes uncaught** —
`src/loro/agraph/expressions.py:288, 233`
`matches(s, '[')` raises `re.PatternError`, which is in neither `except` tuple, so it
propagates out of the whole run (e.g. from a `node.when` guard). **Fix:** add `re.error`
to both tuples.

**B38. Executor persists the wrong node's output contract under parallelism** —
`src/loro/agraph/execute.py:~330`
When persisting node outputs the code reads `node.get("outputs", ...)` where `node` is
leaked from an earlier loop and holds the *last* candidate, not the node being written; with
`max_parallel_nodes > 1` outputs are filtered/redacted against the wrong contract. The
parallel test passes only because its nodes are clones. **Fix:** use
`graph["nodes"][node_id].get("outputs", {})` inside the persistence loop.

**B39. Durable resume loses loop iteration counters** —
`src/loro/agraph/execute.py:~604`
Resume rebuilds `loop_state` as `{}`, so a resumed bounded loop restarts at iteration 0 and
`max_iterations` can be exceeded across resume boundaries. **Fix:** persist `loop_state` in
the run record and restore it on resume.

**B40. Policy evaluation crashes on documents that failed schema validation** —
`src/loro/agraph/policy.py:34, 48`
`TIER_RANK[tier]` raises `KeyError` for an out-of-enum tier and
`int(document.get("requires_conformance", 1))` raises `ValueError` for a non-integer, yet
`GraphExecutor.run` and `graph validate` call `evaluate_policy` on raw data before checking
the schema report — so an invalid graph produces a traceback instead of the intended AG001
finding. **Fix:** `TIER_RANK.get(tier, TIER_RANK["standard"])` + guarded int, or skip policy
evaluation when schema errors exist.

**B41. `graph resume` runs without the original workspace, and ignores `--dry-run`** —
`src/loro/cli_graph.py:118-124, 200-202`
`graph run` sets `workspace=path.resolve().parent`; `resume` omits it and defaults to
`Path.cwd()`, so `file_exists`/`artifact_present`/`json_schema` criteria and local
`subgraph.ref` resolution resolve against a different root after resume. Separately,
`resume --dry-run` silently executes real tools. **Fix:** derive the workspace from
`record["metadata"]["source"]` in `resume()`, and honor/reject `--dry-run` on resume.

---

## Recommended Improvements

### Correctness & robustness (near-term)

- **Wrap all model-directive parsing and pre-flight tool resolution in the runtime loop**
  so no realistic model output can abort a run (generalizes B1). Add a fuzz/property test
  feeding random strings through `parse_tool_calls`.
- **Atomic + locked writes for every mutable state file.** `skills._write_json` and
  `mcp/tasks.save` already use temp-file-`replace`; `memory/proposals.py:50` truncates in
  place (crash loses all proposals), `memory/drafts.py` appends without locking, and the
  gateway rewrites its whole seen-ledger per request. Standardize on the `_file_lock` +
  temp-`replace` (+ `fsync`) pattern already present in `audit/sinks.py`.
- **`FileTools.search` hardening** (`tools/files.py:38-52`): skip symlinked files
  (currently follows a symlink out of the workspace and returns its contents under an
  in-workspace path), skip files over a size threshold, and skip binaries (NUL in first
  8 KB) instead of reading every file fully into memory.
- **Sandbox hardening** (`sandbox.py`): the default Bubblewrap profile `--ro-bind / /`
  exposes `~/.ssh`, `~/.aws`, `~/.config/loro/credentials.json`, and (with `--proc` but no
  `--unshare-pid`) other processes' `/proc/<pid>/environ`, while `diagnose()` reports
  `filesystem_os_enforced: True`. Bind only what profiles need and add
  `--unshare-pid --unshare-ipc --unshare-uts`. Also, executable allowlisting matches on
  basename (`_require_allowed_executable`), so shipped defaults (`git`, `polaris`) are
  weaker than the absolute-path examples in `docs/sandbox.md`; require a path separator or
  a trusted-prefix check.
- **MCP SSRF**: `mcp/security.py` resolves the host, then httpx resolves again (DNS
  rebinding); pin the validated address. Document/redirect-hook the `follow_redirects`
  option, which currently bypasses `allowed_hosts` and the private-network check.
- **Validate `MCPServerConfig.env_allowlist` names** the same way
  `SandboxProfileConfig.environment_allowlist` is validated (config.py:87-97), and consider
  a deny-list for loader variables (`LD_PRELOAD`, `PYTHONPATH`).

### Performance

- **O(n²) hotspots:** `AuditBuffer.append` re-parses the whole buffer per append;
  `mcp/client._bounded` re-serializes the entire accumulated payload per page/event;
  `IcebergSharedMemoryStore.search` pulls every tenant row into Python before filtering and
  limiting (push `status`/`expires_at`/`limit` into the scan — the roadmap already claims
  filter pushdown). Track running counts/bytes instead.
- **Hoist per-node object construction in the AGS executor**: `SkillRegistry` and
  `PermissionEngine` are rebuilt per node execution, and the run-record JSON schema is
  re-read and re-parsed from disk on every save.
- **Batch the HTTP audit sink** (one request per event today).

### Testing

- Coverage is broad but shallow. Highest-value additions, each of which would have caught a
  bug above: malformed model directive through the runtime loop (B1);
  `artifact.create` outside `workspace_roots` (B2); Polaris `--flag=value` normalization
  and leading-`-` positionals (B3/B4); `RegexContentScanner` against `FOO_API_KEY=` forms
  and multi-line private-key blocks asserting the *body* is redacted (B5/B6); spreadsheet
  *cell/formula values* not just the sheet name (B9/B10); draft `expires_at` round-trip
  (B12); Iceberg filter escaping and Postgres LIKE metacharacters (B13/B15); audit buffer
  overflow and concurrent flush/append (B17/B18); legacy audit file with a blank line
  (B19); gateway non-ASCII/oversized/missing signature headers, deeply-nested bodies, and
  post-eviction replay (B20/B21/B24); gateway run inheriting `required_fields` (B23);
  corrupt (non-dict) credential index (B26); tampered message content with intact digest
  (B27); `agraph.plan` with empty `workspace_roots` (B28); AGS resume across two gates and
  with a loop, and any criterion using `self.outputs.*`/`loop.iteration` (B32/B38/B39).
- **`tests/test_agraph.py:85` encodes a spec deviation** (asserts `1 == true` raises).
  Resolve the AGX equality question and update the test alongside B35.
- The AGS example fixtures under `tests/fixtures/agraph/examples` are only *validated*,
  never *executed* — add an execution smoke test so runtime-only bugs (B32–B41) surface.

### Documentation & DX

- Reconcile docs with actual behavior for the items above (audit oldest-first eviction,
  AWS secret-key detection, `api_key`/`token` assignment coverage, Iceberg filter pushdown,
  threat-model TM-18 timestamp checks, memory retention). Each currently over-claims.
- Several accepted-but-unimplemented AGS spec fields silently no-op at execution
  (`policy.on_expression_error`, `map.max_parallel`, `gate.timeout_seconds`/`on_timeout`/
  `on_reject`, `criterion.timeout_seconds` except `command`, `success.evaluation_order`,
  `failure.escalation`). Emit a "feature not supported" diagnostic at load so a graph does
  not appear governed when it is not.
- `execute.py` (1079 lines) and `cli.py` (4137 lines) mix many concerns; split
  scheduler/state/dispatch out of the executor and per-domain command modules out of the
  CLI to reduce review surface.
- `reference_validator.py` keeps an inline copy of the JSON schema that already lives under
  `agraph/schema/` — a drift risk; load the file instead.

---

## Recommended New Features

1. **Native provider tool-calling (highest leverage).** `models.py` already *parses*
   OpenAI/Anthropic/Gemini/Bedrock tool calls, but the runtime never *sends* tool schemas,
   so real models cannot invoke tools except by emitting the textual `@tool` DSL. Publish a
   typed tool-schema catalog and include it in each provider request; keep the textual DSL
   as the deterministic test path. This is the difference between a scaffold and a working
   agent.
2. **Streaming output for `loro run`/`plan`.** The client `stream()` interface exists but
   `BaseModelClient.stream` just yields the whole completion. Real token streaming with a
   live Rich display would materially improve UX for long tasks.
3. **`loro doctor` aggregate health command.** The README references `loro doctor`; a single
   command that runs identity resolution, provider check, sandbox `diagnose`, audit
   `doctor`, memory `backend-check`, and MCP `doctor` and prints a consolidated pass/fail
   table would be a strong first-run and CI signal.
4. **Approval and audit query/reporting CLI.** Given the identity-bound approval records and
   the tamper-evident audit chain, add `loro audit query`/`loro approvals list` with filters
   (actor, tenant, action, time range) and a verify-and-summarize report for compliance
   evidence — the enterprise-evidence docs ask for exactly this.
5. **MCP tool export as first-class runtime tools.** MCP server mode exports a read-only
   subset; the inverse — surfacing a remote MCP server's tools as native `@tool` targets
   with per-tool policy — would let Loro compose external capabilities under the same
   approval/audit envelope.
6. **Retention/lifecycle sweeper.** With B12 fixed, add a `loro memory sweep` job that
   expires or holds shared memories past `expires_at`, emitting audit events — closing the
   loop on the retention feature the schema already models.
7. **Config linting (`loro config check`).** Surface risky-but-valid configurations
   proactively: empty `workspace_roots` with MCP server mode or artifact tools enabled
   (B2/B28), `redact` on a persistence surface, sandbox profiles with basename-only
   executables, gateway endpoints without freshness enforcement.
