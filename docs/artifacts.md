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
- Generated paths
- Prompt preview
- Assumptions
- Timestamp
- Generator identifier

This sidecar is intentionally separate from the artifact so future governance systems can ingest it.

## Safety Notes

The deterministic MVP generators do not query governed data or embed external data. Future model-powered generation must classify inputs and avoid restricted data unless policy and user confirmation allow it.
