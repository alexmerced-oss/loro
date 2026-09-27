"""Regression tests for issues found in the 0.22 self-review (not a penetration test)."""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from loro.config import ContainerSandboxConfig, OIDCConfig, SandboxConfig, SandboxProfileConfig
from loro.oidc import OIDCError, OIDCProvider
from loro.sandbox import SandboxError, SandboxRunner
from loro.tools.patching import PatchError, apply_patch
from loro.tools.web_fetch import public_address
from loro.webui.auth import safe_next


@pytest.mark.parametrize(
    "address",
    [
        "64:ff9b::7f00:1",  # NAT64 for 127.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 for 169.254.169.254 (cloud metadata)
        "64:ff9b:1::a00:1",  # local-use NAT64 prefix
        "::127.0.0.1",  # deprecated IPv4-compatible address
        "2002:a00:1::1",  # 6to4 wrapping 10.0.0.1
    ],
)
def test_ssrf_filter_rejects_tunnelled_private_addresses(address: str) -> None:
    assert not public_address(address)


def test_ssrf_filter_keeps_real_public_addresses() -> None:
    assert public_address("64:ff9b::5db8:d822")  # NAT64 for a public IPv4 address
    assert public_address("2606:4700:4700::1111")


def _provider(idp) -> OIDCProvider:
    return OIDCProvider(OIDCConfig(enabled=True, issuer=idp.issuer, audience="loro-api"))


@pytest.mark.parametrize("audience", [5, ["loro-api", 7], {"loro-api": True}, None])
def test_malformed_audience_is_a_clean_rejection(idp, audience) -> None:
    token = idp.token({"sub": "a", "aud": audience})
    with pytest.raises(OIDCError, match="audience"):
        _provider(idp).verify(token)


def test_oversized_tokens_are_rejected_before_parsing(idp) -> None:
    with pytest.raises(OIDCError, match="too large"):
        _provider(idp).verify("a" * 20_000 + ".b.c")


@pytest.mark.parametrize(
    "value", ["/\t/evil.example", "/\n/evil.example", "/ok\r\nSet-Cookie: x=1", "/\x7f"]
)
def test_login_return_path_rejects_control_characters(value: str) -> None:
    assert safe_next(value) == "/"
    assert safe_next("/runs?view=1") == "/runs?view=1"


def test_rename_cannot_read_a_file_outside_the_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret\n")
    root = tmp_path / "ws"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    patch = "--- a/link/secret.txt\n+++ b/copied.txt\n@@ -1 +1 @@\n-top secret\n+top secret\n"
    with pytest.raises(PatchError, match="leaves the workspace"):
        apply_patch(patch, root)
    assert not (root / "copied.txt").exists()


def test_dry_run_respects_read_denials(tmp_path: Path) -> None:
    from loro.config import LoroConfig, PermissionsConfig
    from loro.tool_runtime import ToolCall, ToolRegistry

    (tmp_path / "a.txt").write_text("private line\n")
    config = LoroConfig(permissions=PermissionsConfig(edit="deny"))
    config.audit.path = str(tmp_path / "audit.jsonl")
    patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-guess\n+x\n"
    result = ToolRegistry(config, project_root=tmp_path).execute(
        ToolCall(name="patch.apply", args={"patch": patch, "root": str(tmp_path), "dry_run": True})
    )
    assert not result.ok and "denied" in result.output
    assert "private line" not in result.output


def _container(tmp_path: Path) -> SandboxRunner:
    profile = SandboxProfileConfig(
        backend="container",
        allowed_executables=["cat"],
        environment_allowlist=["PATH", "API_TOKEN"],
        container=ContainerSandboxConfig(image="alpine:latest"),
    )
    return SandboxRunner(
        SandboxConfig(profiles={"controlled-shell": profile}),
        environ={"PATH": "/usr/bin", "API_TOKEN": "value-that-must-not-leak"},
    )


