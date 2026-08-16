# Release Checklist

Use this checklist before tagging or publishing Loro.

## Local Verification

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
PYTHONPATH=src python scripts/check_audit_inventory.py
PYTHONPATH=src python scripts/check_enterprise_evidence.py
python scripts/check_data_support_matrix.py
PYTHONPATH=src python scripts/check_interoperability_matrix.py
PYTHONPATH=src python scripts/audit_outage_drill.py --events 1000
python -m pytest --cov --cov-report=term-missing --cov-report=json:security-coverage.json
python scripts/check_security_coverage.py security-coverage.json
python -m compileall src tests
```

If optional services are available:

```bash
python -m pip install -e ".[dev,integration,data,aws,mcp]"
LORO_INTEGRATION_POSTGRES=1 python -m pytest -m integration \
  tests/integration/test_postgres_memory_integration.py \
  tests/integration/test_postgres_recovery_integration.py
LORO_INTEGRATION_POLARIS=1 python -m pytest -m integration tests/integration/test_polaris_cli_integration.py
```

## Smoke Checks

```bash
loro --version
loro get-started --topic setup
loro doctor
loro providers list
loro configure mock
loro config check --strict
loro providers smoke "hello" --provider mock --execute --stream
loro providers conformance
loro memory schema --backend postgres
loro memory schema --backend iceberg
loro memory migrate --target 2
loro operations recovery-targets
loro data polaris catalogs list
loro mcp doctor
loro mcp server-inspect
loro skills list
loro skills import-claude --help
loro skills import-pi --help
loro agents create release-reviewer --instructions "Review release evidence."
loro agents validate .loro/agents/release-reviewer.agent.yaml
loro agents explain release-reviewer
loro graph validate docs/examples/agraph/release-readiness.agraph.yaml --strict
loro graph plan docs/examples/agraph/release-readiness.agraph.yaml --json
loro skills validate "$(loro graph skill-path)"
loro docs create "Release verification" --no-ai --output-dir /tmp/loro-release-artifacts
loro artifacts verify /tmp/loro-release-artifacts/*.provenance.json
```

When a secure OS keyring and test gateway configuration are available:

```bash
loro credentials doctor
loro gateway doctor
```

Only run live provider smoke checks when credentials and spend controls are approved:

```bash
loro providers smoke "hello" --provider openai --model gpt-5.6-luna --execute
loro providers smoke "hello" --provider anthropic --model claude-sonnet-5 --execute
loro providers smoke "hello" --provider gemini --model gemini-3.6-flash --execute
loro providers smoke "hello" --provider nous --model deepseek/deepseek-v4-flash --execute
loro providers smoke "hello" --provider openrouter --model deepseek/deepseek-v4-flash --execute
loro providers smoke "hello" --provider opencode-zen --model deepseek-v4-flash --execute
```

Recent patch releases:

- `0.4.1`: protected native tool arguments before execution, made Iceberg state/event retries
  audit-first and idempotent, normalized Iceberg timestamps to UTC, repaired gateway queue/replay
  rollback, and added TrustedRouter and Prime Intellect provider profiles.
- `0.1.1`: updated the Nous Portal endpoint to `https://inference-api.nousresearch.com/v1`.
- `0.1.2`: omitted unsupported `temperature` for OpenAI `gpt-5*` and Anthropic
  `claude-sonnet-5*` requests.
- `0.1.3`: omitted deprecated Gemini sampling config for `gemini-3.6-flash` and
  `gemini-3.5-flash-lite`.

Release `0.4.0` adds native provider tool calling, protocol-safe streaming, compliance queries,
managed graph and MCP improvements, retention operations, and the August 2026 security hardening.
See [Loro 0.4.0](releases/0.4.0.md) for the complete release notes and external deployment gates.
Release `0.4.1` is the recommended patch and provider-profile update. See
[Loro 0.4.1](releases/0.4.1.md).

Release `0.5.0` adds versioned control contracts, durable single-host approval storage, executable
audit/evidence inventories, poisoned-memory labeling, and artifact-bound release evidence. See
[Loro 0.5.0](releases/0.5.0.md).

Release `0.6.0` adds versioned Postgres memory migrations, idempotent operation IDs,
reconciliation, a pinned Polaris/Iceberg/DuckDB matrix, authenticated audit collection,
content-free metrics, and executable backup/restore drills. See [Loro 0.6.0](releases/0.6.0.md).

Release `0.7.0` adds governed provider contracts, route pinning and correlation, frozen MCP/Skill
claims, graph failure-injection evidence, and signed gateway interoperability fixtures. See
[Loro 0.7.0](releases/0.7.0.md).

Release `0.8.0` adds the versioned enterprise-beta reference bundle, content-free benchmark gate,
role-based operational documentation, and beta support contract. See
[Loro 0.8.0](releases/0.8.0.md).

Release `0.9.0` freezes the machine-readable release contract, adds installed-environment
readiness evidence, and publishes pilot/assurance/consumer-verification procedures. See
[Loro 0.9.0](releases/0.9.0.md).

Release `0.10.0` resolves repository hardening findings, freezes the deliberately small stable
core, adds digest-bound artifact verification, and establishes signed release tags. See
[Loro 0.10.0](releases/0.10.0.md).

Release `0.11.0` preserves that stable core and adds experimental provisional OAP v1 Level 2 named
agents, fail-closed narrowing, untrusted state, and atomic `/state`-only writeback. See
[Loro 0.11.0](releases/0.11.0.md).

Release `0.12.0` completes provisional OAP Level 3 harness behavior with composition, scoped MCP,
Skills and memory, bounded subagents, and Agentic Graph profile binding. See
[Loro 0.12.0](releases/0.12.0.md).

Release `0.13.0` adds selectable provider/model setup, the folder REPL, and governed structured
AI drafting for productivity artifacts. See [Loro 0.13.0](releases/0.13.0.md).

Release `0.14.0` adds live provider model discovery, a complete profile wizard, streaming REPL
tool activity, model-authored artifact enforcement, AI-compiled executable graphs, and the
context-aware `get-started` guide. See [Loro 0.14.0](releases/0.14.0.md).

## Documentation

- Confirm `README.md` examples still match CLI behavior.
- Confirm `docs/roadmap-1.0.md` statuses and remaining gates are current.
- Confirm `scripts/generate_release_contract.py --check` passes without unreviewed drift.
- Confirm `docs/providers.md`, `docs/memory.md`, `docs/polaris-iceberg.md`, and `docs/mcp.md` reflect any
  changed command names or safety guarantees.
- Confirm the MCP support matrix matches green conformance workflow artifacts for the release
  commit, and run Agent Skills/session-message security tests.
- Confirm the AGS conformance workflow is green on the release commit and the bundled Skill is
  present in the wheel.
- Confirm gateway signature/replay/identity tests pass and the supported adapters match
  [Channel Gateways](channel-gateways.md).
- Confirm the OS credential backend fails closed when unavailable and release artifacts contain no
  vault values.

## Packaging

```bash
python -m pip install --upgrade build twine
rm -rf dist build
python -m build
python -m twine check dist/*
```

Tags and manual dispatches run `Release Evidence`, which verifies tag/version agreement and the
SSH tag signature, builds
distributions in protected CI, smoke-tests the wheel and bundled Agent Skill, generates SHA-256
checksums, creates GitHub/Sigstore build-provenance attestations, and retains the artifact bundle.
The bundle also contains the machine-readable product, data, and interoperability support
matrices, CycloneDX
release SBOM, and a release manifest binding the commit, workflow run, artifact digests,
evidence documents, and known limitations.
Verify an attested artifact with:

```bash
gh attestation verify dist/loro_agent-*.whl --repo alexmerced-oss/loro
(cd dist && sha256sum --check SHA256SUMS)
```

See [Release Signing And Verification](release-signing.md) for tag verification and the signing
key fingerprint.

The workflow intentionally does not upload to PyPI. Publication still requires release-owner
approval after all protected checks and external evidence gates pass.

## Publish To PyPI

Confirm the version in both `pyproject.toml` and `src/loro/__init__.py`, then publish:

```bash
python -m twine upload dist/loro_agent-*.whl dist/loro_agent-*.tar.gz
```

Twine should discover credentials from the standard environment variables, keyring, or
`~/.pypirc`.

## Post-Publish Smoke Test

Use a fresh environment after PyPI has the release:

```bash
python -m venv /tmp/loro-release-smoke
/tmp/loro-release-smoke/bin/python -m pip install --upgrade pip
/tmp/loro-release-smoke/bin/python -m pip install loro-agent
/tmp/loro-release-smoke/bin/loro --version
/tmp/loro-release-smoke/bin/loro doctor
/tmp/loro-release-smoke/bin/loro providers list
/tmp/loro-release-smoke/bin/loro providers smoke "hello" --provider mock --execute --stream
/tmp/loro-release-smoke/bin/python -m pip install "loro-agent[mcp]"
/tmp/loro-release-smoke/bin/loro mcp doctor
```

Publishing should be done only after local validation and CI pass on the release commit.
