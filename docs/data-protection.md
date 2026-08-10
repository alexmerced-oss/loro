# Managed Data Protection

Loro applies one classification and scanning decision contract at its model, memory, artifact,
session, session-message, tool-output, and audit boundaries. This is an application control for
the supported runtime and CLI paths; it is not a substitute for provider-side DLP, storage
encryption, tenant authorization, or an enterprise data-owner review.

## Decision Contract

`DataProtectionEngine` returns the content surface, detected classification, configured ceiling,
findings, action, transformed content, and policy reason. Classifications follow
[Enterprise Data Classification](data-classification.md): Public, Internal, Confidential, and
Restricted. Unlabelled content defaults to Internal. A marker such as
`[classification: confidential]` can raise the detected class; scanner findings can also raise it.

Each surface chooses `allow`, `redact`, or `block` when a finding is present or content exceeds
its ceiling. Persistence and model input block by default. Model output, tool output, and audit
details redact by default. Unknown surfaces fail closed.

The built-in scanner recognizes complete private-key blocks (header through footer, so
redaction removes the key material), credential assignments including `PREFIX_API_KEY=` and
`AWS_SECRET_ACCESS_KEY=` forms, GitHub tokens, AWS access-key IDs, and bare 40-character AWS
secret access keys that pass a shape and entropy check. Finding previews never echo any
characters of the matched value: they report the redaction text and the match length. Managed configuration may add regular-expression patterns and allow a named
finding kind on a specific surface. Allowlisting does not disable other findings. Code
integrations can inject implementations of the `ContentScanner` protocol without changing policy
evaluation.

## Enforced Flows

- The complete composed model request is checked immediately before provider dispatch, including
  recalled memory, resumed sessions, cross-session messages, skills, and initial tool output.
- Model text and every nested native tool-argument value are transformed before parsing,
  execution, reuse, session persistence, or display. Provider wire metadata needed for protocol
  round trips is recursively protected before it is retained.
- Local memory writes, shared-memory proposal acceptance/commit, artifact prompts and provenance,
  session records, and cross-session message writes use persistence policies.
- Audit details are recursively evaluated. Nested sensitive strings are redacted and the event
  records affected field paths, classifications, and the managed redaction method.

Low-level artifact generators and storage adapters can still be used as libraries without a
`SafetyConfig`; callers embedding those primitives must supply an equivalent boundary control.
The supported Loro CLI, runtime, MCP server audit path, and tool registry provide it.

## Operations

Inspect effective policy with:

```bash
loro safety doctor
loro safety scan --surface model_input "api_key = 'abc123456789'"
```

`--allow-sensitive` is a development compatibility option. An enterprise overlay should set
`safety.allow_sensitive_override = false`, which makes that option incapable of bypassing a
managed block. Managed overlays are applied after all user and project configuration.

## Remaining Enterprise Proof

Before Confidential data is enabled, integrate the adopting organization's scanner or gateway,
approve provider-specific classification ceilings, test the production policy bundle, and record
false-positive/false-negative handling. Restricted workflows remain denied by default and require
a separately reviewed design. Storage lifecycle, tenant isolation, encryption, and external DLP
evidence are separate roadmap gates.
