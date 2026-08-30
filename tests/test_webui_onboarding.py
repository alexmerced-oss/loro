"""First-run readiness for the Web UI.

The browser assumed a provider was already configured: open it on a fresh
folder and the first message failed with no hint why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loro.webui.onboarding import OFFLINE_PROVIDER, OnboardingService


def test_a_fresh_folder_is_not_ready(tmp_path: Path) -> None:
    readiness = OnboardingService(tmp_path).readiness()

    assert readiness["ready"] is False
    assert "config" in readiness["blocking"]


def test_readiness_names_every_step(tmp_path: Path) -> None:
    steps = {step["id"] for step in OnboardingService(tmp_path).readiness()["steps"]}
    assert steps == {"config", "model", "credential", "profile"}


def test_the_offline_provider_makes_a_folder_usable(tmp_path: Path) -> None:
    """A first run must be possible with no credential at all."""
    service = OnboardingService(tmp_path)
    readiness = service.configure(OFFLINE_PROVIDER)

    assert readiness["ready"] is True
    assert readiness["offline"] is True
    assert readiness["blocking"] == []


def test_a_real_provider_without_a_key_is_blocked_on_the_credential(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("NOUS_API_KEY", raising=False)
    readiness = OnboardingService(tmp_path).configure("nous")

    assert readiness["ready"] is False
    assert readiness["blocking"] == ["credential"]
    credential = next(s for s in readiness["steps"] if s["id"] == "credential")
    # The step must name the variable, or the user has to go hunting.
    assert "NOUS_API_KEY" in credential["detail"]


def test_a_key_in_the_environment_clears_the_block(tmp_path: Path, monkeypatch) -> None:
    service = OnboardingService(tmp_path)
    service.configure("nous")
    monkeypatch.setenv("NOUS_API_KEY", "not-a-real-key")

    readiness = service.readiness()
    assert readiness["ready"] is True
    assert readiness["blocking"] == []


def test_a_missing_default_profile_never_blocks(tmp_path: Path) -> None:
    """A default profile is a nicety, not a gate on the first message."""
    readiness = OnboardingService(tmp_path).configure(OFFLINE_PROVIDER)

    profile = next(step for step in readiness["steps"] if step["id"] == "profile")
    assert profile["ok"] is False
    assert "profile" not in readiness["blocking"]


def test_providers_are_listed_with_their_key_variable(tmp_path: Path) -> None:
    listed = OnboardingService(tmp_path).providers()

    assert listed["offline_provider"] == OFFLINE_PROVIDER
    by_name = {item["name"]: item for item in listed["providers"]}
    assert by_name[OFFLINE_PROVIDER]["needs_key"] is False
    assert by_name["nous"]["needs_key"] is True
    assert by_name["nous"]["api_key_env"] == "NOUS_API_KEY"


def test_configure_writes_only_the_route(tmp_path: Path) -> None:
    """Without a submitted key, configuration contains only route metadata."""
    OnboardingService(tmp_path).configure("nous", model="deepseek/deepseek-v4-flash-0731")
    written = (tmp_path / ".loro" / "config.local.toml").read_text(encoding="utf-8")

    assert "nous" in written
    assert "deepseek/deepseek-v4-flash-0731" in written
    # The env var name may be recorded; a secret value never is.
    assert "api_key =" not in written


def test_configure_stores_submitted_key_in_vault_and_only_persists_reference(
    tmp_path: Path, monkeypatch
) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr(
        "loro.credentials.CredentialVault.set",
        lambda _self, ref, value: stored.update(ref=ref, value=value),
    )
    secret = "test-provider-secret"
    result = OnboardingService(tmp_path).configure("nous", credential=secret)
    written = (tmp_path / ".loro" / "config.local.toml").read_text(encoding="utf-8")

    assert stored == {"ref": "vault://provider/nous/webui", "value": secret}
    assert result["credential_storage"] == "keyring"
    assert "vault://provider/nous/webui" in written
    assert secret not in written


def test_configure_falls_back_to_the_provider_default_model(tmp_path: Path) -> None:
    readiness = OnboardingService(tmp_path).configure("nous")
    model = next(step for step in readiness["steps"] if step["id"] == "model")
    assert "hermes" in model["detail"]


@pytest.mark.parametrize("bad", ["", "   ", "nonesuch"])
def test_an_unknown_provider_is_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError, match="Unknown provider"):
        OnboardingService(tmp_path).configure(bad)
