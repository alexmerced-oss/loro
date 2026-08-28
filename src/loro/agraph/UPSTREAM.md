# Upstream AGS Materials

Loro 0.17.0 depends on `agentic-graph-spec>=1.0.1,<2` for canonical JSON, graph digests,
specification validation, and the portable run-record schema. CI pins
`AlexMercedCoder/agentic-graph-spec` commit
`f180a4dbd07911f90dd0821f531d7ccd51bb0764`, licensed under MIT. The upstream repository is
<https://github.com/AlexMercedCoder/agentic-graph-spec>.

The JSON Schemas under `schema/` and the schema in the bundled Agentic Graph Skill mirror that
pinned 1.0.1 revision. `reference_validator.py` retains expression-parser and finding types from
the earlier vendored implementation for Loro runtime compatibility; it is no longer the source of
canonical graph validation. The conformance workflow validates the immutable upstream examples
and runs Loro's positive, negative-diagnostic, execution, and run-record tests.
