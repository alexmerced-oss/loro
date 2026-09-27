"""OpenTelemetry instrumentation (in-memory exporters) and SIEM audit forwarding."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loro.audit import AuditLogger
from loro.audit.forwarding import SyslogForwarder, to_cef, to_ocsf
from loro.cli import app
from loro.config import (
    AuditConfig,
    AuditForwardConfig,
    LocalMemoryConfig,
    LoroConfig,
    MemoryConfig,
    RuntimeConfig,
    TelemetryConfig,
)
from loro.runtime import AgentRuntime
from loro.sessions import SessionConfig
from loro.telemetry import Telemetry

pytest.importorskip("opentelemetry.sdk")


def _config(tmp_path: Path) -> LoroConfig:
    return LoroConfig(
        runtime=RuntimeConfig(max_steps=1, native_tool_calling=False),
        memory=MemoryConfig(local=LocalMemoryConfig(enabled=False)),
        audit=AuditConfig(path=str(tmp_path / "audit.jsonl"), buffer_path=str(tmp_path / "b")),
        sessions=SessionConfig(path=str(tmp_path / "sessions")),
        telemetry=TelemetryConfig(enabled=True, exporter="none"),
    )


def _telemetry(config: LoroConfig):
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    spans, metrics = InMemorySpanExporter(), InMemoryMetricReader()
    return Telemetry(config.telemetry, span_exporter=spans, metric_reader=metrics), spans, metrics


def _metric_points(reader) -> dict[str, list]:
    points: dict[str, list] = {}
    data = reader.get_metrics_data()
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                points.setdefault(metric.name, []).extend(metric.data.data_points)
    return points


def test_runs_models_and_tools_are_traced_without_content(tmp_path: Path) -> None:
    config = _config(tmp_path)
    telemetry, spans, metrics = _telemetry(config)
    note = tmp_path / "secret-note.txt"
    note.write_text("confidential body text\n")

    result = AgentRuntime(config, telemetry=telemetry).run(
        f'Summarize the plan.\n@tool file.read {{"path": "{note}"}}', mode="run"
    )

    finished = {span.name: span for span in spans.get_finished_spans()}
    assert {"loro.run", "loro.model.call", "loro.tool"} <= set(finished)
    run = finished["loro.run"]
    assert run.attributes["loro.run_id"] == result.run_id
    assert run.attributes["loro.stop_reason"] == "completed"
    assert run.attributes["gen_ai.system"] == "mock"
    assert finished["loro.tool"].attributes["loro.tool"] == "file.read"
    assert finished["loro.tool"].parent.span_id == run.context.span_id
    rendered = json.dumps([dict(span.attributes) for span in finished.values()])
    assert "Summarize the plan" not in rendered
    assert "confidential body text" not in rendered
    assert str(note) not in rendered

    points = _metric_points(metrics)
    assert sum(point.value for point in points["loro.runs"]) == 1
    assert sum(point.value for point in points["loro.tool.calls"]) == 1
    assert points["loro.model.duration"][0].count == 1


def test_provider_failure_marks_the_span_as_error(tmp_path: Path) -> None:
    from opentelemetry.trace import StatusCode

    config = _config(tmp_path)
    config.model.provider = "nous"
    config.model.base_url = "http://127.0.0.1:9/v1"
    config.model.max_retries = 0
    telemetry, spans, _metrics = _telemetry(config)
    AgentRuntime(config, telemetry=telemetry).run("Hello.", mode="run")
    run = next(span for span in spans.get_finished_spans() if span.name == "loro.run")
    assert run.status.status_code is StatusCode.ERROR


def test_telemetry_is_a_no_op_when_disabled_or_not_installed(monkeypatch) -> None:
    disabled = Telemetry(TelemetryConfig(enabled=False))
    assert not disabled.enabled and disabled.reason == "disabled"
    with disabled.span("x") as span:
        assert span is None
    disabled.count("runs")
    monkeypatch.setattr("loro.telemetry.otel_available", lambda: False)
    missing = Telemetry(TelemetryConfig(enabled=True))
    assert not missing.enabled
    assert "loro-agent[otel]" in missing.reason


EVENT = {
    "schema_version": "1.0",
    "event_id": "e-1",
    "event_type": "approval.denied",
    "timestamp": "2026-09-27T12:00:00+00:00",
    "actor": "alex",
    "tenant_id": "acme",
    "session_id": "s-1",
    "trace_id": "run-1",
    "action": "shell.run",
    "target": "rm -rf | tee a=b",
    "result": None,
    "details": {"identity": {"display_name": "Alex", "groups": ["ops"], "verified": True}},
    "integrity": {"event_hash": "sha256:abc"},
}


def test_ocsf_mapping_keeps_the_loro_payload() -> None:
    ocsf = to_ocsf(EVENT)
    assert (ocsf["class_uid"], ocsf["category_uid"], ocsf["type_uid"]) == (6003, 6, 600399)
    assert ocsf["activity_name"] == "approval.denied"
    assert ocsf["status"] == "Failure" and ocsf["severity_id"] == 3
    assert ocsf["time"] == 1790510400000
    assert ocsf["actor"]["user"] == {
        "uid": "alex",
        "name": "Alex",
        "groups": [{"name": "ops"}],
    }
    assert ocsf["metadata"]["correlation_uid"] == "run-1"
    assert ocsf["unmapped"]["event_hash"] == "sha256:abc"
    assert ocsf["unmapped"]["identity_verified"] is True
    assert ocsf["resources"] == [{"name": "rm -rf | tee a=b"}]


def test_cef_escapes_header_and_extension_values() -> None:
    cef = to_cef({**EVENT, "event_type": "tool|executed", "result": {"ok": True}})
    assert cef.startswith("CEF:0|Alex Merced|Loro|")
    # Pipes are escaped in the header; in extensions only "=" and backslashes are.
    assert "|tool\\|executed|tool\\|executed|3|rt=" in cef
    assert "request=rm -rf | tee a\\=b" in cef
    assert "outcome=success" in cef
    assert "cs4=sha256:abc" in cef


class _Syslog:
    def __init__(self, protocol: str) -> None:
        self.received: list[bytes] = []
        self.ready = threading.Event()
        kind = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
        self.sock = socket.socket(socket.AF_INET, kind)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        if protocol == "tcp":
            self.sock.listen(1)
        threading.Thread(target=self._serve, args=(protocol,), daemon=True).start()

    def _serve(self, protocol: str) -> None:
        if protocol == "udp":
            data, _ = self.sock.recvfrom(65535)
            self.received.append(data)
        else:
            connection, _ = self.sock.accept()
            with connection:
                buffer = b""
                while (
                    not buffer
                    or len(buffer)
                    < int(buffer.split(b" ", 1)[0]) + len(buffer.split(b" ", 1)[0]) + 1
                ):
                    chunk = connection.recv(65535)
                    if not chunk:
                        break
                    buffer += chunk
                self.received.append(buffer)
        self.ready.set()


@pytest.mark.parametrize("protocol", ["udp", "tcp"])
def test_syslog_forwarder_frames_rfc5424(protocol: str) -> None:
    server = _Syslog(protocol)
    forwarder = SyslogForwarder(
        AuditForwardConfig(enabled=True, protocol=protocol, port=server.port, format="cef")
    )
    forwarder.send(EVENT)
    assert server.ready.wait(5)
    forwarder.close()
    frame = server.received[0].decode()
    if protocol == "tcp":
        length, _, frame = frame.partition(" ")
        assert int(length) == len(frame.encode())
    assert frame.startswith("<108>1 2026-09-27T12:00:00+00:00 ")  # facility 13, warning
    assert " loro - approval.denied - CEF:0|" in frame


def test_audit_logger_forwards_and_survives_siem_outages(tmp_path: Path) -> None:
    server = _Syslog("udp")
    config = AuditConfig(
        path=str(tmp_path / "audit.jsonl"),
        buffer_path=str(tmp_path / "b"),
        forward=AuditForwardConfig(enabled=True, protocol="udp", port=server.port),
    )
    AuditLogger(config).write("runtime.task_started", mode="run")
    assert server.ready.wait(5)
    ocsf = json.loads(server.received[0].decode().split(" - ", 2)[2])
    assert ocsf["activity_name"] == "runtime.task_started"

    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    down = config.model_copy(
        update={"forward": AuditForwardConfig(enabled=True, protocol="tcp", port=port)}
    )
    with pytest.warns(RuntimeWarning, match="local audit log is unaffected"):
        AuditLogger(down).write("runtime.task_completed", stop_reason="completed")
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["event_type"] == "runtime.task_completed"


def test_audit_export_cli_writes_ocsf_and_cef(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        f'schema_version = "1.0"\n[audit]\npath = "{tmp_path / "audit.jsonl"}"\n'
        f'buffer_path = "{tmp_path / "b"}"\n',
    )
    logger = AuditLogger(AuditConfig(path=str(tmp_path / "audit.jsonl"), buffer_path="b"))
    logger.write("runtime.task_started", mode="run")
    logger.write("approval.denied", request_id="r1")
    runner = CliRunner()
    ocsf = runner.invoke(app, ["audit", "export", "--format", "ocsf"])
    assert ocsf.exit_code == 0, ocsf.output
    rows = [json.loads(line) for line in ocsf.output.splitlines()]
    assert [row["activity_name"] for row in rows] == ["runtime.task_started", "approval.denied"]
    assert rows[0]["unmapped"]["event_hash"].startswith("sha256:")
    cef = runner.invoke(app, ["audit", "export", "--format", "cef", "-o", "out.cef"])
    assert cef.exit_code == 0 and "Wrote 2 CEF" in cef.output
    assert (tmp_path / "out.cef").read_text().count("CEF:0|") == 2
    later = runner.invoke(app, ["audit", "export", "--since", "2999-01-01T00:00:00Z"])
    assert later.exit_code == 0 and later.output == ""
    bad = runner.invoke(app, ["audit", "export", "--format", "xml"])
    assert bad.exit_code == 2
