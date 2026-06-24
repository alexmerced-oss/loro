# CLI Guide

## Core Commands

```bash
loro --version
loro doctor
loro config
loro plan "Draft a rollout plan"
loro run "Summarize the project"
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

## Governed Data

```bash
loro data catalogs
loro data polaris catalogs list
```

Polaris commands require `[polaris].enabled = true` and are restricted to read-only operation families.
