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
- Basic tests for CLI and configuration behavior.

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
loro docs create "Draft a project kickoff document"
```

## License

MIT
