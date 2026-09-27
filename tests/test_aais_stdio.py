"""The `--approval-stdio` transport: requests out on stdout, decisions in on stdin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from aais import create_decision

SOURCE = textwrap.dedent(
    """
    import json
    from pathlib import Path

    from loro.aais_stdio import create_stdio_provider
    from loro.approvals import ApprovalRequest

    provider = create_stdio_provider(Path.cwd())
    request = ApprovalRequest(
        action="shell.run",
        target="command",
        arguments={"command": "echo ok"},
        identity_subject="tester",
        identity_tenant="test",
        identity_session_id="stdio-test",
        policy_decision="ask",
        policy_version="1",
        policy_source="test",
        policy_reason="Shell commands ask.",
        risk_reason="Runs a command.",
    )
    print(json.dumps({"response": provider(request)}), flush=True)
    """
)


def test_stdio_decision_is_attributed_to_the_stdio_channel(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "home" / ".config")
    env["XDG_STATE_HOME"] = str(tmp_path / "home" / ".local" / "state")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    child = subprocess.Popen(
        [sys.executable, "-c", SOURCE],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdin is not None
        requested = None
        for line in child.stdout:
            envelope = json.loads(line)
            if envelope.get("type") == "approval.requested":
                requested = envelope
                break
        assert requested is not None, child.stderr.read() if child.stderr else ""
        decided = create_decision(
            requested,
            decision="approve",
            scope="once",
            actor={"id": "presenter-user", "type": "human", "authenticated_by": "presenter"},
            sequence=1,
            stream="presenter",
        )
        child.stdin.write(json.dumps(decided) + "\n")
        child.stdin.flush()
        response = None
        for line in child.stdout:
            payload = json.loads(line)
            if "response" in payload:
                response = payload["response"]
                break
        assert response == "once"
    finally:
        if child.stdin:
            child.stdin.close()
        child.wait(timeout=30)

    state = json.loads((tmp_path / ".loro" / "aais-approvals.json").read_text(encoding="utf-8"))
    [stored] = state["decisions"].values()
    assert stored["decision"]["id"] == decided["decision"]["id"]
    assert stored["decision"]["actor"] == {
        "id": "stdio-user",
        "type": "human",
        "authenticated_by": "loro-aais-stdio",
    }
    [resolution] = state["resolutions"].values()
    assert resolution["resolution"]["outcome"] == "approved"
