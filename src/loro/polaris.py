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
        self._validate_readonly(args)
        command = [self.config.cli_path, *args]
        completed: CompletedProcess[str] = run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        return PolarisResult(
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

    def _validate_readonly(self, args: list[str]) -> None:
        if len(args) < 2:
            raise ValueError("Polaris read-only operations require a resource and action.")
        resource, action = args[0], args[1]
        allowed_resources = {
            "catalogs",
            "namespaces",
            "tables",
            "views",
            "principal-roles",
            "catalog-roles",
            "privileges",
            "policies",
            "applicable-policies",
        }
        allowed_actions = {"list", "get", "show", "describe"}
        if resource not in allowed_resources or action not in allowed_actions:
            raise PermissionError(f"Polaris operation is not read-only: {' '.join(args)}")
