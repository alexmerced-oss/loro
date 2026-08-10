from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import keyring
from keyring.errors import KeyringError

from loro.config import CredentialsConfig

_PART = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class CredentialError(RuntimeError):
    """Raised when a secure credential operation cannot be completed."""


class KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(frozen=True)
class CredentialReference:
    namespace: str
    profile: str
    key: str

    @classmethod
    def parse(cls, value: str) -> CredentialReference:
        prefix = "vault://"
        parts = value.removeprefix(prefix).split("/") if value.startswith(prefix) else []
        if len(parts) != 3 or any(_PART.fullmatch(part) is None for part in parts):
            raise CredentialError(
                "credential references must use vault://namespace/profile/key with safe names"
            )
        return cls(*parts)

    @property
    def uri(self) -> str:
        return f"vault://{self.namespace}/{self.profile}/{self.key}"

    @property
    def service(self) -> str:
        return f"loro/{self.namespace}/{self.profile}"


@dataclass(frozen=True)
class CredentialMetadata:
    ref: str
    created_at: str
    updated_at: str


class CredentialVault:
    def __init__(
        self,
        config: CredentialsConfig | None = None,
        *,
        backend: KeyringBackend | None = None,
    ) -> None:
        self.config = config or CredentialsConfig()
        self.backend = backend or keyring
        self.index_path = Path(self.config.index_path).expanduser()

    def set(self, ref: str, value: str) -> None:
        reference = CredentialReference.parse(ref)
        if not value:
            raise CredentialError("credential value cannot be empty")
        self._require_secure_backend()
        try:
            self.backend.set_password(reference.service, reference.key, value)
        except KeyringError as error:
            raise CredentialError(str(error)) from error
        entries = self._read_index()
        now = datetime.now(UTC).isoformat()
        previous = entries.get(reference.uri)
        entries[reference.uri] = CredentialMetadata(
            ref=reference.uri,
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        self._write_index(entries)

    def get(self, ref: str) -> str | None:
        reference = CredentialReference.parse(ref)
        self._require_secure_backend()
        try:
            return self.backend.get_password(reference.service, reference.key)
        except KeyringError as error:
            raise CredentialError(str(error)) from error

    def delete(self, ref: str) -> None:
        reference = CredentialReference.parse(ref)
        self._require_secure_backend()
        try:
            self.backend.delete_password(reference.service, reference.key)
        except KeyringError as error:
            raise CredentialError(str(error)) from error
        entries = self._read_index()
        entries.pop(reference.uri, None)
        self._write_index(entries)

    def list(self) -> list[CredentialMetadata]:
        entries = self._read_index()
        return [entries[ref] for ref in sorted(entries)]

    def diagnose(self) -> dict[str, object]:
        try:
            self._require_secure_backend()
        except CredentialError as error:
            return {"ok": False, "backend": None, "message": str(error)}
        active = keyring.get_keyring()
        return {
            "ok": True,
            "backend": f"{type(active).__module__}.{type(active).__name__}",
            "entries": len(self._read_index()),
        }

    def _require_secure_backend(self) -> None:
        if self.backend is not keyring:
            return
        active = keyring.get_keyring()
        try:
            priority = active.priority
        except Exception as error:  # noqa: BLE001 - backend implementations vary
            raise CredentialError(f"system keyring is unavailable: {error}") from error
        if priority <= 0 or active.__class__.__module__.startswith("keyring.backends.fail"):
            raise CredentialError(
                "no secure system keyring is available; configure Secret Service, "
                "macOS Keychain, or Windows Credential Locker"
            )

    def _read_index(self) -> dict[str, CredentialMetadata]:
        if not self.index_path.exists():
            return {}
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            return {key: CredentialMetadata(**value) for key, value in raw.items()}
        except (OSError, ValueError, TypeError) as error:
            raise CredentialError(f"credential index is invalid: {error}") from error

    def _write_index(self, entries: dict[str, CredentialMetadata]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({key: asdict(value) for key, value in entries.items()}, indent=2),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.index_path)
