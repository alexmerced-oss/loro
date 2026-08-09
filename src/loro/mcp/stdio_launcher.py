"""Clear SDK-added environment variables before executing an MCP stdio server."""

from __future__ import annotations

import os
import re
import sys


def main() -> None:
    try:
        separator = sys.argv.index("--", 1)
    except ValueError as error:
        raise SystemExit("MCP stdio launcher requires '--' before the command.") from error
    names = sys.argv[1:separator]
    command = sys.argv[separator + 1 :]
    if not command:
        raise SystemExit("MCP stdio launcher requires a command.")
    if any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None for name in names):
        raise SystemExit("MCP stdio launcher received an invalid environment name.")
    missing = [name for name in names if name not in os.environ]
    if missing:
        raise SystemExit("MCP stdio launcher is missing environment: " + ", ".join(missing))
    environment = {name: os.environ[name] for name in names}
    # This launcher intentionally replaces itself after sandbox policy validates the argv.
    os.execve(command[0], command, environment)  # nosec B606


if __name__ == "__main__":
    main()
