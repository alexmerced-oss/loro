"""Read-only capability negotiation for desktop and broker clients."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

from loro import __version__


def capability_report() -> dict[str, Any]:
    reasons: list[str] = []
    package = importlib.util.find_spec("playwright") is not None
    browser = False
    if package:
        try:
            from playwright._impl._driver import compute_driver_executable

            node, cli = compute_driver_executable()
            script = (
                "process.stdout.write(require("
                + json.dumps(str(Path(cli).parent))
                + ").chromium.executablePath())"
            )
            # Installed Playwright driver and fixed script; no user command or shell.
            result = subprocess.run(  # nosec B603
                [node, "-e", script], capture_output=True, text=True, timeout=3, check=True
            )
            browser = Path(result.stdout).is_file()
        except Exception:
            reasons.append("Browser driver could not be inspected.")
    if not package:
        reasons.append("Install the webmcp optional dependency pack.")
    if not browser:
        reasons.append("Install the Playwright Chromium browser.")
    try:
        from loro.webmcp_bridge import normalize_webmcp_origins

        origins = normalize_webmcp_origins()
    except (ValueError, RuntimeError):
        origins = ()
        reasons.append("Configure at least one valid exact HTTPS origin.")
    return {
        "schema": "agent-runtime.capabilities.v1",
        "harness": "loro",
        "version": __version__,
        "supported": {"aais": True, "webmcp": True, "oap": "1.0", "ags": "1.0"},
        "ready": {"webmcp": bool(package and browser and origins)},
        "webmcp": {
            "package_installed": package,
            "browser_installed": browser,
            "origins": list(origins),
            "registry_status": "not_checked",
            "reasons": reasons,
        },
        "limits": [
            "Live registry, credentials and profile policy are checked by the harness at execution."
        ],
    }
