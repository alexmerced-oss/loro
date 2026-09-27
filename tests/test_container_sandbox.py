"""Container sandbox backend: command construction, policy, diagnosis, and a real Docker run."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from loro.config import ContainerSandboxConfig, SandboxConfig, SandboxProfileConfig
from loro.sandbox import SandboxError, SandboxRunner


def _config(tmp_path: Path, **profile) -> SandboxConfig:
    settings = {
        "backend": "container",
        "network": "deny",
        "allowed_executables": ["sh", "cat", "echo", "sleep", "touch", "wget", "yes"],
        "container": ContainerSandboxConfig(image="alpine:latest"),
        **profile,
    }
    return SandboxConfig(profiles={"controlled-shell": SandboxProfileConfig(**settings)})


def test_container_profile_requires_an_image() -> None:
    with pytest.raises(ValidationError, match="container.image"):
        SandboxProfileConfig(backend="container")


def test_launch_isolates_the_container(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("loro.sandbox.shutil.which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr("loro.sandbox.container_runtimes", lambda engine: {"runc"})
    (tmp_path / "out").mkdir()
    config = _config(tmp_path, writable_roots=[str(tmp_path / "out")])
    runner = SandboxRunner(
        config, workspace_roots=[str(tmp_path)], environ={"PATH": "/host/bin", "LANG": "C"}
    )
    launch = runner.prepare(["cat", "notes.txt"], profile_name="controlled-shell", cwd=tmp_path)
    args = launch.args
    assert args[:2] == ["/usr/bin/docker", "run"]
    for flag in ("--rm", "--init", "--read-only", "--security-opt", "--pids-limit", "--memory"):
        assert flag in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--network") + 1] == "none"
    assert f"{tmp_path}:{tmp_path}:ro" in args  # workspace read-only by default
    assert f"{tmp_path / 'out'}:{tmp_path / 'out'}:rw" in args
    assert "LANG" in args and not any("=C" in item or item.startswith("PATH") for item in args)
    assert launch.environment["LANG"] == "C"
    assert args[-3:] == ["alpine:latest", "cat", "notes.txt"]
    assert "--runtime" not in args
    assert launch.os_enforced is True
    assert launch.cleanup is not None and launch.cleanup[1:3] == ["rm", "--force"]


def test_gvisor_is_used_when_registered_and_required_when_asked(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("loro.sandbox.shutil.which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr("loro.sandbox.container_runtimes", lambda engine: {"runc", "runsc"})
    runner = SandboxRunner(_config(tmp_path))
    args = runner.prepare(["echo", "hi"], profile_name="controlled-shell", cwd=tmp_path).args
    assert args[args.index("--runtime") + 1] == "runsc"

    monkeypatch.setattr("loro.sandbox.container_runtimes", lambda engine: {"runc"})
    required = _config(tmp_path, container=ContainerSandboxConfig(image="alpine", runtime="runsc"))
    with pytest.raises(SandboxError, match="gVisor"):
        SandboxRunner(required).prepare(["echo"], profile_name="controlled-shell", cwd=tmp_path)


def test_executable_allowlist_and_missing_engine(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("loro.sandbox.shutil.which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr("loro.sandbox.container_runtimes", lambda engine: set())
    runner = SandboxRunner(_config(tmp_path))
    with pytest.raises(SandboxError, match="not allowed"):
        runner.prepare(["curl", "x"], profile_name="controlled-shell", cwd=tmp_path)
    with pytest.raises(SandboxError, match="not allowed"):
        runner.prepare(["/workspace/evil", "x"], profile_name="controlled-shell", cwd=tmp_path)
    monkeypatch.setattr("loro.sandbox.shutil.which", lambda name, path=None: None)
    with pytest.raises(SandboxError, match="not installed"):
        runner.prepare(["echo"], profile_name="controlled-shell", cwd=tmp_path)


def test_diagnosis_is_honest_about_the_boundary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "loro.sandbox.container_diagnosis",
        lambda profile: {
            "engine": "docker",
            "engine_available": True,
            "engine_operational": True,
            "engine_rootless": False,
            "image": "alpine:latest",
            "image_present": True,
            "runtime": "default",
            "gvisor": False,
        },
    )
    report = SandboxRunner(_config(tmp_path)).diagnose()["profiles"]["controlled-shell"]
    assert report["backend"] == "container" and report["ready"] is True
    assert report["network_isolated"] is True and report["filesystem_os_enforced"] is True
    notes = " ".join(report["notes"])
    assert "host kernel" in notes and "runs as root" in notes


def _docker_ready() -> bool:
    if os.environ.get("LORO_INTEGRATION_DOCKER") != "1" or shutil.which("docker") is None:
        return False
    return (
        subprocess.run(
            ["docker", "image", "inspect", "alpine:latest"], capture_output=True, check=False
        ).returncode
        == 0
    )


@pytest.mark.integration
@pytest.mark.skipif(not _docker_ready(), reason="Set LORO_INTEGRATION_DOCKER=1 with alpine:latest")
def test_real_container_enforces_the_profile(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello from the workspace\n")
    (tmp_path / "out").mkdir()
    runner = SandboxRunner(
        _config(tmp_path, writable_roots=[str(tmp_path / "out")], max_seconds=90),
        workspace_roots=[str(tmp_path)],
    )

    def run(*args: str, timeout: int = 60):
        return runner.run(
            list(args), profile_name="controlled-shell", cwd=tmp_path, timeout=timeout
        )

    read = run("cat", "notes.txt")
    assert read.returncode == 0 and "hello from the workspace" in read.stdout
    assert read.os_enforced is True
    assert run("touch", "blocked.txt").returncode != 0  # workspace is read-only
    assert run("touch", "/etc/blocked").returncode != 0  # root filesystem is read-only
    assert run("touch", str(tmp_path / "out" / "ok.txt")).returncode == 0
    assert (tmp_path / "out" / "ok.txt").exists()
    network = run("wget", "-q", "-T", "3", "-O", "-", "http://1.1.1.1/")
    assert network.returncode != 0  # no network
    with pytest.raises(SandboxError, match="exceeded"):
        run("sleep", "60", timeout=5)
    leftover = subprocess.run(
        ["docker", "ps", "-q", "--filter", "name=loro-sandbox-"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert leftover.stdout.strip() == ""  # the timed-out container was removed
