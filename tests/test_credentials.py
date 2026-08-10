from __future__ import annotations

import os
from pathlib import Path

import pytest

from loro.config import CredentialsConfig, ModelConfig
from loro.credentials import CredentialError, CredentialReference, CredentialVault
from loro.models import ModelMessage, OpenAICompatibleClient


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


def test_vault_stores_secrets_only_in_keyring_and_indexes_metadata(tmp_path: Path) -> None:
    backend = MemoryKeyring()
    index = tmp_path / "credentials.json"
    vault = CredentialVault(CredentialsConfig(index_path=str(index)), backend=backend)
    ref = "vault://provider/openai/work-api-key"
    vault.set(ref, "credential-value")
    assert vault.get(ref) == "credential-value"
    assert "credential-value" not in index.read_text(encoding="utf-8")
    assert index.stat().st_mode & 0o777 == 0o600
    assert [entry.ref for entry in vault.list()] == [ref]
    vault.delete(ref)
    assert vault.list() == []


def test_vault_supports_multiple_named_accounts_for_one_provider(tmp_path: Path) -> None:
    backend = MemoryKeyring()
    vault = CredentialVault(
        CredentialsConfig(index_path=str(tmp_path / "credentials.json")), backend=backend
    )
    vault.set("vault://provider/openai/work", "work-value")
    vault.set("vault://provider/openai/personal", "personal-value")

    assert vault.get("vault://provider/openai/work") == "work-value"
    assert vault.get("vault://provider/openai/personal") == "personal-value"
    assert len(vault.list()) == 2


def test_credential_reference_is_strict() -> None:
    assert CredentialReference.parse("vault://provider/openai/work").profile == "openai"
    with pytest.raises(CredentialError):
        CredentialReference.parse("vault://provider/../secret")


def test_provider_environment_value_overrides_vault(monkeypatch) -> None:
    class FakeVault:
        def get(self, _ref: str) -> str:
            return "vault-value"

    monkeypatch.setattr("loro.models.CredentialVault", FakeVault)
    config = ModelConfig(
        provider="openai",
        model="test",
        api_key_env="TEST_PROVIDER_KEY",  # pragma: allowlist secret
        credential_ref="vault://provider/openai/work",
    )
    client = OpenAICompatibleClient(config)
    request = client.build_request([ModelMessage(role="user", content="hello")])
    assert request.headers["Authorization"] == "Bearer vault-value"
    monkeypatch.setenv("TEST_PROVIDER_KEY", "environment-value")
    request = client.build_request([ModelMessage(role="user", content="hello")])
    assert request.headers["Authorization"] == "Bearer environment-value"
    assert os.environ["TEST_PROVIDER_KEY"] == "environment-value"
