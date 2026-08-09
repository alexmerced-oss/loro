from dataclasses import dataclass
from pathlib import Path

from loro.config import SandboxConfig
from loro.tools.shell import ShellTools


@dataclass(frozen=True)
class GitResult:
    args: list[str]
    cwd: Path
    stdout: str
    stderr: str
    returncode: int
    sandbox_profile: str | None = None
    sandbox_os_enforced: bool = False
    output_truncated: bool = False


class GitTools:
    def __init__(
        self,
        sandbox: SandboxConfig | None = None,
        *,
        workspace_roots: list[str] | None = None,
    ) -> None:
        self.sandbox = sandbox or SandboxConfig()
        self.shell = ShellTools(self.sandbox, workspace_roots=workspace_roots)

    def run(self, args: list[str], *, cwd: Path = Path("."), timeout: int = 120) -> GitResult:
        cwd = cwd.expanduser().resolve()
        result = self.shell.run(
            ["git", *args],
            cwd=cwd,
            timeout=timeout,
            profile=self.sandbox.git_profile,
        )
        return GitResult(
            args=["git", *args],
            cwd=cwd,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            sandbox_profile=result.profile,
            sandbox_os_enforced=result.os_enforced,
            output_truncated=result.output_truncated,
        )

    def status(self, *, cwd: Path = Path("."), timeout: int = 120) -> GitResult:
        return self.run(["status", "--short"], cwd=cwd, timeout=timeout)

    def diff(self, *, cwd: Path = Path("."), timeout: int = 120) -> GitResult:
        return self.run(["diff", "--"], cwd=cwd, timeout=timeout)

    def show(self, revision: str, *, cwd: Path = Path("."), timeout: int = 120) -> GitResult:
        return self.run(["show", "--stat", "--oneline", revision], cwd=cwd, timeout=timeout)

    def add(self, paths: list[str], *, cwd: Path = Path("."), timeout: int = 120) -> GitResult:
        return self.run(["add", "--", *paths], cwd=cwd, timeout=timeout)

    def commit(self, message: str, *, cwd: Path = Path("."), timeout: int = 120) -> GitResult:
        return self.run(
            [
                "-c",
                "user.name=Loro Agent",
                "-c",
                "user.email=loro-agent@example.invalid",
                "commit",
                "-m",
                message,
            ],
            cwd=cwd,
            timeout=timeout,
        )
