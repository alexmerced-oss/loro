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

## Providers

```bash
loro providers list
loro providers show openai
loro providers check openai
loro providers request "hello" --provider openai --model gpt-4.1
loro configure --provider ollama --model llama3.2 --small-model llama3.2
```

## Memory

```bash
loro remember --local "Status briefs should include risks and next steps."
loro memory list
loro memory search "status briefs"
```

Shared memory is explicit-only. Current MVP support stages shared memory drafts and generates backend schemas rather than writing to a live enterprise backend by default.

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
