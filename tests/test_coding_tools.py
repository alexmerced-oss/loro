"""patch.apply, tests.run and web.fetch: behavior, safety limits, and policy gates."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest

from loro.config import LoroConfig, PermissionsConfig, WebFetchConfig
from loro.tool_runtime import ToolCall, ToolRegistry
from loro.tool_schemas import tool_catalog
from loro.tools.patching import PatchError, apply_patch, parse_unified_diff
from loro.tools.test_runner import build_command, detect_runner, summarize, tail
from loro.tools.web_fetch import WebFetcher, WebFetchError, domain_allowed, public_address

MODIFY = """--- a/app.py
+++ b/app.py
@@ -1,4 +1,4 @@
 def greet(name):
-    return "hi " + name
+    return f"hello {name}"

 VALUE = 1
"""


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_modify_create_delete_and_rename(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'def greet(name):\n    return "hi " + name\n\nVALUE = 1\n')
    _write(tmp_path, "old.txt", "a\nb\n")
    _write(tmp_path, "gone.txt", "bye\n")
    patch = (
        MODIFY
        + "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1,2 @@\n+X = 1\n+Y = 2\n"
        + "--- a/gone.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"
        + "--- a/old.txt\n+++ b/renamed.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n"
    )
    result = apply_patch(patch, tmp_path)
    assert result.ok and result.applied, result.summary()
    assert 'return f"hello {name}"' in (tmp_path / "app.py").read_text()
    assert (tmp_path / "pkg/new.py").read_text() == "X = 1\nY = 2\n"
    assert not (tmp_path / "gone.txt").exists()
    assert (tmp_path / "renamed.txt").read_text() == "a\nc\n"
    assert not (tmp_path / "old.txt").exists()
    actions = {item["path"]: item["action"] for item in result.files}
    assert actions == {
        "app.py": "modify",
        "pkg/new.py": "create",
        "gone.txt": "delete",
        "renamed.txt": "rename",
    }


def test_hunks_tolerate_line_drift(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "app.py",
        '# header\n# more\ndef greet(name):\n    return "hi " + name\n\nVALUE = 1\n',
    )
    assert apply_patch(MODIFY, tmp_path).ok


def test_conflicts_are_reported_and_nothing_is_written(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'def greet(name):\n    return "hey " + name\n\nVALUE = 1\n')
    _write(tmp_path, "b.txt", "one\n")
    both = MODIFY + "--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-one\n+two\n"
    result = apply_patch(both, tmp_path)
    assert not result.ok and not result.applied
    [conflict] = result.conflicts
    assert conflict.path == "app.py" and conflict.line == 2
    assert conflict.expected == '    return "hi " + name'
    assert conflict.actual == '    return "hey " + name'
    assert "expected:" in result.summary()
    assert (tmp_path / "b.txt").read_text() == "one\n"  # all or nothing


def test_dry_run_reports_without_writing(tmp_path: Path) -> None:
    original = 'def greet(name):\n    return "hi " + name\n\nVALUE = 1\n'
    _write(tmp_path, "app.py", original)
    result = apply_patch(MODIFY, tmp_path, dry_run=True)
    assert result.ok and not result.applied
    assert result.summary().startswith("Would apply 1 file change(s)")
    assert (tmp_path / "app.py").read_text() == original


@pytest.mark.parametrize(
    "header",
    ["--- a/../x\n+++ b/../x\n", "--- /etc/passwd\n+++ /etc/passwd\n", "--- a\\b\n+++ a\\b\n"],
)
def test_paths_cannot_leave_the_workspace(header: str) -> None:
    with pytest.raises(PatchError, match="relative"):
        parse_unified_diff(header + "@@ -1 +1 @@\n-a\n+b\n")


def test_malformed_patches_are_rejected() -> None:
    with pytest.raises(PatchError, match="No unified diff"):
        parse_unified_diff("just text")
    with pytest.raises(PatchError, match="shorter"):
        parse_unified_diff("--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n a\n")


def _registry(tmp_path: Path, **permissions: str) -> ToolRegistry:
    config = LoroConfig(
        permissions=PermissionsConfig(workspace_roots=[str(tmp_path)], **permissions),
        web_fetch=WebFetchConfig(allowed_domains=["docs.example.com"]),
    )
    config.approvals.interactive = False
    config.audit.path = str(tmp_path / "audit.jsonl")
    return ToolRegistry(config, project_root=tmp_path)


def test_patch_apply_requires_approval_and_stays_in_the_workspace(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'def greet(name):\n    return "hi " + name\n\nVALUE = 1\n')
    registry = _registry(tmp_path)
    denied = registry.execute(
        ToolCall(name="patch.apply", args={"patch": MODIFY, "root": str(tmp_path)}, origin="model")
    )
    assert not denied.ok and "approval" in denied.output.lower()
    preview = registry.execute(
        ToolCall(
            name="patch.apply",
            args={"patch": MODIFY, "root": str(tmp_path), "dry_run": True},
            origin="model",
        )
    )
    assert preview.ok and preview.metadata["patch_dry_run"] is True
    approved = registry.execute(
        ToolCall(
            name="patch.apply",
            args={"patch": MODIFY, "root": str(tmp_path), "approved": True},
            origin="user",
        )
    )
    assert approved.ok, approved.output
    assert approved.metadata["patch_applied"] is True
    outside = registry.execute(
        ToolCall(name="patch.apply", args={"patch": MODIFY, "root": "/"}, origin="user")
    )
    assert not outside.ok and "workspace" in outside.output


def test_runner_detection_and_commands(tmp_path: Path) -> None:
    assert detect_runner(tmp_path) is None
    _write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    assert detect_runner(tmp_path) == "pytest"
    _write(tmp_path, "package.json", json.dumps({"scripts": {"test": "node t.js"}}))
    assert detect_runner(tmp_path) == "npm"
    _write(tmp_path, "Cargo.toml", "[package]\nname='x'\n")
    assert detect_runner(tmp_path) == "cargo"
    assert build_command(tmp_path, "pytest", ["-k", "fast"]).args[-3:] == ["-q", "-k", "fast"]
    assert build_command(tmp_path, "npm", ["x"]).args == ["npm", "test", "--silent", "--", "x"]
    with pytest.raises(ValueError, match="simple tokens"):
        build_command(tmp_path, "pytest", ["; rm -rf /"])
    with pytest.raises(ValueError, match="No test runner"):
        build_command(tmp_path / "empty", "auto", [])
    assert summarize("pytest", "..\n===== 3 passed, 1 skipped in 0.1s =====\n") == (
        "3 passed, 1 skipped in 0.1s"
    )
    assert summarize("cargo", "test result: ok. 4 passed; 0 failed") == (
        "test result: ok. 4 passed; 0 failed"
    )
    text, truncated = tail("x" * 50 + "END", 10)
    assert truncated and text.endswith("END")


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm is not installed")
def test_tests_run_executes_in_the_test_sandbox(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    _write(
        project,
        "package.json",
        json.dumps(
            {"name": "x", "scripts": {"test": "node -e \"console.log('Tests: 2 passed')\""}}
        ),
    )
    registry = _registry(tmp_path, shell="allow")
    result = registry.execute(
        ToolCall(name="tests.run", args={"path": str(project)}, origin="model")
    )
    assert result.ok, result.output
    assert result.output.startswith("npm exited 0: Tests: 2 passed")
    assert result.metadata["test_runner"] == "npm"
    assert result.metadata["sandbox_profile"] == "test-runner"


def test_tests_run_is_gated_by_shell_policy(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    registry = _registry(tmp_path, shell="deny")
    result = registry.execute(ToolCall(name="tests.run", args={"path": str(tmp_path)}))
    assert not result.ok and "denied by policy" in result.output


def test_address_and_domain_rules() -> None:
    for address in (
        "127.0.0.1",
        "10.1.2.3",
        "192.168.0.1",
        "169.254.169.254",
        "::1",
        "fd00::1",
        "100.64.0.1",
        "0.0.0.0",
        "::ffff:127.0.0.1",
    ):
        assert not public_address(address), address
    assert public_address("93.184.216.34")
    assert domain_allowed("docs.example.com", ["docs.example.com"])
    assert domain_allowed("a.example.com", ["*.example.com"])
    assert not domain_allowed("example.com", ["*.example.com"])
    assert not domain_allowed("docs.example.com.evil.net", ["docs.example.com"])


def _fetcher(handler, resolved: dict[str, list[str]] | None = None, **config) -> WebFetcher:
    addresses = resolved or {"docs.example.com": ["93.184.216.34"]}
    return WebFetcher(
        WebFetchConfig(allowed_domains=["docs.example.com", "*.example.org"], **config),
        resolver=lambda host, _port: addresses.get(host, ["93.184.216.34"]),
        transport=httpx.MockTransport(handler),
        peer_check=False,
    )


def test_fetch_returns_bounded_text() -> None:
    fetcher = _fetcher(
        lambda request: httpx.Response(
            200, text="a" * 5000, headers={"content-type": "text/plain"}
        ),
        max_bytes=1024,
    )
    page = fetcher.fetch("https://docs.example.com/page")
    assert page.status == 200 and page.truncated and page.bytes_read == 1024


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://docs.example.com/", "Only https"),
        ("https://evil.example.net/", "not in web_fetch.allowed_domains"),
        ("https://127.0.0.1/", "literal IP"),
        ("https://user:pw@docs.example.com/", "credentials"),
    ],
)
def test_fetch_refuses_unsafe_urls(url: str, message: str) -> None:
    fetcher = _fetcher(lambda request: httpx.Response(200, text="x"))
    with pytest.raises(WebFetchError, match=message):
        fetcher.fetch(url)


def test_fetch_refuses_private_resolution_and_bad_redirects() -> None:
    internal = _fetcher(
        lambda request: httpx.Response(200, text="secret"),
        resolved={"docs.example.com": ["10.0.0.5"]},
    )
    with pytest.raises(WebFetchError, match="non-public"):
        internal.fetch("https://docs.example.com/")

    def redirect(request: httpx.Request) -> httpx.Response:
        if request.url.host == "docs.example.com":
            return httpx.Response(302, headers={"location": "https://metadata.internal/latest"})
        return httpx.Response(200, text="should not be read")

    with pytest.raises(WebFetchError, match="allowed_domains"):
        _fetcher(redirect).fetch("https://docs.example.com/")

    binary = _fetcher(
        lambda request: httpx.Response(200, content=b"\x00", headers={"content-type": "image/png"})
    )
    with pytest.raises(WebFetchError, match="not text"):
        binary.fetch("https://docs.example.com/logo.png")


def test_rebinding_is_caught_at_the_connected_address() -> None:
    class Stream:
        def get_extra_info(self, name: str):
            return ("127.0.0.1", 443) if name == "server_addr" else None

    fetcher = WebFetcher(WebFetchConfig(allowed_domains=["docs.example.com"]))
    response = httpx.Response(200, extensions={"network_stream": Stream()})
    with pytest.raises(WebFetchError, match="non-public"):
        fetcher._check_peer(response)


def test_web_fetch_tool_is_denied_by_default_policy(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    result = registry.execute(
        ToolCall(name="web.fetch", args={"url": "https://docs.example.com/"}, origin="model")
    )
    assert not result.ok and "denied by policy" in result.output


def test_web_fetch_tool_runs_when_allowed(tmp_path: Path) -> None:
    registry = _registry(tmp_path, web="allow")
    registry.web_fetcher_factory = lambda config: _fetcher(
        lambda request: httpx.Response(
            200, text="hello docs", headers={"content-type": "text/html"}
        )
    )
    result = registry.execute(
        ToolCall(name="web.fetch", args={"url": "https://docs.example.com/"}, origin="model")
    )
    assert result.ok and "hello docs" in result.output
    assert "Untrusted web content" in result.output
    assert result.metadata["web_host"] == "docs.example.com"


def test_catalog_offers_tools_only_when_policy_can_run_them() -> None:
    default = {schema.name for schema in tool_catalog(LoroConfig())}
    assert {"patch.apply", "tests.run"} <= default
    assert "web.fetch" not in default  # web is denied and no domains are allowlisted
    enabled = LoroConfig(
        permissions=PermissionsConfig(web="ask"),
        web_fetch=WebFetchConfig(allowed_domains=["docs.example.com"]),
    )
    assert "web.fetch" in {schema.name for schema in tool_catalog(enabled)}
