import os
import sys
from pathlib import Path

import pytest

from loro.config import SandboxConfig, SandboxProfileConfig
from loro.sandbox import SandboxError, SandboxRunner


def sandbox_config(**overrides: object) -> SandboxConfig:
    values = {
        "backend": "process",
        "network": "inherit",
        "allowed_executables": ["python*"],
        "environment_allowlist": ["PATH"],
        "max_seconds": 5,
        "max_output_bytes": 1024,
        **overrides,
    }
    return SandboxConfig(
        shell_profile="test",
        skill_profile="test",
        profiles={"test": SandboxProfileConfig.model_validate(values)},
    )


def test_process_sandbox_does_not_inherit_secret_environment(tmp_path: Path) -> None:
    config = sandbox_config()
    runner = SandboxRunner(
        config,
        workspace_roots=[str(tmp_path)],
        environ={"PATH": os.environ["PATH"], "PROVIDER_API_KEY": "do-not-leak"},
    )

    result = runner.run(
        [sys.executable, "-c", "import os; print(os.getenv('PROVIDER_API_KEY', 'missing'))"],
        profile_name="test",
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "missing"


def test_sandbox_rejects_disallowed_executable(tmp_path: Path) -> None:
    runner = SandboxRunner(sandbox_config(allowed_executables=["git"]), environ=os.environ)

    with pytest.raises(SandboxError, match="not allowed"):
        runner.run([sys.executable, "-V"], profile_name="test", cwd=tmp_path)


def test_sandbox_rejects_cwd_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    runner = SandboxRunner(
        sandbox_config(), workspace_roots=[str(workspace)], environ=os.environ
    )

    with pytest.raises(SandboxError, match="outside configured workspace"):
        runner.run([sys.executable, "-V"], profile_name="test", cwd=outside)


def test_sandbox_terminates_output_over_limit(tmp_path: Path) -> None:
    runner = SandboxRunner(sandbox_config(max_output_bytes=1024), environ=os.environ)

    result = runner.run(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
        profile_name="test",
        cwd=tmp_path,
    )

    assert result.returncode != 0
    assert result.output_truncated is True
    assert len(result.stdout.encode()) < 1200
    assert "process terminated" in result.stdout


def test_sandbox_enforces_profile_timeout_ceiling(tmp_path: Path) -> None:
    runner = SandboxRunner(sandbox_config(max_seconds=1), environ=os.environ)

    with pytest.raises(SandboxError, match="exceeded 1 seconds"):
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            profile_name="test",
            cwd=tmp_path,
            timeout=30,
        )


def test_process_backend_fails_when_os_enforcement_is_required(tmp_path: Path) -> None:
    runner = SandboxRunner(
        sandbox_config(require_os_enforcement=True),
        environ=os.environ,
    )

    with pytest.raises(SandboxError, match="requires OS enforcement"):
        runner.run([sys.executable, "-V"], profile_name="test", cwd=tmp_path)


def test_diagnostics_distinguish_advisory_and_os_enforcement() -> None:
    report = SandboxRunner(sandbox_config(network="deny"), environ=os.environ).diagnose()
    profile = report["profiles"]["test"]

    assert profile["ready"] is True
    assert profile["network_policy_enforced"] is False
    assert profile["network_isolated"] is False
    assert profile["filesystem_os_enforced"] is False


def test_minimal_bubblewrap_profile_binds_only_declared_roots() -> None:
    from loro.sandbox import _bubblewrap_isolation

    profile = SandboxProfileConfig(backend="bubblewrap", readonly_roots=["/usr", "/etc"])

    flags = _bubblewrap_isolation(profile)

    assert "--unshare-pid" in flags
    assert "--unshare-ipc" in flags
    assert "--unshare-uts" in flags
    # The whole host root is no longer bound; only declared roots that exist are.
    pairs = [
        (flags[index + 1], flags[index + 2])
        for index, value in enumerate(flags)
        if value == "--ro-bind"
    ]
    assert ("/", "/") not in pairs
    assert all(source == destination for source, destination in pairs)
    assert set(dict(pairs)) <= {"/usr", "/etc"}
    assert "--tmpfs" in flags


def test_host_readonly_profile_masks_sensitive_paths(tmp_path, monkeypatch) -> None:
    from loro.sandbox import _bubblewrap_isolation, _masked_paths

    secret = tmp_path / ".ssh"
    secret.mkdir()
    profile = SandboxProfileConfig(
        backend="bubblewrap",
        filesystem="host_readonly",
        masked_paths=[str(secret), str(tmp_path / "absent")],
    )

    flags = _bubblewrap_isolation(profile)
    masked = _masked_paths(profile)

    assert flags[flags.index("--ro-bind") + 1 : flags.index("--ro-bind") + 3] == ["/", "/"]
    assert [str(path) for path in masked] == [str(secret)]


def test_minimal_profile_has_nothing_to_mask() -> None:
    from loro.sandbox import _masked_paths

    assert _masked_paths(SandboxProfileConfig(backend="bubblewrap")) == []


def test_name_only_allowlist_rejects_an_agent_writable_executable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    planted = workspace / "git"
    planted.write_text("#!/bin/sh\n", encoding="utf-8")
    planted.chmod(0o755)
    runner = SandboxRunner(
        sandbox_config(allowed_executables=["git"]),
        workspace_roots=[str(workspace)],
    )

    with pytest.raises(SandboxError, match="agent-writable root"):
        runner._require_allowed_executable(planted, runner.config.profiles["test"])


def test_name_only_allowlist_can_require_a_trusted_prefix(tmp_path: Path) -> None:
    elsewhere = tmp_path / "opt" / "bin"
    elsewhere.mkdir(parents=True)
    binary = elsewhere / "git"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    runner = SandboxRunner(sandbox_config(allowed_executables=["git"]))
    profile = runner.config.profiles["test"]

    # No prefixes configured: a binary outside any workspace is accepted.
    runner._require_allowed_executable(binary, profile)

    strict = profile.model_copy(update={"trusted_executable_prefixes": ["/usr/bin"]})
    with pytest.raises(SandboxError, match="trusted executable prefixes"):
        runner._require_allowed_executable(binary, strict)


def test_absolute_path_allowlist_still_matches_exactly(tmp_path: Path) -> None:
    runner = SandboxRunner(sandbox_config(allowed_executables=["/usr/bin/git"]))
    profile = runner.config.profiles["test"]

    runner._require_allowed_executable(Path("/usr/bin/git"), profile)
    with pytest.raises(SandboxError, match="not allowed"):
        runner._require_allowed_executable(Path("/usr/local/bin/git"), profile)
