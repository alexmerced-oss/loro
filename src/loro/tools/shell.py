from dataclasses import dataclass
from pathlib import Path

from loro.config import SandboxConfig
from loro.sandbox import SandboxRunner


@dataclass(frozen=True)
class ShellResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int
    profile: str = "controlled-shell"
    os_enforced: bool = False
    output_truncated: bool = False


class ShellTools:
    def __init__(
        self,
        config: SandboxConfig | None = None,
        *,
        workspace_roots: list[str] | None = None,
    ) -> None:
        self.config = config or SandboxConfig()
        self.runner = SandboxRunner(self.config, workspace_roots=workspace_roots)

    def run(
        self,
        args: list[str],
        timeout: int = 120,
        *,
        profile: str | None = None,
        cwd: Path | None = None,
    ) -> ShellResult:
        result = self.runner.run(
            args,
            profile_name=profile or self.config.shell_profile,
            cwd=cwd,
            timeout=timeout,
        )
        return ShellResult(
            args=args,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            profile=result.profile,
            os_enforced=result.os_enforced,
            output_truncated=result.output_truncated,
        )
