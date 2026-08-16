# Artifacts

Loro generates enterprise productivity artifacts from approved user prompts and local context.
The direct CLI commands use the configured model to author complete content before a deterministic
renderer writes the files.

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

## AI Drafting Contract

`loro docs create`, `loro slides create`, `loro sheets create`, `loro sheets analyze`, `loro brief
*`, and the `loro create` aliases require a schema-valid model draft by default. Loro parses the
model's direct response, asks once for a corrected draft when validation fails, and writes no files
unless a draft validates. A resolved `mock` provider is treated as unconfigured for this workflow;
run `loro configure` to select a real provider and model.

Use `--no-ai` only when an offline scaffold is intentionally useful. This mode is deterministic
and does not claim that the resulting content was model-authored.

Agents use the runtime `artifact.create` tool as the rendering half of the same workflow. The agent
must provide final kind-specific content: document title/body, presentation slides, spreadsheet
columns/rows, or brief summary/risks/next steps. A prompt alone is rejected. The only scaffold path
is an explicit `offline_scaffold=true` tool argument.

## Safety Notes

Prompts and generated drafts pass through artifact data protection before rendering. Drafts are
structurally validated, spreadsheet cells are formula-neutralized where required, and factual
accuracy still requires user review. The renderer itself does not autonomously query governed data
or external sources.
