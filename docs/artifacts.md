# Artifacts

Loro generates enterprise productivity artifacts from approved user prompts and local context.

## Supported MVP Outputs

- Documents: `.md`, `.docx`
- Presentations: outline `.md`, `.pptx`
- Spreadsheets: `.xlsx`, `.csv`
- Briefs: `.md`

## Provenance

Each generated artifact gets a `.provenance.json` sidecar containing:

- Artifact title and kind
- Generated paths with byte counts and SHA-256 digests
- Prompt preview
- Assumptions
- Timestamp
- Generator identifier and provenance schema version

Verify every bound file before using the sidecar as evidence:

```bash
loro artifacts verify report.docx.provenance.json
```

The command exits nonzero when a file is missing, its size changed, its digest changed, or the
record does not use the supported schema. The sidecar is intentionally separate from the artifact
so governance systems can ingest it; protect both files from unauthorized replacement.

## Safety Notes

The deterministic MVP generators do not query governed data or embed external data. Future model-powered generation must classify inputs and avoid restricted data unless policy and user confirmation allow it.
