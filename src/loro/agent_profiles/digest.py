from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def profile_digest(document: dict[str, Any]) -> str:
    return _digest(document)


def spec_digest(document: dict[str, Any]) -> str:
    metadata = dict(document.get("metadata", {}))
    for mutable in ("trust", "revision", "updatedAt", "updated_at"):
        metadata.pop(mutable, None)
    return _digest({"metadata": metadata, "spec": document.get("spec", {})})
