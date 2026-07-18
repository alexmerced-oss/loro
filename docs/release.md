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
python -m pip install build twine
python -m build
python -m twine check dist/*
```

Publishing should be done only after CI passes on the release commit.
