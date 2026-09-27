# Coding Tools: Patches, Tests, And Web Fetch

Status: experimental in 0.22. All three are ordinary Loro tools: they go through permission
policy, approvals, data protection on their output, and the audit log (`runtime.tool_executed`
with the metadata listed below), and agent profiles can include or exclude them by name.

## `patch.apply`

Applies a unified diff (the `---`/`+++`/`@@` format from `git diff` or `diff -u`) under `root`
(default: the current directory).

```text
@tool {"name": "patch.apply", "args": {"patch": "--- a/app.py\n+++ b/app.py\n@@ ...", "dry_run": true}}
```

- Modify, create (`--- /dev/null`), delete (`+++ /dev/null`) and rename (different paths) are
  supported. Paths must be relative, without `..`, and inside `permissions.workspace_roots`.
- All or nothing: every hunk of every file is located first. If any hunk's context does not
  match, nothing is written, and the output lists each conflict with the file, hunk number, line,
  the expected text and the text actually found.
- Hunks may sit up to 200 lines away from their stated position (the file changed since the
  diff was made); context itself must match exactly.
- `dry_run: true` reports what would change and needs no approval. Writing uses the `edit`
  permission (`ask` by default, so it needs approval) and the same secret checks as `file.write`
  on added lines. Files are written atomically.
- Audit metadata: `patch_files`, `patch_conflicts`, `patch_dry_run`, `patch_applied`.

## `tests.run`

Runs a project's test suite and returns the runner's result line plus the tail of its output.

```text
@tool {"name": "tests.run", "args": {"path": ".", "args": ["-k", "parser"]}}
```

- `runner` is `auto` (default), `pytest`, `npm` or `cargo`. Auto detection picks `cargo` for a
  `Cargo.toml`, `npm` for a `package.json` with a `test` script, and `pytest` for a Python project
  (`pyproject.toml`, `pytest.ini`, `setup.cfg`, `tox.ini`, `conftest.py` or `tests/`).
- Commands are `python3 -m pytest -q`, `npm test --silent`, and `cargo test`. Extra `args` must
  be at most 32 plain tokens; no shell is involved.
- It runs in the `test-runner` sandbox profile (`sandbox.test_profile`): allowlisted executables
  `python*`, `pytest`, `npm`, `node`, `cargo`, `rustc`, up to 900 seconds and 2 MB of output.
  Binaries inside the workspace are never trusted by name, so a project virtualenv's Python is
  not used unless you configure the profile for it.
- Output keeps the last 20,000 characters (where failures are reported); `max_chars` changes it.
- Uses the `shell` permission (`ask` by default). Audit metadata: `test_runner`, `returncode`,
  `sandbox_profile`, `sandbox_os_enforced`, `output_truncated`.

## `web.fetch`

Fetches one text page. It is off until an operator allowlists domains **and** web policy allows
it (`permissions.web` is `deny` by default):

```toml
[permissions]
web = "ask"

[web_fetch]
allowed_domains = ["docs.python.org", "*.readthedocs.io"]
max_bytes = 500000
timeout_seconds = 15
max_redirects = 3
allow_http = false
```

- Only `https` (plus `http` when `allow_http = true`), no embedded credentials, no literal IP
  addresses, and only allowlisted hosts (`*.example.com` matches subdomains, not the apex).
- Server-side request forgery protection: the host must resolve only to public addresses (no
  loopback, private, link-local such as `169.254.169.254`, carrier-grade NAT, multicast or
  reserved ranges, including IPv4-mapped IPv6), and the address actually connected to is checked
  again before the body is read, which defeats DNS rebinding. Every redirect hop is checked the
  same way. Ambient proxy settings are ignored.
- Bodies stop at `max_bytes` and `timeout_seconds`; non-text content types are refused.
- The result is labelled untrusted web content and passes the tool-output data-protection policy.
- Audit metadata: `web_host`, `web_status`, `web_bytes`, `web_redirects`.

Limits: the request is sent before the connected address can be confirmed, so a GET to a
rebinding target is refused only at the response. Only `GET` is supported.
