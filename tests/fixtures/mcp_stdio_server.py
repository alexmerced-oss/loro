import json
import os
import sys


def response(request: dict[str, object]) -> dict[str, object] | None:
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        result: dict[str, object] = {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "loro-stdio-sandbox-fixture", "version": "1.0"},
        }
    elif method == "tools/call":
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(dict(os.environ), sort_keys=True),
                }
            ],
            "isError": False,
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "environment",
                    "description": "Return the fixture child environment.",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        }
    else:
        result = {}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


for line in sys.stdin:
    payload = response(json.loads(line))
    if payload is not None:
        print(json.dumps(payload), flush=True)
