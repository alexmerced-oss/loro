# CLI Guide

## Core Commands

```bash
loro --version
loro doctor
loro config
loro configure
loro plan "Draft a rollout plan"
loro run "Summarize the project"
```

`plan` and `run` can execute explicit typed tool directives in the prompt:

```bash
loro plan '@tool file.read {"path": "README.md", "limit": 1000}'
loro plan '@tool {"name": "file.read", "args": {"path": "README.md", "limit": 1000}}'
loro plan '@tool file.search {"query": "Polaris", "root": ".", "limit": 5}'
```

The runtime loop also lets model responses request tools with the JSON directive form.
Loro executes approved tool calls, returns tool results to the model, and stops when the
model responds without tool directives or `[runtime].max_steps` is reached. The initial
tool registry supports:

- `file.read`: `{"path": "README.md", "limit": 1000}`
- `file.search`: `{"query": "Polaris", "root": ".", "limit": 5}`
- `file.write`: `{"path": "notes.md", "content": "Hello", "approved": true}`
- `file.replace`: `{"path": "notes.md", "old": "Hello", "new": "Hi", "approved": true}`
- `git.status`: `{"cwd": "."}`
- `git.diff`: `{"cwd": "."}`
- `git.show`: `{"cwd": ".", "revision": "HEAD"}`
- `git.add`: `{"cwd": ".", "paths": ["notes.md"], "approved": true}`
- `git.commit`: `{"cwd": ".", "message": "Update notes", "approved": true}`
- `memory.search`: `{"query": "launch template", "limit": 10}`
- `memory.shared_search`: `{"query": "launch template", "tenant_id": "acme"}`
- `shell.run`: `{"args": ["python", "-c", "print(123)"], "approved": true}`
- `polaris.readonly`: `{"args": ["catalogs", "list"]}`
- `artifact.create`: `{"kind": "document", "prompt": "Draft onboarding guide"}`

Runtime write-like calls still obey configured permissions. When policy is `ask`, `file.write`,
`file.replace`, `git.add`, `git.commit`, and `shell.run` must include `"approved": true`;
`deny` always blocks execution. File writes/replacements and artifact creation use the same
safety scanner as CLI write commands. Polaris runtime calls require `[polaris].enabled = true`
and are constrained to read-only operations. Artifact runtime calls support `document`,
`presentation`, `spreadsheet`, and `brief`; they write provenance sidecars.

## Providers

```bash
loro providers list
loro providers show openai
loro providers check openai
loro providers request "hello" --provider openai --model gpt-4.1
loro providers smoke "hello" --provider openai --model gpt-4.1
loro providers smoke "hello" --provider openai --model gpt-4.1 --execute --stream
loro configure --provider ollama --model llama3.2 --small-model llama3.2
```

## Memory

```bash
loro remember --local "Status briefs should include risks and next steps."
loro memory list
loro memory search "status briefs"
loro memory shared-search "launch readiness" --tenant-id acme
loro memory shared-search "launch readiness" --tenant-id acme --dry-run
loro memory propose "Use concise status summaries" --target local
loro memory propose "Use the enterprise launch readiness template" --target shared
loro memory proposals
loro memory accept-proposal <proposal-id>
```

Shared memory is explicit-only. Loro can search configured shared memory, stage shared-memory
drafts, and render or execute supported backend SQL, but it never autonomously commits shared
memory. Accepting a shared proposal creates a draft that still requires an explicit
`commit-draft` step.

## Artifacts

```bash
loro docs create "Draft a project kickoff document"
loro slides create "Quarterly platform update"
loro sheets create "Launch readiness tracker"
loro brief meeting "Prepare for roadmap sync"
```

Use `--output-dir` to choose where generated files go.

## Files And Shell

```bash
loro file read README.md --limit 1000
loro file search "Polaris" --root .
loro shell run --yes -- python -c "print('hello')"
```

Use `--` before child commands that have flags.

## Sessions

```bash
loro sessions list
loro sessions show <session-id>
```

## Safety

```bash
loro safety scan "api_key = 'abc123456789'"
loro safety scan --file .env
```

Memory and artifact commands scan for obvious secrets before writing files or memory records. Use `--allow-sensitive` only when policy allows persistence.

## Governed Data

```bash
loro data catalogs
loro data namespaces --catalog prod
loro data tables --catalog prod --namespace analytics
loro data schema events --catalog prod --namespace analytics
loro data explain-access events --catalog prod --namespace analytics --catalog-role reader
loro data views --catalog prod --namespace analytics
loro data principal-roles
loro data catalog-roles --catalog prod
loro data privileges --catalog prod --catalog-role reader
loro data policies --catalog prod
loro data applicable-policies events --catalog prod --namespace analytics
loro data polaris catalogs list
```

Polaris commands require `[polaris].enabled = true`. Typed commands cover common catalog,
namespace, table, view, role, privilege, and policy discovery. The lower-level
`data polaris` escape hatch is restricted to read-only operation families.
Use `data schema` and `data explain-access` for higher-level governed metadata summaries
without querying table data.
