# Development Roadmap

## Done In Scaffold

- Typer CLI
- Config layering
- Local memory
- Sessions
- JSONL audit
- Artifact generation and provenance
- File and shell tools
- Read-only Polaris wrapper validation
- Safety scanning before memory and artifact writes
- Provider profiles and local configuration wizard
- Shared memory schema and draft workflow
- Postgres shared memory SQL adapter, schema apply command, and backend check
- Iceberg shared memory SQL adapter
- Polaris typed catalog, namespace, table, and view commands
- Polaris typed role, privilege, policy, and applicable-policy commands
- Explicit typed runtime tool loop for file read/search
- Glob-based permission policy rules

## Next MVP Work

- Iceberg governed execution integration
- Complete model provider adapters, including Bedrock and streaming
- Model-directed tool-calling runtime loop
- Secret scanning before memory and artifact writes

## Enterprise Hardening

- Managed non-overridable config
- SSO/internal model gateway integration
- Real approval prompts in TUI
- Sandbox profiles
- Audit sinks beyond local JSONL
- Integration tests for Postgres, Iceberg, and Polaris
