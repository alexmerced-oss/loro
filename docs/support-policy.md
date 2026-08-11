# Beta Support Policy

Loro `0.8` is a best-effort open-source enterprise beta. GitHub Issues is the public defect and
feature intake channel. Do not include secrets, prompts, model responses, customer data, memory
content, private infrastructure details, or unredacted audit records in public reports.

Security vulnerabilities must follow `SECURITY.md`, not public issue disclosure. Each adopting
organization owns its production support hours, severity/response targets, on-call coverage,
provider/database/catalog/chat applications, identity, backups, retention, and incident process.

Patch releases may fix correctness, security, compatibility, packaging, or documentation defects
without expanding the stable boundary. The CLI, configuration schema, storage records, provider
contracts, protocol revisions, and support matrix remain pre-1.0 interfaces and may change with
documented migration guidance. Only cells marked `supported` are in the beta support boundary;
experimental integrations require organization-owned evidence and may change more quickly.
