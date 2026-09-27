from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    # Run when the launch is killed (timeout or output limit): the container backend must stop
    # the container itself, because killing the engine CLI leaves the container running.
    cleanup: list[str] | None = None


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
            cleanup=launch.cleanup,
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
        if profile.backend == "container":
            return self._container_launch(args, resolved_cwd, environment, profile, profile_name)
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

    def diagnose(self) -> dict[str, Any]:
        profiles: dict[str, object] = {}
        for name, profile in self.config.profiles.items():
            if profile.backend == "container":
                profiles[name] = self._diagnose_container(profile)
                continue
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

    @staticmethod
    def _diagnose_container(profile: SandboxProfileConfig) -> dict[str, Any]:
        details = container_diagnosis(profile)
        operational = bool(details["engine_operational"] and details["image_present"])
        return {
            "backend": "container",
            "backend_available": details["engine_available"],
            "backend_operational": operational,
            "require_os_enforcement": profile.require_os_enforcement,
            "network": profile.network,
            "network_policy_enforced": profile.network == "inherit" or operational,
            "network_isolated": profile.network == "deny" and operational,
            "filesystem_os_enforced": operational,
            "environment_allowlist": profile.environment_allowlist,
            "max_seconds": profile.max_seconds,
            "max_output_bytes": profile.max_output_bytes,
            "ready": operational,
            "container": details,
            "notes": [
                note
                for note in (
                    "gVisor (runsc) adds a user-space kernel between the process and the host."
                    if details["gvisor"]
                    else "Runs on the host kernel (runc); install gVisor for a stronger boundary.",
                    "The container engine runs as root; a container escape is a host compromise."
                    if details["engine_rootless"] is False
                    else None,
                    "Image not present locally; pull it before use."
                    if details["engine_operational"] and not details["image_present"]
                    else None,
                )
                if note
            ],
        }

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
        absolute = str(executable)
        path_patterns = [
            pattern for pattern in profile.allowed_executables if "/" in pattern or pattern == "*"
        ]
        name_patterns = [
            pattern
            for pattern in profile.allowed_executables
            if "/" not in pattern and pattern != "*"
        ]
        if any(fnmatch.fnmatchcase(absolute, pattern) for pattern in path_patterns):
            return
        # A basename-only pattern such as "git" matched any file called git, anywhere —
        # including one the agent just wrote into the workspace and put on PATH.
        if any(fnmatch.fnmatchcase(executable.name, pattern) for pattern in name_patterns):
            agent_writable = [
                *self.workspace_roots,
                *(Path(root).expanduser().resolve(strict=False) for root in profile.writable_roots),
            ]
            if any(_is_within(executable, root) for root in agent_writable):
                raise SandboxError(
                    "Executable matches a name-only allowlist entry but resolves inside an "
                    f"agent-writable root: {executable}"
                )
            prefixes = [
                Path(prefix).expanduser().resolve(strict=False)
                for prefix in profile.trusted_executable_prefixes
            ]
            if prefixes and not any(_is_within(executable, prefix) for prefix in prefixes):
                raise SandboxError(
                    "Executable matches a name-only allowlist entry but is outside the profile's "
                    f"trusted executable prefixes: {executable}"
                )
            return
        raise SandboxError(f"Executable is not allowed by sandbox profile: {executable}")

    def _container_launch(
        self,
        args: list[str],
        cwd: Path,
        environment: dict[str, str],
        profile: SandboxProfileConfig,
        profile_name: str,
    ) -> SandboxLaunch:
        """Run ``args`` inside a fresh container; executables come from the image."""

        import uuid

        settings = profile.container
        engine = shutil.which(settings.engine)
        if engine is None:
            raise SandboxError(
                f"Sandbox profile {profile_name!r} uses the container backend, but "
                f"{settings.engine!r} is not installed."
            )
        name = args[0]
        if "/" in name:
            allowed = any(
                fnmatch.fnmatchcase(name, pattern)
                for pattern in profile.allowed_executables
                if "/" in pattern or pattern == "*"
            )
        else:
            allowed = any(
                fnmatch.fnmatchcase(name, pattern) for pattern in profile.allowed_executables
            )
        if not allowed:
            raise SandboxError(f"Executable is not allowed by sandbox profile: {name}")
        runtimes = container_runtimes(settings.engine)
        if settings.runtime == "runsc" and "runsc" not in runtimes:
            raise SandboxError(
                "Sandbox profile requires the gVisor runtime (runsc), but the container engine "
                "has not registered it."
            )
        runtime = "runsc" if settings.runtime != "default" and "runsc" in runtimes else None
        container_name = f"loro-sandbox-{uuid.uuid4().hex[:12]}"
        user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "65534:65534"
        command = [engine, *_container_isolation(profile, runtime, container_name, user)]
        mounts: list[tuple[Path, bool]] = [(cwd, settings.writable_workspace)]
        for root_value in profile.writable_roots:
            root = Path(root_value).expanduser().resolve()
            if self.workspace_roots and not any(
                _is_within(root, item) for item in self.workspace_roots
            ):
                raise SandboxError(f"Writable sandbox root is outside workspace policy: {root}")
            if not root.exists():
                raise SandboxError(f"Writable sandbox root does not exist: {root}")
            mounts.append((root, True))
        for path, writable in mounts:
            # Same path inside and out, so arguments that name workspace files stay valid.
            command.extend(["--volume", f"{path}:{path}:{'rw' if writable else 'ro'}"])
        for variable, value in sorted(environment.items()):
            if variable == "PATH":
                continue  # the image's PATH, not the host's
            command.extend(["--env", f"{variable}={value}"])
        command.extend(["--workdir", str(cwd), settings.image or "", *args])
        return SandboxLaunch(
            args=command,
            cwd=cwd,
            environment={"PATH": environment.get("PATH", os.defpath)},
            profile=profile_name,
            os_enforced=True,
            cleanup=[engine, "rm", "--force", container_name],
        )

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
        command = [bwrap, *_bubblewrap_isolation(profile)]
        if profile.network == "deny":
            command.append("--unshare-net")
        for masked in _masked_paths(profile):
            command.extend(["--tmpfs", str(masked)])
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


