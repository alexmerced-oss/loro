# Testing

Run the standard suite:

```bash
python -m pytest
python -m ruff check .
python -m compileall src tests
```

Install development extras to enable coverage reporting:

```bash
python -m pip install -e ".[dev]"
python -m pytest --cov --cov-report=term-missing
```

Current high-value gaps that need external services or credentials:

- Live Postgres shared-memory commits.
- Live Iceberg/Polaris governed execution.
- Live model-provider completions for cloud providers.
- Bedrock adapter behavior once AWS SDK support is implemented.
