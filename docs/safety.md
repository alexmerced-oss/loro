# Safety

Loro includes a lightweight scanner for obvious secrets and sensitive credential patterns.

## Current Detections

- Private key headers
- `api_key`, `secret`, `token`, or `password` assignments
- GitHub token-looking strings
- AWS access key IDs

## Commands

```bash
loro safety scan "api_key = 'abc123456789'"
loro safety scan --file .env
```

## Write Gates

The scanner runs before:

- Local memory writes
- Shared memory draft staging
- Document generation
- Presentation generation
- Spreadsheet generation
- Brief generation

When `[safety].block_on_findings = true`, findings block writes unless the user passes `--allow-sensitive`. That flag should only be used when enterprise policy explicitly allows the content to be persisted.

## Limits

This is a defensive MVP scanner, not a full DLP engine. Enterprise deployments should integrate managed secret scanning, data classification, and audit sinks.
