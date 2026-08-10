# Credential Vault

Loro stores provider, gateway, and integration secrets in the operating system credential service
through Python Keyring. Secure backends include macOS Keychain, Windows Credential Locker, and
Linux Secret Service. Loro does not fall back to plaintext when a secure backend is unavailable.

## Named Accounts

Credential references use `vault://namespace/profile/key`:

```bash
loro credentials set vault://provider/openai/work-api-key
loro credentials set vault://provider/openai/personal-api-key
loro credentials list
```

Select one account in project configuration:

```bash
loro configure --provider openai --model gpt-5.6-luna \
  --credential-ref vault://provider/openai/work-api-key
```

The resulting TOML contains only the reference:

```toml
[model]
provider = "openai"
model = "gpt-5.6-luna"
credential_ref = "vault://provider/openai/work-api-key"
api_key_env = "OPENAI_API_KEY" # pragma: allowlist secret
```

If `OPENAI_API_KEY` is present, it overrides the vault entry. This preserves CI, container, and
managed-launcher workflows while allowing normal use without exported secrets.

Import an existing value without putting it in shell history:

```bash
loro credentials set vault://provider/openai/work-api-key --from-env OPENAI_API_KEY
unset OPENAI_API_KEY
```

Maintenance commands never display values:

```bash
loro credentials doctor
loro credentials list
loro credentials delete vault://provider/openai/personal-api-key
```

Loro keeps a mode-`0600` metadata index containing references and timestamps so entries can be
listed. Values exist only in the active operating-system keyring. See the
[Python Keyring documentation](https://keyring.readthedocs.io/en/latest/) for backend behavior.

Headless Linux services commonly lack an unlocked Secret Service session. Use environment
injection from an enterprise secrets manager or an organization-approved Keyring backend; Loro
fails closed instead of creating an unencrypted file.
