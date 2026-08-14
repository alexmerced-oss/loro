from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

from loro.config import LoroConfig

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "deploy" / "reference"


def main() -> int:
    manifest = json.loads((DEPLOYMENT / "manifest.json").read_text(encoding="utf-8"))
    managed = tomllib.loads((DEPLOYMENT / "managed.toml").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data = json.loads((ROOT / "docs" / "data-support-matrix.json").read_text(encoding="utf-8"))
    support = json.loads((ROOT / "docs" / "support-matrix.json").read_text(encoding="utf-8"))
    interoperability = json.loads(
        (ROOT / "docs" / "interoperability-matrix.json").read_text(encoding="utf-8")
    )

    issues: list[str] = []
    release_line = ".".join(project["project"]["version"].split(".")[:2])
    for name, value in (
        ("manifest", manifest.get("release_line")),
        ("support matrix", support.get("release_line")),
        ("data matrix", data.get("release_line")),
        ("interoperability matrix", interoperability.get("release_line")),
    ):
        if value != release_line:
            issues.append(f"{name} release line is {value!r}, expected {release_line!r}")

    for name, version in manifest.get("components", {}).items():
        if data.get("reference_stack", {}).get(name) != version:
            issues.append(f"manifest component {name}={version!r} disagrees with data matrix")

    try:
        config = LoroConfig.model_validate(managed)
    except Exception as error:  # noqa: BLE001 - render Pydantic's complete validation result
        issues.append(f"managed.toml is invalid: {error}")
    else:
        if config.memory.shared.write_policy != "explicit_user_dictation_only":
            issues.append("managed shared-memory write policy is not explicit-user-only")
        if config.audit.failure_mode != "fail" or config.audit.include_prompt_preview:
            issues.append("managed audit policy does not fail closed without prompt previews")
        selected_profiles = {
            config.sandbox.shell_profile,
            config.sandbox.git_profile,
            config.sandbox.governed_data_profile,
            config.sandbox.mcp_stdio_profile,
            config.sandbox.skill_profile,
        }
        for profile_name in sorted(selected_profiles):
            profile = config.sandbox.profiles[profile_name]
            if profile.backend != "bubblewrap" or not profile.require_os_enforcement:
                issues.append(f"managed sandbox profile {profile_name!r} is not OS-enforced")
            if "*" in profile.allowed_executables:
                issues.append(f"managed sandbox profile {profile_name!r} allows any executable")
        if config.approvals.allow_non_interactive:
            issues.append("managed approvals permit non-interactive authorization")

    rendered = (DEPLOYMENT / "managed.toml").read_text(encoding="utf-8").casefold()
    forbidden = ("password =", "token =", "api_key =", "secret =")
    issues.extend(
        f"managed.toml contains forbidden literal {item!r}"
        for item in forbidden
        if item in rendered
    )

    if issues:
        print("Reference deployment is invalid:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("Reference deployment OK: versions, managed policy, and safety invariants agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
