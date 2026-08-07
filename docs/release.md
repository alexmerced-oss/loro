# Release Checklist

Use this checklist before tagging or publishing Loro.

## Local Verification

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest --cov --cov-report=term-missing
python -m compileall src tests
```

If optional services are available:

```bash
python -m pip install -e ".[dev,integration,data,aws]"
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
```

Only run live provider smoke checks when credentials and spend controls are approved:

```bash
loro providers smoke "hello" --provider openai --model <model> --execute
```

## Documentation

- Confirm `README.md` examples still match CLI behavior.
- Confirm `docs/roadmap.md` statuses are current.
- Confirm `docs/providers.md`, `docs/memory.md`, and `docs/polaris-iceberg.md` reflect any
  changed command names or safety guarantees.

## Packaging

```bash
python -m pip install --upgrade build twine
rm -rf dist build
python -m build
python -m twine check dist/*
```

## Publish To PyPI

Confirm the version in both `pyproject.toml` and `src/loro/__init__.py`, then publish:

```bash
python -m twine upload dist/*
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
```

Publishing should be done only after local validation and CI pass on the release commit.
