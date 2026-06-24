# Loro

Loro is a Python CLI agent harness for enterprise coding, governed data work, and productivity tasks.

"Loro" is Spanish for parrot: an intelligent, social bird that listens, learns, repeats useful knowledge, and helps information move across groups.

## Current Status

This repository is an initial scaffold based on [PRD.md](./PRD.md). It includes:

- A Typer-powered CLI entrypoint.
- Configuration loading from managed, user, project, local, environment, and runtime sources.
- Permission decision primitives.
- Local and shared memory interfaces.
- Postgres and Iceberg memory backend placeholders.
- Polaris client and artifact-generation module placeholders.
- Real MVP artifact generation for Markdown/DOCX documents, PPTX presentations, XLSX/CSV spreadsheets, and Markdown briefs.
- Artifact provenance sidecars that record prompt previews, generated paths, assumptions, and generator metadata.
- JSONL audit logging for runtime tasks, memory writes, and artifact creation.
- Durable session records with `loro sessions list` and `loro sessions show`.
- Permission-gated file and shell tools.
- Shared memory draft staging and Postgres/Iceberg schema output.
- Safety scanner for obvious secrets before memory and artifact writes.
- AI provider profiles and `loro configure` setup wizard.
- A read-only Polaris CLI wrapper for governed catalog discovery scaffolding.
- Basic tests for CLI, configuration, memory, audit, and artifact behavior.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
loro --help
pytest
```

## Example

```bash
loro plan "Create a release readiness checklist"
loro remember --local "Status briefs should include risks, blockers, next steps, and owner."
loro remember --shared "Use the enterprise launch readiness template for launches."
loro memory drafts
loro memory schema --backend postgres
loro docs create "Draft a project kickoff document"
loro slides create "Quarterly platform update"
loro sheets create "Launch readiness tracker"
loro brief meeting "Prepare for roadmap sync"
loro memory search "status briefs"
loro sessions list
loro file search "Polaris" --root .
loro file read PRD.md --limit 1000
loro shell run --yes -- python -c "print('hello from Loro')"
loro safety scan "api_key = 'abc123456789'"
loro providers list
loro configure --provider ollama --model llama3.2 --small-model llama3.2
```

Generated files are written to `artifacts/` by default. Use `--output-dir` to choose another location. Each generated artifact also gets a `.provenance.json` sidecar.

Configuration can be layered from `.loro/config.toml`, `LORO_CONFIG`, and `LORO_CONFIG_CONTENT`.

For shell commands, use `--` before the command when passing flags to the child process.

Memory and artifact commands scan for obvious secrets by default. Use `--allow-sensitive` only when enterprise policy allows that content to be persisted.

## License

MIT
