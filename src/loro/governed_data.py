from dataclasses import dataclass, field

from loro.polaris import PolarisClient, PolarisResult


@dataclass(frozen=True)
class GovernedTableSchema:
    table: str
    namespace: str | None
    catalog: str | None
    table_result: PolarisResult

    @property
    def ok(self) -> bool:
        return self.table_result.returncode == 0

    def to_payload(self) -> dict[str, object]:
        return {
            "table": self.table,
            "namespace": self.namespace,
            "catalog": self.catalog,
            "ok": self.ok,
            "command": self.table_result.command,
            "stdout": self.table_result.stdout,
            "stderr": self.table_result.stderr,
            "returncode": self.table_result.returncode,
        }


@dataclass(frozen=True)
class AccessExplanation:
    resource: str
    catalog: str | None
    namespace: str | None
    checks: list[PolarisResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.returncode == 0 for check in self.checks)

    def to_payload(self) -> dict[str, object]:
        return {
            "resource": self.resource,
            "catalog": self.catalog,
            "namespace": self.namespace,
            "ok": self.ok,
            "checks": [
                {
                    "command": check.command,
                    "stdout": check.stdout,
                    "stderr": check.stderr,
                    "returncode": check.returncode,
                }
                for check in self.checks
            ],
            "explanation": self.explanation,
        }

    @property
    def explanation(self) -> str:
        failed = [check for check in self.checks if check.returncode != 0]
        if failed:
            return (
                "One or more read-only Polaris discovery checks failed. "
                "Review stderr/stdout to understand the access boundary."
            )
        return (
            "Polaris returned catalog metadata, applicable policy metadata, and privilege "
            "metadata for the requested resource. This indicates the configured identity can "
            "perform these read-only discovery operations; query or mutation access still "
            "depends on Polaris privileges and downstream engine policy."
        )


def inspect_table_schema(
    client: PolarisClient,
    *,
    table: str,
    namespace: str | None = None,
    catalog: str | None = None,
) -> GovernedTableSchema:
    return GovernedTableSchema(
        table=table,
        namespace=namespace,
        catalog=catalog,
        table_result=client.get_table(table, namespace=namespace, catalog=catalog),
    )


def explain_access(
    client: PolarisClient,
    *,
    resource: str,
    namespace: str | None = None,
    catalog: str | None = None,
    catalog_role: str | None = None,
) -> AccessExplanation:
    checks = [
        client.get_table(resource, namespace=namespace, catalog=catalog),
        client.list_applicable_policies(resource, namespace=namespace, catalog=catalog),
    ]
    if catalog_role:
        checks.append(client.list_privileges(catalog_role=catalog_role, catalog=catalog))
    else:
        checks.append(client.list_privileges(catalog=catalog))
    return AccessExplanation(
        resource=resource,
        namespace=namespace,
        catalog=catalog,
        checks=checks,
    )