def _container_isolation(
    profile: SandboxProfileConfig, runtime: str | None, name: str, user: str
) -> list[str]:
    """Flags for an isolated, resource-limited, read-only container."""

    settings = profile.container
    flags = [
        "run",
        "--rm",
        "--name",
        name,
        "--init",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(settings.pids_limit),
        "--memory",
        f"{settings.memory_mb}m",
        "--memory-swap",
        f"{settings.memory_mb}m",
        "--cpus",
        f"{settings.cpus:g}",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,size={settings.tmp_mb}m",  # nosec B108
        "--user",
        user,
        "--network",
        "none" if profile.network == "deny" else "bridge",
    ]
    if runtime:
        flags.extend(["--runtime", runtime])
    return flags


def container_runtimes(engine: str) -> set[str]:
    """Runtimes the engine has registered (``runsc`` means gVisor is available)."""

    binary = shutil.which(engine)
    if binary is None:
        return set()
    try:
        result = subprocess.run(  # nosec B603 - fixed engine query
            [binary, "info", "--format", "{{json .Runtimes}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    try:
        import json

        return set(json.loads(result.stdout or "{}"))
    except ValueError:
        return set()


def container_diagnosis(profile: SandboxProfileConfig) -> dict[str, Any]:
    """What the container backend would actually enforce on this machine."""

    settings = profile.container
    binary = shutil.which(settings.engine)
    operational = False
    rootless = None
    image_present = False
    if binary is not None:
        try:
            info = subprocess.run(  # nosec B603 - fixed engine query
                [binary, "info", "--format", "{{json .SecurityOptions}}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            operational = info.returncode == 0
            rootless = "rootless" in info.stdout if operational else None
            if operational and settings.image:
                image = subprocess.run(  # nosec B603 - fixed engine query
                    [binary, "image", "inspect", settings.image],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                image_present = image.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            operational = False
    runtimes = container_runtimes(settings.engine) if operational else set()
    gvisor = "runsc" in runtimes and settings.runtime in {"auto", "runsc"}
    return {
        "engine": settings.engine,
        "engine_available": binary is not None,
        "engine_operational": operational,
        "engine_rootless": rootless,
        "image": settings.image,
        "image_present": image_present,
        "runtime": "runsc" if gvisor else "default",
        "gvisor": gvisor,
    }


def _bubblewrap_isolation(profile: SandboxProfileConfig) -> list[str]:
    """Bubblewrap flags shared by the real launch and the capability probe.

    `--ro-bind / /` exposed ~/.ssh, ~/.aws and the Loro credential store to every
    sandboxed process, and `--proc` without `--unshare-pid` exposed other processes'
    /proc/<pid>/environ. The default profile now binds only the system roots it needs and
    unshares the PID, IPC and UTS namespaces.
    """

    flags = [
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
    ]
    if profile.filesystem == "host_readonly":
        flags.extend(["--ro-bind", "/", "/"])
    else:
        for root_value in profile.readonly_roots:
            root = Path(root_value).expanduser()
            if root.exists():
                flags.extend(["--ro-bind", str(root), str(root)])
    flags.extend(["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"])  # nosec B108
    return flags


def _masked_paths(profile: SandboxProfileConfig) -> list[Path]:
    if profile.filesystem != "host_readonly":
        # Nothing outside readonly_roots is bound, so there is nothing to mask.
        return []
    masked: list[Path] = []
    for value in profile.masked_paths:
        path = Path(value).expanduser()
        if path.exists():
            masked.append(path)
    return masked


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
    command = [bwrap, *_bubblewrap_isolation(profile)]
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
    cleanup: list[str] | None = None,
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
        _run_cleanup(cleanup)
        for thread in threads:
            thread.join()
        raise SandboxError(
            f"Sandbox profile {profile_name!r} exceeded {timeout} seconds."
        ) from error
    for thread in threads:
        thread.join()
    truncated = bool(state["truncated"])
    if truncated:
        _run_cleanup(cleanup)
    stdout = bytes(buffers[0]).decode(errors="replace")
    stderr = bytes(buffers[1]).decode(errors="replace")
    if truncated:
        stdout += "\n[output limit exceeded; process terminated]\n"
        if returncode == 0:
            returncode = 125
    return returncode, stdout, stderr, truncated


def _run_cleanup(cleanup: list[str] | None) -> None:
    if not cleanup:
        return
    try:
        subprocess.run(  # nosec B603 - fixed engine command built by the sandbox
            cleanup, capture_output=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
