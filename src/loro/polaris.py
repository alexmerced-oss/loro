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

    def list_catalogs(self) -> PolarisResult:
        return self.run_readonly(["catalogs", "list"])

    def get_catalog(self, catalog: str) -> PolarisResult:
        return self.run_readonly(["catalogs", "get", catalog])

    def list_namespaces(self, catalog: str | None = None) -> PolarisResult:
        args = ["namespaces", "list"]
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

    def get_namespace(self, namespace: str, catalog: str | None = None) -> PolarisResult:
        args = ["namespaces", "get", namespace]
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

    def list_tables(
        self,
        namespace: str | None = None,
        catalog: str | None = None,
    ) -> PolarisResult:
        args = ["tables", "list"]
        if namespace:
            args.extend(["--namespace", namespace])
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

    def get_table(
        self,
        table: str,
        namespace: str | None = None,
        catalog: str | None = None,
    ) -> PolarisResult:
        args = ["tables", "get", table]
        if namespace:
            args.extend(["--namespace", namespace])
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

    def list_views(
        self,
        namespace: str | None = None,
        catalog: str | None = None,
    ) -> PolarisResult:
        args = ["views", "list"]
        if namespace:
            args.extend(["--namespace", namespace])
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

    def get_view(
        self,
        view: str,
        namespace: str | None = None,
        catalog: str | None = None,
    ) -> PolarisResult:
        args = ["views", "get", view]
        if namespace:
            args.extend(["--namespace", namespace])
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

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
