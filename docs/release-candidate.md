# Release Candidate Operations

Loro `0.9` freezes the candidate contract in `release-contract.json`. After the release commit,
only release-blocking correctness or security fixes, dependency/security updates, documentation
corrections, and evidence work are permitted. Any CLI, schema, protocol, matrix, or deployment
change requires explicit review and regeneration of the contract.

## Candidate Acceptance

1. Verify the public wheel, source distribution, checksums, SBOM, provenance, release manifest,
   support matrices, release contract, benchmark, and readiness report.
2. Install from the approved mirror into a clean environment and run the documented smoke suite.
3. Run the controlled pilot charter for the approved cohort, duration, repositories, data,
   provider routes, and stop conditions.
4. Classify every defect. Severity 1 and 2 findings block promotion unless resolved and retested;
   security risk cannot be silently relabeled as a product limitation.
5. Complete the independent assurance and operational exercises, recording controlled references
   rather than sensitive artifacts in the repository.
6. Obtain named product, engineering, security, privacy, legal, data, operations, support, and
   release disposition of every external gate.

## Severity And Disposition

| Severity | Examples | Candidate treatment |
| --- | --- | --- |
| 1 Critical | Cross-tenant access, credential exposure, remote code execution, silent audit loss, unrecoverable data corruption. | Stop pilot; fix and independently retest before any promotion. |
| 2 High | Policy bypass, approval replay, material data-loss window, unsupported provider fallback, reliable service denial. | Block promotion; fix or formally reject the candidate. |
| 3 Medium | Bounded workflow failure with recovery, misleading diagnostics, supported compatibility defect. | Fix or document owner/date/workaround before GA decision. |
| 4 Low | Cosmetic or low-impact documentation/ergonomic defect. | Track under normal maintenance policy. |

Disposition records must name the affected version/commit, environment, severity rationale,
owner, remediation, retest evidence, accepted risk approver, expiry, and support-matrix impact.
