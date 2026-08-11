from __future__ import annotations

import argparse
import json
import os

from loro.models import smoke_model_client
from loro.providers import model_config_from_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Run content-free protected provider smoke tests.")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    config = model_config_from_profile(args.provider, model=args.model)
    key_name = config.api_key_env
    if key_name and not os.environ.get(key_name):
        print(json.dumps({"provider": args.provider, "model": args.model, "status": "skipped"}))
        return 0
    result = smoke_model_client(
        config,
        prompt="Reply with exactly: loro-conformance-ok",
        execute=True,
        stream=args.stream,
    )
    content = str(result.get("content", "")).strip().casefold()
    ok = bool(result.get("ok")) and "loro-conformance-ok" in content
    print(
        json.dumps(
            {
                "provider": args.provider,
                "model": args.model,
                "stream": args.stream,
                "status": "passed" if ok else "failed",
            },
            sort_keys=True,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
