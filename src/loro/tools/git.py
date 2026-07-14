from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess, run


@dataclass(frozen=True)
class GitResult:
    args: list[str]
    cwd: Path
    stdout: str
    stderr: str
    returncode: int


class GitTools:
    def run(self, args: list[str], *, cwd: Path = Path("."), timeout: int = 120) -> GitResult:
        cwd = cwd.expanduser()
        completed: CompletedProcess[str] = run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return GitResult(
            args=["git", *args],
            cwd=cwd,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
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