def test_container_env_values_never_reach_the_command_line(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("loro.sandbox.shutil.which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr("loro.sandbox.container_runtimes", lambda engine: set())
    launch = _container(tmp_path).prepare(
        ["cat", "x"], profile_name="controlled-shell", cwd=tmp_path
    )
    assert not any("value-that-must-not-leak" in item for item in launch.args)
    assert launch.args[launch.args.index("--env") + 1] == "API_TOKEN"
    assert launch.environment["API_TOKEN"] == "value-that-must-not-leak"


def test_container_refuses_to_mount_home_root_or_odd_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("loro.sandbox.shutil.which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr("loro.sandbox.container_runtimes", lambda engine: set())
    runner = _container(tmp_path)
    for cwd in (Path("/"), Path.home()):
        with pytest.raises(SandboxError, match="Refusing to mount"):
            runner.prepare(["cat"], profile_name="controlled-shell", cwd=cwd)
    odd = tmp_path / "a:b"
    odd.mkdir()
    with pytest.raises(SandboxError, match="cannot contain"):
        runner.prepare(["cat"], profile_name="controlled-shell", cwd=odd)


def test_spans_never_record_exception_messages() -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from loro.config import TelemetryConfig
    from loro.telemetry import Telemetry

    exporter = InMemorySpanExporter()
    telemetry = Telemetry(TelemetryConfig(enabled=True, exporter="none"), span_exporter=exporter)
    with pytest.raises(RuntimeError), telemetry.span("loro.model.call"):
        raise RuntimeError("provider said: secret-response-body")
    [span] = exporter.get_finished_spans()
    rendered = json.dumps(
        [dict(span.attributes), [dict(event.attributes or {}) for event in span.events]]
    )
    assert "secret-response-body" not in rendered
    assert span.attributes["error.type"] == "RuntimeError"
    assert span.status.description == "RuntimeError"


def test_evidence_verify_rejects_hostile_archives_before_reading(tmp_path: Path) -> None:
    from loro.evidence import verify_bundle

    bundle = tmp_path / "hostile.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index in range(200):
            archive.writestr(f"member-{index}", b"x")
    bundle.write_bytes(buffer.getvalue())
    result = verify_bundle(bundle)
    assert not result.ok and "200 members" in result.issues[0]


def test_symlinked_patch_targets_stay_confined(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep\n")
    root = tmp_path / "ws"
    root.mkdir()
    os.symlink(outside, root / "app.txt")
    patch = "--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-keep\n+changed\n"
    with pytest.raises(PatchError, match="leaves the workspace"):
        apply_patch(patch, root)
    assert outside.read_text() == "keep\n"


def test_cef_header_fields_cannot_inject_records() -> None:
    from loro.audit.forwarding import to_cef

    line = to_cef({"event_type": "tool.call\r\nCEF:0|forged", "tool": "x"})
    assert "\r" not in line and "\n" not in line


@pytest.mark.parametrize(
    ("runner", "extra"),
    [
        ("pytest", ["--basetemp=/home/someone/project"]),
        ("pytest", ["--basetemp", "."]),
        ("pytest", ["--junitxml=report.xml"]),
        ("pytest", ["-o", "cache_dir=x"]),
        ("pytest", ["-pplugin"]),
        ("pytest", ["../other/tests"]),
        ("pytest", ["/etc/passwd"]),
        ("cargo", ["--target-dir=target2"]),
        ("cargo", ["--manifest-path", "x/Cargo.toml"]),
        ("npm", ["--prefix=/tmp"]),
    ],
)
def test_test_runner_refuses_arguments_that_escape_the_run(tmp_path, runner, extra) -> None:
    from loro.tools.test_runner import build_command

    with pytest.raises(ValueError, match="refused"):
        build_command(tmp_path, runner, extra)


def test_test_runner_keeps_ordinary_selectors(tmp_path) -> None:
    from loro.tools.test_runner import build_command

    args = build_command(tmp_path, "pytest", ["tests/test_a.py::test_b", "-k", "fast", "-x"]).args
    assert args[-4:] == ["tests/test_a.py::test_b", "-k", "fast", "-x"]
    assert build_command(tmp_path, "cargo", ["--release", "parser"]).args[-2:] == [
        "--release",
        "parser",
    ]
