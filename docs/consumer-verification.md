# Consumer Release Verification

Download release artifacts from the GitHub release or an approved mirror. Verify checksums before
installation:

```bash
sha256sum -c SHA256SUMS
python -m pip install ./loro_agent-0.9.0-py3-none-any.whl
loro --version
loro providers conformance
loro operations benchmark --strict --output loro-benchmark.json
loro operations release-readiness
```

Compare `release-manifest.json` commit and artifact hashes to the immutable tag. Inspect the
CycloneDX SBOM and support, data, interoperability, reference-deployment, and release-contract
files. Verify GitHub artifact attestations using the repository and tag as the trusted source;
do not treat a checksum hosted beside a compromised artifact as an independent trust root.

The readiness JSON contains no prompt, response, memory, tool output, hostname, username, or
credential values. Retain it only under the organization's approved evidence policy because its
configuration and external-gate statuses may still be operational metadata.
