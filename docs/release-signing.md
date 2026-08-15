# Release Signing And Verification

Loro releases use two complementary trust paths:

- the annotated Git tag is SSH-signed by `alex@alexmerced.dev` using the public key in
  `docs/keys/release-signing.pub`;
- release files built by GitHub Actions receive GitHub/Sigstore artifact attestations and are
  listed with SHA-256 digests in the release manifest and `SHA256SUMS`.

The release-signing key fingerprint is
`SHA256:7c3tjmy0hUcD41qCPsfDHFD9SJrWQhOtGyDzp1AnJA0`. Confirm this fingerprint through an
independent project-owner channel before establishing trust for the first time.

Verify a checked-out release tag:

```bash
git config gpg.ssh.allowedSignersFile docs/keys/allowed_signers
git verify-tag v0.13.0
git show --no-patch --format=fuller v0.13.0
```

Then verify downloaded files and the CI identity:

```bash
sha256sum --check SHA256SUMS
gh attestation verify loro_agent-0.13.0-py3-none-any.whl --repo alexmerced-oss/loro
gh attestation verify loro_agent-0.13.0.tar.gz --repo alexmerced-oss/loro
```

The public key committed in the same repository is a reproducible verification input, not an
independent root of trust. The fingerprint check and GitHub workload-identity attestation provide
separate evidence. A signing-key rotation requires a release note, an updated allowed-signers
file, independent fingerprint publication, and a transition release signed by the prior key when
it remains available.
