from dataclasses import dataclass
from subprocess import CompletedProcess, run

from loro.config import PolarisConfig


@dataclass(frozen=True)
class PolarisResult:
    command: list[str]
    stdout: str
    stderr: str
    returncode: int


class PolarisClient:
    """Typed wrapper around the Polaris CLI.

    This scaffold intentionally exposes only a small read-only command helper.
    Future work should add typed methods for catalogs, namespaces, tables,
    roles, privileges, and policies.
    """

    def __init__(self, config: PolarisConfig) -> None:
        self.config = config

    def run_readonly(self, args: list[str]) -> PolarisResult:
        command = [self.config.cli_path, *args]
        completed: CompletedProcess[str] = run(command, capture_output=True, text=True, check=False)
        return PolarisResult(
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
