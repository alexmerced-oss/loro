from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess, run

from loro.config import PolarisConfig, SandboxConfig
from loro.tools.shell import ShellTools


@dataclass(frozen=True)
class PolarisResult:
    command: list[str]
    stdout: str
    stderr: str
    returncode: int
    sandbox_profile: str | None = None
    sandbox_os_enforced: bool = False
    output_truncated: bool = False


class PolarisClient:
    """Typed read-only wrapper around the Polaris CLI."""

    def __init__(
        self,
        config: PolarisConfig,
        sandbox: SandboxConfig | None = None,
        *,
        workspace_roots: list[str] | None = None,
    ) -> None:
        self.config = config
        self.sandbox = sandbox
        self.shell = (
            ShellTools(sandbox, workspace_roots=workspace_roots) if sandbox is not None else None
        )

    def run_readonly(self, args: list[str]) -> PolarisResult:
        self._validate_readonly(args)
        command = [self.config.cli_path, *args]
        if self.shell is not None and self.sandbox is not None:
            result = self.shell.run(
                command,
                profile=self.sandbox.governed_data_profile,
                cwd=Path.cwd(),
            )
            return PolarisResult(
                command=command,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
                sandbox_profile=result.profile,
                sandbox_os_enforced=result.os_enforced,
                output_truncated=result.output_truncated,
            )
        # Polaris receives a structured argv assembled from typed command components.
        completed: CompletedProcess[str] = run(  # nosec B603
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

    def list_principal_roles(self) -> PolarisResult:
        return self.run_readonly(["principal-roles", "list"])

    def get_principal_role(self, role: str) -> PolarisResult:
        return self.run_readonly(["principal-roles", "get", role])

    def list_catalog_roles(self, catalog: str | None = None) -> PolarisResult:
        args = ["catalog-roles", "list"]
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

    def get_catalog_role(self, role: str, catalog: str | None = None) -> PolarisResult:
        args = ["catalog-roles", "get", role]
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

    def list_privileges(
        self,
        catalog_role: str | None = None,
        catalog: str | None = None,
    ) -> PolarisResult:
        args = ["privileges", "list"]
        if catalog_role:
            args.extend(["--catalog-role", catalog_role])
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

    def list_policies(self, catalog: str | None = None) -> PolarisResult:
        args = ["policies", "list"]
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

    def get_policy(self, policy: str, catalog: str | None = None) -> PolarisResult:
        args = ["policies", "get", policy]
        if catalog:
            args.extend(["--catalog", catalog])
        return self.run_readonly(args)

    def list_applicable_policies(
        self,
        resource: str,
        catalog: str | None = None,
        namespace: str | None = None,
    ) -> PolarisResult:
        args = ["applicable-policies", "list", "--resource", resource]
        if catalog:
            args.extend(["--catalog", catalog])
        if namespace:
            args.extend(["--namespace", namespace])
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
