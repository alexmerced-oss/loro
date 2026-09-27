"""Per-run evidence bundles: export, verification, tamper detection, redaction, CLI."""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loro.aais_bridge import AAISBridge
from loro.approvals import ApprovalRequest
from loro.audit import AuditLogger
from loro.cli import app
from loro.config import (
    AuditConfig,
    LocalMemoryConfig,
    LoroConfig,
    MemoryConfig,
    RuntimeConfig,
)
from loro.evidence import (
    MANIFEST,
    MANIFEST_DIGEST,
    MEMBERS,
    EvidenceError,
    export_run,
    list_runs,
    verify_bundle,
)
from loro.runtime import AgentRuntime
from loro.sessions import SessionConfig


def _config(tmp_path: Path) -> LoroConfig:
    return LoroConfig(
        runtime=RuntimeConfig(max_steps=1, native_tool_calling=False),
        memory=MemoryConfig(local=LocalMemoryConfig(enabled=False)),
        audit=AuditConfig(
            path=str(tmp_path / "audit.jsonl"), buffer_path=str(tmp_path / "buffer.jsonl")
        ),
        sessions=SessionConfig(path=str(tmp_path / "sessions")),
    )


def _run_with_tool(tmp_path: Path, config: LoroConfig, output: str = "hello evidence\n"):
    note = tmp_path / "note.txt"
    note.write_text(output, encoding="utf-8")
    return AgentRuntime(config).run(
        f'Read the note.\n@tool file.read {{"path": "{note}"}}', mode="run"
    )


