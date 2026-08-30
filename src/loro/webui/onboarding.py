"""First-run readiness for the local Web UI.

`loro get-started` reads a folder and says what is missing. The browser assumed
everything was already configured: open it on a fresh project and the first
message simply failed, with no hint that a provider was never chosen.

This exposes the same readiness signal, and lets the browser pick a provider
and model. A supplied credential is stored only in Loro's OS-keyring-backed
vault; secret values are never written to project config or returned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loro.config import load_config

# A first run should be possible without any credential at all.
OFFLINE_PROVIDER = "mock"


class OnboardingService:
    """What this folder still needs, and the smallest way to supply it."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def readiness(self) -> dict[str, Any]:
        """The same question `loro get-started` answers: is this folder usable?"""
        config = load_config(self.project_root)
        local_config = (self.project_root / ".loro" / "config.local.toml").is_file()
        provider = config.model.provider
        offline = provider == OFFLINE_PROVIDER

        steps: list[dict[str, Any]] = [
            {
                "id": "config",
                "label": "Project configuration",
                "ok": local_config,
                "detail": str(self.project_root / ".loro" / "config.local.toml")
                if local_config
                else "No project config yet.",
                "action": "Choose a provider below, or run `loro configure`.",
            },
            {
                # The mock provider is a usable answer, not a missing one: a
                # first run should work with no credential at all. It is
                # reported as offline so the UI can still suggest a real one.
                "id": "model",
                "label": "Model",
                "ok": True,
                "offline": offline,
                "detail": f"{provider}/{config.model.model}",
                "action": (
                    "Ready for model work."
                    if not offline
                    else (
                        "The mock provider answers without a key. "
                        "Pick a real one when you need real work."
                    )
                ),
            },
            {
                "id": "credential",
                "label": "Credential",
                "ok": offline or self._has_credential(config),
                "detail": self._credential_detail(config),
                "action": (
                    "Export the provider's key in the shell that runs `loro web`, "
                    "or store it with `loro credentials set`."
                ),
            },
            {
                "id": "profile",
                "label": "Default agent profile",
                "ok": bool(config.agent_profiles.default_profile),
                "detail": config.agent_profiles.default_profile or "none",
                "action": "Optional. Create one in Profiles to give bots a role.",
            },
        ]

        # Only the first three decide whether a first message can succeed; a
        # default profile is a nicety, not a gate.
        blocking = [step for step in steps if step["id"] != "profile" and not step["ok"]]

        return {
            "ok": True,
            "workspace": str(self.project_root),
            "ready": not blocking,
            "offline": offline,
            "steps": steps,
            "blocking": [step["id"] for step in blocking],
            "findings": self._findings(config),
        }

    def _has_credential(self, config: Any) -> bool:
        import os

        env_var = getattr(config.model, "api_key_env", "") or ""
        if env_var and os.environ.get(env_var):
            return True
        return bool(getattr(config.model, "credential_ref", ""))

    def _credential_detail(self, config: Any) -> str:
        env_var = getattr(config.model, "api_key_env", "") or ""
        if config.model.provider == OFFLINE_PROVIDER:
            return "Not needed for the mock provider."
        if self._has_credential(config):
            return f"Found via {env_var or 'the credential vault'}."
        return f"Not set. Expected in {env_var or 'the provider environment variable'}."

    def _findings(self, config: Any) -> list[dict[str, str]]:
        """Configuration warnings worth showing before the first turn."""
        try:
            from loro.config_check import check_config

            return [
                {
                    "level": str(getattr(item, "level", "") or getattr(item, "severity", "")),
                    "message": str(getattr(item, "message", item)),
                }
                for item in check_config(config)
            ][:20]
        except Exception:  # noqa: BLE001 - advisory only
            return []

    def providers(self) -> dict[str, Any]:
        """Providers that can be selected, and where each expects its key."""
        from loro.provider_profiles import PROVIDER_PROFILES

        config = load_config(self.project_root)
        listed = []
        for name, profile in sorted(PROVIDER_PROFILES.items()):
            listed.append(
                {
                    "name": name,
                    "display_name": getattr(profile, "display_name", name),
                    "default_model": getattr(profile, "default_model", ""),
                    "small_model": getattr(profile, "small_model", ""),
                    "api_key_env": getattr(profile, "api_key_env", ""),
                    "needs_key": name != OFFLINE_PROVIDER,
                    "configured": name == config.model.provider,
                    "credential_ready": (
                        name == OFFLINE_PROVIDER
                        or (name == config.model.provider and self._has_credential(config))
                    ),
                }
            )
        return {
            "ok": True,
            "providers": listed,
            "offline_provider": OFFLINE_PROVIDER,
            "default_provider": config.model.provider,
            "default_model": config.model.model,
            "default_small_model": config.model.small_model,
        }

    def configure(
        self,
        provider: str,
        model: str = "",
        small_model: str = "",
        credential: str = "",
    ) -> dict[str, Any]:
        """Write the provider and model choice into the project config.

        The route is written to project configuration. A supplied key is stored
        in the configured OS-keyring-backed vault and only its reference is
        persisted; an omitted key preserves the existing reference.
        """
        from loro.provider_profiles import PROVIDER_PROFILES

        provider = (provider or "").strip()
        if provider not in PROVIDER_PROFILES:
            raise ValueError(f"Unknown provider: {provider or '(empty)'}")

        profile = PROVIDER_PROFILES[provider]
        chosen = (model or "").strip() or getattr(profile, "default_model", "")
        chosen_small = (small_model or "").strip() or getattr(profile, "small_model", "") or chosen

        from loro.providers import model_config_from_profile, write_local_model_config

        config = load_config(self.project_root)
        credential_ref = config.model.credential_ref if config.model.provider == provider else None
        secret = credential.strip()
        if secret:
            from loro.credentials import CredentialVault

            credential_ref = f"vault://provider/{provider}/webui"
            CredentialVault(config.credentials).set(credential_ref, secret)
        config.model = model_config_from_profile(
            provider,
            model=chosen,
            small_model=chosen_small,
            api_key_env=getattr(profile, "api_key_env", None),
            credential_ref=credential_ref,
            base_url=getattr(profile, "base_url", None),
        )
        write_local_model_config(self.project_root / ".loro" / "config.local.toml", config)
        result = self.readiness()
        result["credential_storage"] = "keyring" if secret else "unchanged"
        return result
