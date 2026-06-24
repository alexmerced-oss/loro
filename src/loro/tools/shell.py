from dataclasses import dataclass
from subprocess import CompletedProcess, run


@dataclass(frozen=True)
class ShellResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int


class ShellTools:
    def run(self, args: list[str], timeout: int = 120) -> ShellResult:
        completed: CompletedProcess[str] = run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return ShellResult(
            args=args,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
