# Security And Supply Chain

Loro's repository security gates are implemented in `.github/workflows/security.yml` and use
pinned versions of `pip-audit`, Bandit, detect-secrets, CycloneDX, and pip-licenses. Dependency
audits cover installed third-party packages while excluding Loro's editable checkout, preventing
unreleased versions from creating a circular PyPI requirement. The workflow still fails for known
dependency vulnerabilities, new Bandit findings, secret-baseline drift,
prohibited AGPL dependencies, overall coverage regression, or a security-critical module below
its individual floor.

`.bandit-baseline.json` records reviewed findings where validated table/schema identifiers are
interpolated while all record values remain bound parameters. `.secrets.baseline` retains every
existing scanner candidate with an unresolved verdict; it must be adjudicated line by line by a
release owner and security reviewer. Do not bulk-mark findings as false positives. Any accepted
secret exception needs a reason, owner, review date, and expiry in the release evidence record.

The release-evidence workflow builds wheels and source archives, writes `SHA256SUMS`, and creates
a GitHub artifact attestation using a short-lived CI identity. Protected branch/tag controls,
trusted publishing, reviewer assignment, vulnerability SLA, and exception approval are
repository-administration controls and must be configured by the project owners.

Suggested triage targets:

| Severity | Initial response | Closure |
| --- | --- | --- |
| Critical | Same business day | Fix or executive/security risk acceptance before release |
| High | One business day | Fix or security risk acceptance before release |
| Medium | Five business days | Fix, schedule, or documented owner/date |
| Low | Ten business days | Triage and backlog with owner |

Report suspected vulnerabilities privately through GitHub's security-advisory interface. Do
not include credentials, customer data, or exploit details in public issues.
