# Safety And Data Protection

Loro includes a managed classification and scanning engine with a compatibility facade for the
original secret scanner.

## Current Detections

- Private key headers
- `api_key`, `secret`, `token`, or `password` assignments
- GitHub token-looking strings
- AWS access key IDs

## Commands

```bash
loro safety scan "api_key = 'abc123456789'"
loro safety scan --file .env
loro safety scan --surface model_input "[classification: restricted] production export"
loro safety doctor
```

## Enforcement

The managed engine covers:

- Composed model input and model output
- Local and shared memory writes
- Artifact prompts and provenance
- Session records and cross-session messages
- Tool output and recursively nested audit details

Surface policy chooses allow, redact, or block and sets a classification ceiling. Managed
configuration can add patterns, allow named finding kinds on exact surfaces, and set
`[safety].allow_sensitive_override = false` so `--allow-sensitive` cannot bypass policy.

## Limits

The built-in regex scanner is not corporate DLP proof. Enterprise deployments must still
integrate an approved scanner or gateway and review provider-specific flows. See
[Managed Data Protection](data-protection.md) for the decision contract and remaining gates.
