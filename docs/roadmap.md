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
- Postgres shared memory SQL adapter and backend check
- Iceberg shared memory SQL adapter

## Next MVP Work

- Iceberg governed execution integration
- Postgres migration/apply command and live integration tests
- Complete model provider adapters, including Bedrock and streaming
- Typed tool-calling runtime loop
- Richer permission policy matching
- Polaris typed client methods
- Secret scanning before memory and artifact writes

## Enterprise Hardening

- Managed non-overridable config
- SSO/internal model gateway integration
- Real approval prompts in TUI
- Sandbox profiles
- Audit sinks beyond local JSONL
- Integration tests for Postgres, Iceberg, and Polaris