def _rewrite(path: Path, name: str, content: bytes) -> None:
    """Replace one member while keeping every other member and header identical."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(buffer, "w") as target:
        for info in source.infolist():
            target.writestr(info, content if info.filename == name else source.read(info))
    path.write_bytes(buffer.getvalue())


@pytest.fixture
def exported(tmp_path: Path):
    config = _config(tmp_path)
    result = _run_with_tool(tmp_path, config)
    # Another run interleaves nothing here, but a later run must not leak into the slice.
    AgentRuntime(config).run("A later, unrelated task.", mode="run")
    bundle = export_run(config, result.run_id, tmp_path / "run.zip", project_root=tmp_path)
    return config, result, bundle


def test_export_and_verify_round_trip(exported, tmp_path: Path) -> None:
    config, result, bundle = exported

    verified = verify_bundle(
        bundle.path, expected_digest=bundle.digest, audit_log=tmp_path / "audit.jsonl"
    )

    assert verified.ok, verified.issues
    assert verified.run_id == result.run_id
    assert "original audit log" in verified.checks
    with zipfile.ZipFile(bundle.path) as archive:
        assert archive.namelist() == [MANIFEST, MANIFEST_DIGEST, *MEMBERS]
        run = json.loads(archive.read("run.json"))
        tools = json.loads(archive.read("tool-calls.json"))
        digests = json.loads(archive.read("digests.json"))
        slice_events = [json.loads(line) for line in archive.read("audit-slice.jsonl").splitlines()]
    assert run["provider"] == "mock" and run["model"] == config.model.model
    assert run["usage"] == result.usage
    assert run["session_id"] == result.session_id
    assert tools["source"] == "session-record"
    assert tools["results"][0]["output"] == "hello evidence\n"
    assert digests["config_digest_at_run"] == digests["config_digest_at_export"]
    assert {event["trace_id"] for event in slice_events} == {result.run_id}
    assert "A later, unrelated task" not in (tmp_path / "run.zip").read_text(errors="ignore")


@pytest.mark.parametrize("member", [MANIFEST, MANIFEST_DIGEST, *MEMBERS])
def test_one_tampered_byte_in_any_member_fails(exported, member: str) -> None:
    _config_value, _result, bundle = exported
    with zipfile.ZipFile(bundle.path) as archive:
        content = bytearray(archive.read(member))
    content[len(content) // 2] ^= 0x01
    _rewrite(bundle.path, member, bytes(content))

    verified = verify_bundle(bundle.path)

    assert not verified.ok
    assert verified.issues


def test_single_byte_flips_anywhere_in_the_archive_fail(exported) -> None:
    _config_value, _result, bundle = exported
    original = bundle.path.read_bytes()
    # Every header byte of the first entry, then a stride through data and the directory.
    positions = sorted({*range(0, 64), *range(64, len(original), 97), len(original) - 1})
    for position in positions:
        damaged = bytearray(original)
        damaged[position] ^= 0x01
        bundle.path.write_bytes(bytes(damaged))
        assert not verify_bundle(bundle.path).ok, f"byte {position} flip went undetected"
    bundle.path.write_bytes(original)
    assert verify_bundle(bundle.path).ok


def test_wrong_expected_digest_and_foreign_audit_log_fail(exported, tmp_path: Path) -> None:
    _config_value, _result, bundle = exported
    assert not verify_bundle(bundle.path, expected_digest="sha256:" + "0" * 64).ok
    other = tmp_path / "other"
    other.mkdir()
    other_config = _config(other)
    AgentRuntime(other_config).run("Different log.", mode="run")
    verified = verify_bundle(bundle.path, audit_log=other / "audit.jsonl")
    assert not verified.ok
    assert any("not present in this audit log" in issue for issue in verified.issues)


def test_tool_results_are_redacted_by_classification_policy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    # Confidential content is allowed in tool output and the session record, but the audit
    # surface (and so the bundle) only admits up to "internal".
    result = _run_with_tool(
        tmp_path, config, output="[classification: confidential] quarterly figures\n"
    )

    bundle = export_run(config, result.run_id, tmp_path / "run.zip", project_root=tmp_path)

    with zipfile.ZipFile(bundle.path) as archive:
        tools = json.loads(archive.read("tool-calls.json"))
        everything = b"".join(archive.read(name) for name in archive.namelist())
    assert b"quarterly figures" not in everything
    decision = tools["redaction"]["decisions"][0]
    assert decision["classification"] == "confidential"
    assert decision["maximum_classification"] == "internal"
    assert verify_bundle(bundle.path).ok


def test_secrets_never_reach_the_bundle(tmp_path: Path) -> None:
    config = _config(tmp_path)
    secret = "AKIA" + "Q" * 16
    result = _run_with_tool(tmp_path, config, output=f"aws key {secret}\n")

    bundle = export_run(config, result.run_id, tmp_path / "run.zip", project_root=tmp_path)

    with zipfile.ZipFile(bundle.path) as archive:
        everything = b"".join(archive.read(name) for name in archive.namelist())
    assert secret.encode() not in everything


def test_advanced_session_exports_audit_metadata_only(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = _run_with_tool(tmp_path, config)
    AgentRuntime(config).run("Next turn.", mode="run", session_id=first.session_id)

    bundle = export_run(config, first.run_id, tmp_path / "run.zip", project_root=tmp_path)

    with zipfile.ZipFile(bundle.path) as archive:
        tools = json.loads(archive.read("tool-calls.json"))
    assert tools["source"] == "audit-metadata-only"
    assert tools["results"] == []
    assert tools["audit"][0]["tool"] == "file.read"
    assert any("session record has advanced" in warning for warning in bundle.warnings)
    assert verify_bundle(bundle.path).ok


def test_aais_receipts_are_included(tmp_path: Path) -> None:
    config = _config(tmp_path)
    bridge = AAISBridge(tmp_path)
    request = ApprovalRequest(
        action="shell.run",
        target="echo hi",
        arguments={"command": "echo hi"},
        identity_subject="tester",
        identity_tenant="default",
        identity_session_id="session-1",
        policy_decision="ask",
        policy_version="test",
        policy_source="test",
        policy_reason="needs approval",
        risk_reason="runs a command",
    )
    ready = threading.Event()
    outcome: list[object] = []
    worker = threading.Thread(
        target=lambda: outcome.append(
            bridge.request(
                request,
                origin={},
                publish=lambda *_: ready.set(),
                allow_session=False,
                cancelled=threading.Event(),
                timeout=10,
            )
        )
    )
    worker.start()
    assert ready.wait(5)
    bridge.decide(request.request_id, decision="approve", scope="once", actor_id="reviewer")
    worker.join(5)

    audit = AuditLogger(config.audit)
    audit.bind_context(trace_id="run-with-approval")
    audit.write("runtime.task_started", mode="run", model_provider="mock", model="m")
    audit.write("approval.requested", request_id=request.request_id)
    audit.write("approval.granted", request_id=request.request_id)
    audit.write("runtime.task_completed", session_id="none", stop_reason="completed", usage={})

    bundle = export_run(config, "run-with-approval", tmp_path / "run.zip", project_root=tmp_path)

    with zipfile.ZipFile(bundle.path) as archive:
        approvals = json.loads(archive.read("approvals.json"))
    receipt = approvals["aais_receipts"][request.request_id]
    assert receipt["resolution"]["resolution"]["outcome"] == "approved"
    assert receipt["request"]["request"]["id"] == request.request_id
    assert "decision" in receipt
    assert bundle.approvals == 1


def test_unknown_run_and_missing_log_are_user_errors(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(EvidenceError, match="No audit log"):
        export_run(config, "nope", tmp_path / "x.zip")
    AgentRuntime(config).run("Seed the log.", mode="run")
    with pytest.raises(EvidenceError, match="loro run list"):
        export_run(config, "nope", tmp_path / "x.zip")
    with pytest.raises(EvidenceError, match="No bundle"):
        verify_bundle(tmp_path / "missing.zip")


def test_list_runs_newest_first(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = AgentRuntime(config).run("one", mode="run")
    time.sleep(0.01)
    second = AgentRuntime(config).run("two", mode="plan")
    runs = list_runs(config)
    assert [run.run_id for run in runs[:2]] == [second.run_id, first.run_id]
    assert runs[0].mode == "plan" and runs[0].stop_reason == "completed"


@pytest.fixture
def cli_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "LORO_CONFIG_CONTENT",
        'schema_version = "1.0"\n[model]\nprovider = "mock"\nmodel = "mock-agent"\n'
        f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n'
        f'buffer_path = "{tmp_path / "buffer.jsonl"}"\n'
        f'[sessions]\npath = "{tmp_path / "sessions"}"\n'
        f'message_path = "{tmp_path / "messages"}"\n'
        "[memory.local]\nenabled = false\n",
    )
    return tmp_path


def test_cli_run_prints_id_and_evidence_commands_work(cli_project: Path) -> None:
    runner = CliRunner()
    ran = runner.invoke(app, ["run", "Summarize nothing."])
    assert ran.exit_code == 0, ran.output
    assert "loro run export" in ran.output

    listed = runner.invoke(app, ["run", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    run_id = json.loads(listed.output)[0]["run_id"]
    assert run_id in ran.output

    exported = runner.invoke(app, ["run", "export", run_id, "--out", "run.zip", "--json"])
    assert exported.exit_code == 0, exported.output
    digest = json.loads(exported.output)["bundle_digest"]

    verified = runner.invoke(app, ["run", "verify", "run.zip", "--expect-digest", digest])
    assert verified.exit_code == 0, verified.output
    assert "Verified" in verified.output

    again = runner.invoke(app, ["run", "export", run_id, "--out", "run.zip"])
    assert again.exit_code == 2
    assert "--force" in again.output

    missing = runner.invoke(app, ["run", "export", "no-such-run"])
    assert missing.exit_code == 2
    assert "Traceback" not in missing.output

    damaged = bytearray((cli_project / "run.zip").read_bytes())
    damaged[len(damaged) // 3] ^= 0x01
    (cli_project / "run.zip").write_bytes(bytes(damaged))
    failed = runner.invoke(app, ["run", "verify", "run.zip", "--json"])
    assert failed.exit_code == 1
    assert json.loads(failed.output)["ok"] is False


def test_double_dash_runs_a_prompt_named_like_a_subcommand(cli_project: Path) -> None:
    result = CliRunner().invoke(app, ["run", "--", "export"])
    assert result.exit_code == 0, result.output
    assert "Mock response for" in result.output and "export" in result.output
