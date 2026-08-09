from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from loro.config import SandboxConfig, SandboxProfileConfig


class SandboxError(RuntimeError):
    """Raised when a process cannot run within its configured sandbox contract."""


@dataclass(frozen=True)
class SandboxResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int
    profile: str
    os_enforced: bool
    output_truncated: bool


@dataclass(frozen=True)
class SandboxLaunch:
    args: list[str]
    cwd: Path
    environment: dict[str, str]
    profile: str
    os_enforced: bool


class SandboxRunner:
    def __init__(
        self,
        config: SandboxConfig,
        *,
        workspace_roots: list[str] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.workspace_roots = [Path(root).expanduser().resolve() for root in workspace_roots or []]
        self.environ = dict(os.environ if environ is None else environ)

    def run(
        self,
        args: list[str],
        *,
        profile_name: str,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> SandboxResult:
        launch = self.prepare(args, profile_name=profile_name, cwd=cwd)
        profile = self._profile(profile_name)
        requested_timeout = profile.max_seconds if timeout is None else max(1, timeout)
        effective_timeout = min(requested_timeout, profile.max_seconds)
        returncode, stdout, stderr, truncated = _run_bounded(
            launch.args,
            cwd=launch.cwd,
            environment=launch.environment,
            timeout=effective_timeout,
            output_limit=profile.max_output_bytes,
            profile_name=profile_name,
        )
        return SandboxResult(
            args=list(args),
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            profile=profile_name,
            os_enforced=launch.os_enforced,
            output_truncated=truncated,
        )

    def prepare(
        self,
        args: list[str],
        *,
        profile_name: str,
        cwd: Path | None = None,
        explicit_environment: Mapping[str, str] | None = None,
    ) -> SandboxLaunch:
        if not args or not all(isinstance(item, str) and "\x00" not in item for item in args):
            raise SandboxError("Sandbox commands require a non-empty NUL-free argument list.")
        profile = self._profile(profile_name)
        resolved_cwd = (cwd or Path.cwd()).expanduser().resolve()
        self._require_workspace(resolved_cwd)
        environment = self._environment(profile)
        if explicit_environment:
            for name, value in explicit_environment.items():
                if "\x00" in name or "=" in name or "\x00" in value:
                    raise SandboxError("Explicit sandbox environment contains an invalid entry.")
                environment[name] = value
        executable = self._resolve_executable(args[0], environment)
        self._require_allowed_executable(executable, profile)
        command, os_enforced = self._command(args, executable, resolved_cwd, profile)
        return SandboxLaunch(
            args=command,
            cwd=resolved_cwd,
            environment=environment,
            profile=profile_name,
            os_enforced=os_enforced,
        )

    def diagnose(self) -> dict[str, object]:
        profiles: dict[str, object] = {}
        for name, profile in self.config.profiles.items():
            backend_available = profile.backend == "process" or shutil.which("bwrap") is not None
            backend_operational = profile.backend == "process" or _bubblewrap_usable(profile)
            network_policy_enforced = profile.network == "inherit" or (
                profile.backend == "bubblewrap" and backend_operational
            )
            profiles[name] = {
                "backend": profile.backend,
                "backend_available": backend_available,
                "backend_operational": backend_operational,
                "require_os_enforcement": profile.require_os_enforcement,
                "network": profile.network,
                "network_policy_enforced": network_policy_enforced,
                "network_isolated": profile.network == "deny"
                and profile.backend == "bubblewrap"
                and backend_operational,
                "filesystem_os_enforced": profile.backend == "bubblewrap" and backend_operational,
                "environment_allowlist": profile.environment_allowlist,
                "max_seconds": profile.max_seconds,
                "max_output_bytes": profile.max_output_bytes,
                "ready": backend_operational
                and (profile.backend == "bubblewrap" or not profile.require_os_enforcement),
            }
        return {"enabled": self.config.enabled, "profiles": profiles}

    def _profile(self, name: str) -> SandboxProfileConfig:
        if not self.config.enabled:
            raise SandboxError("Subprocess execution is disabled because sandboxing is disabled.")
        try:
            return self.config.profiles[name]
        except KeyError as error:
            raise SandboxError(f"Unknown sandbox profile: {name}") from error

    def _require_workspace(self, cwd: Path) -> None:
        if self.workspace_roots and not any(_is_within(cwd, root) for root in self.workspace_roots):
            raise SandboxError(f"Sandbox cwd is outside configured workspace roots: {cwd}")

    def _environment(self, profile: SandboxProfileConfig) -> dict[str, str]:
        environment = {
            name: self.environ[name]
            for name in profile.environment_allowlist
            if name in self.environ
        }
        environment.setdefault("PATH", os.defpath)
        return environment

    def _resolve_executable(self, value: str, environment: Mapping[str, str]) -> Path:
        if "/" in value:
            executable = Path(value).expanduser().resolve()
            if not executable.is_file():
                raise SandboxError(f"Sandbox executable does not exist: {executable}")
            return executable
        resolved = shutil.which(value, path=environment.get("PATH"))
        if resolved is None:
            raise SandboxError(f"Sandbox executable was not found on the allowed PATH: {value}")
        return Path(resolved).resolve()

    def _require_allowed_executable(self, executable: Path, profile: SandboxProfileConfig) -> None:
        candidates = {executable.name, str(executable)}
        if not any(
            fnmatch.fnmatchcase(candidate, pattern)
            for pattern in profile.allowed_executables
            for candidate in candidates
        ):
            raise SandboxError(f"Executable is not allowed by sandbox profile: {executable}")

    def _command(
        self,
        args: list[str],
        executable: Path,
        cwd: Path,
        profile: SandboxProfileConfig,
    ) -> tuple[list[str], bool]:
        normalized = [str(executable), *args[1:]]
        if profile.backend == "process":
            if profile.require_os_enforcement:
                raise SandboxError(
                    "Sandbox profile requires OS enforcement but uses the process backend."
                )
            return normalized, False
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            raise SandboxError("Sandbox profile requires Bubblewrap, but bwrap is unavailable.")
        command = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
        ]
        if profile.network == "deny":
            command.append("--unshare-net")
        for root_value in profile.writable_roots:
            root = Path(root_value).expanduser().resolve()
            if self.workspace_roots and not any(
                _is_within(root, item) for item in self.workspace_roots
            ):
                raise SandboxError(f"Writable sandbox root is outside workspace policy: {root}")
            if not root.exists():
                raise SandboxError(f"Writable sandbox root does not exist: {root}")
            command.extend(["--bind", str(root), str(root)])
        command.extend(["--chdir", str(cwd), "--", *normalized])
        return command, True


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _bubblewrap_usable(profile: SandboxProfileConfig) -> bool:
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return False
    command = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
    ]
    if profile.network == "deny":
        command.append("--unshare-net")
    command.extend(["--", "/bin/true"])
    try:
        # Fixed Bubblewrap capability probe; no user-controlled shell is involved.
        result = subprocess.run(  # nosec B603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _run_bounded(
    command: list[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: int,
    output_limit: int,
    profile_name: str,
) -> tuple[int, str, str, bool]:
    # The sandbox validates executable, cwd, environment, limits, and structured argv first.
    process = subprocess.Popen(  # nosec B603
        command,
        cwd=cwd,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    buffers = [bytearray(), bytearray()]
    state = {"captured": 0, "truncated": False}
    lock = threading.Lock()

    def drain(stream: object, index: int) -> None:
        while True:
            chunk = stream.read(65536)  # type: ignore[attr-defined]
            if not chunk:
                return
            with lock:
                remaining = max(0, output_limit - state["captured"])
                buffers[index].extend(chunk[:remaining])
                state["captured"] += min(len(chunk), remaining)
                if len(chunk) > remaining and not state["truncated"]:
                    state["truncated"] = True
                    process.kill()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, 0), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, 1), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        for thread in threads:
            thread.join()
        raise SandboxError(
            f"Sandbox profile {profile_name!r} exceeded {timeout} seconds."
        ) from error
    for thread in threads:
        thread.join()
    truncated = bool(state["truncated"])
    stdout = bytes(buffers[0]).decode(errors="replace")
    stderr = bytes(buffers[1]).decode(errors="replace")
    if truncated:
        stdout += "\n[output limit exceeded; process terminated]\n"
        if returncode == 0:
            returncode = 125
    return returncode, stdout, stderr, truncated
