import json
import os
import subprocess
import sys


def test_stdio_launcher_clears_unapproved_environment() -> None:
    environment = dict(os.environ)
    environment["MCP_ALLOWED"] = "present"
    environment["OPENAI_API_KEY"] = "must-not-leak"
    command = [
        sys.executable,
        "-m",
        "loro.mcp.stdio_launcher",
        "MCP_ALLOWED",
        "--",
        sys.executable,
        "-c",
        "import json, os; print(json.dumps(dict(os.environ)))",
    ]

    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    child_environment = json.loads(result.stdout)
    assert child_environment["MCP_ALLOWED"] == "present"
    assert "OPENAI_API_KEY" not in child_environment
    assert "HOME" not in child_environment


def test_stdio_launcher_rejects_missing_environment() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loro.mcp.stdio_launcher",
            "MISSING_VALUE",
            "--",
            sys.executable,
            "-V",
        ],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "MISSING_VALUE" in result.stderr
