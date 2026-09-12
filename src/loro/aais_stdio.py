"""Stdio transport for the Loro AAIS authority, separate from CLI dispatch."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from loro.aais_bridge import AAISBridge
from loro.approvals import ApprovalRequest, ApprovalScope
from loro.config import load_config


def create_stdio_provider(project_root: Path) -> Callable[[ApprovalRequest], ApprovalScope | None]:
    import signal

    bridge = AAISBridge(project_root)
    cancelled = threading.Event()

    def read_decisions() -> None:
        try:
            for line in sys.stdin:
                try:
                    from aais import validate

                    envelope = validate(json.loads(line))
                    if envelope.get("type") != "approval.decided":
                        continue
                    decision = envelope["decision"]
                    bridge.decide(
                        str(decision["request_id"]),
                        decision=str(decision["decision"]),
                        scope=str(decision["scope"]),
                        actor_id="stdio-user",
                        reviewed_digest=str(decision["action_digest"]),
                        decision_id=str(decision.get("id") or "") or None,
                    )
                except Exception as error:
                    print(
                        json.dumps({"type": "aais.error", "error": str(error)}),
                        file=sys.stderr,
                        flush=True,
                    )
        finally:
            cancelled.set()

    def terminate(_signum, _frame) -> None:
        cancelled.set()
        bridge.cancel_active()
        raise SystemExit(143)

    threading.Thread(target=read_decisions, daemon=True, name="loro-aais-stdin").start()
    signal.signal(signal.SIGTERM, terminate)

    def provider(request):
        return bridge.request(
            request,
            origin={},
            publish=lambda _event, envelope: print(json.dumps(envelope), flush=True),
            allow_session=load_config().approvals.allow_session_scope,
            cancelled=cancelled,
        )

    return provider
