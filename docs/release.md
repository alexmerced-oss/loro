# Release Checklist

Use this checklist before tagging or publishing Loro.

## Local Verification

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest --cov --cov-report=term-missing --cov-report=json:security-coverage.json
python scripts/check_security_coverage.py security-coverage.json
python -m compileall src tests
```

If optional services are available:

```bash
python -m pip install -e ".[dev,integration,data,aws,mcp]"
LORO_INTEGRATION_POSTGRES=1 python -m pytest -m integration tests/integration/test_postgres_memory_integration.py
LORO_INTEGRATION_POLARIS=1 python -m pytest -m integration tests/integration/test_polaris_cli_integration.py
```

## Smoke Checks

```bash
loro --version
loro doctor
loro providers list
loro providers smoke "hello" --provider mock --execute --stream
loro memory schema --backend postgres
loro memory schema --backend iceberg
loro data polaris catalogs list
loro mcp doctor
loro mcp server-inspect
loro skills list
loro graph validate docs/examples/agraph/release-readiness.agraph.yaml --strict
loro graph plan docs/examples/agraph/release-readiness.agraph.yaml --json
loro skills validate "$(loro graph skill-path)"
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

- `0.1.1`: updated the Nous Portal endpoint to `https://inference-api.nousresearch.com/v1`.
- `0.1.2`: omitted unsupported `temperature` for OpenAI `gpt-5*` and Anthropic
  `claude-sonnet-5*` requests.
- `0.1.3`: omitted deprecated Gemini sampling config for `gemini-3.6-flash` and
  `gemini-3.5-flash-lite`.

Release `0.3.0` adds AGS 1.0 conformance level 3, managed graph policy, durable graph execution,
model-tier routing, graph generation, read-only MCP graph tools, and a bundled authoring Skill.
See [Loro 0.3.0](releases/0.3.0.md) for the complete release notes and external deployment gates.

## Documentation

- Confirm `README.md` examples still match CLI behavior.
- Confirm `docs/roadmap.md` statuses are current.
- Confirm `docs/providers.md`, `docs/memory.md`, `docs/polaris-iceberg.md`, and `docs/mcp.md` reflect any
  changed command names or safety guarantees.
- Confirm the MCP support matrix matches green conformance workflow artifacts for the release
  commit, and run Agent Skills/session-message security tests.
- Confirm the AGS conformance workflow is green on the release commit and the bundled Skill is
  present in the wheel.
- Confirm [Channel Gateways](channel-gateways.md) still accurately describes unsupported bot
  surfaces; do not advertise Slack, Discord, or Telegram without adapter and security evidence.

## Packaging

```bash
python -m pip install --upgrade build twine
rm -rf dist build
python -m build
python -m twine check dist/*
```

Tags and manual dispatches run `Release Evidence`, which verifies tag/version agreement, builds
distributions in protected CI, smoke-tests the wheel and bundled Agent Skill, generates SHA-256
checksums, creates GitHub/Sigstore build-provenance attestations, and retains the artifact bundle.
Verify an attested artifact with:

```bash
gh attestation verify dist/loro_agent-*.whl --repo alexmerced-oss/loro
(cd dist && sha256sum --check SHA256SUMS)
```

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
