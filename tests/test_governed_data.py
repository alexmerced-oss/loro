from subprocess import CompletedProcess

from loro.config import PolarisConfig
from loro.governed_data import explain_access, inspect_table_schema
from loro.polaris import PolarisClient


def test_inspect_table_schema_payload(monkeypatch) -> None:
    def fake_run(command, capture_output, text, check):
        return CompletedProcess(command, 0, stdout='{"schema": "ok"}\n', stderr="")

    monkeypatch.setattr("loro.polaris.run", fake_run)
    result = inspect_table_schema(
        PolarisClient(PolarisConfig(cli_path="polaris")),
        table="events",
        namespace="analytics",
        catalog="prod",
    )
    payload = result.to_payload()
    assert payload["ok"] is True
    assert payload["table"] == "events"
    assert payload["command"] == [
        "polaris",
        "tables",
        "get",
        "events",
        "--namespace",
        "analytics",
        "--catalog",
        "prod",
    ]


def test_explain_access_uses_readonly_checks(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, capture_output, text, check):
        commands.append(command)
        return CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("loro.polaris.run", fake_run)
    result = explain_access(
        PolarisClient(PolarisConfig(cli_path="polaris")),
        resource="events",
        namespace="analytics",
        catalog="prod",
        catalog_role="reader",
    )
    assert result.ok is True
    assert len(result.checks) == 3
    assert commands[-1] == [
        "polaris",
        "privileges",
        "list",
        "--catalog-role",
        "reader",
        "--catalog",
        "prod",
    ]
    assert "read-only discovery" in result.explanation
