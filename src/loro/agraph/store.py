from __future__ import annotations

import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from loro.config import AGraphConfig
from loro.data_protection import DataProtectionEngine

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class GraphRunStore:
    """Bounded, atomic durable storage for AGS run records."""

    def __init__(self, config: AGraphConfig, protection: DataProtectionEngine) -> None:
        self.root = Path(config.state_path).expanduser().resolve()
        self.max_bytes = config.max_record_bytes
        self.protection = protection

    def save(self, record: dict[str, Any]) -> Path:
        run_id = self._safe_id(record.get("run_id"))
        protected = self.protection.enforce(
            json.dumps(record, ensure_ascii=True, sort_keys=True), "session"
        ).content
        payload = protected.encode("utf-8")
        if len(payload) > self.max_bytes:
            raise ValueError(f"graph run record exceeds {self.max_bytes} bytes")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{run_id}.json"
        with NamedTemporaryFile(dir=self.root, prefix=f".{run_id}-", delete=False) as stream:
            stream.write(payload)
            temp = Path(stream.name)
        os.chmod(temp, 0o600)
        temp.replace(target)
        return target

    def get(self, run_id: str) -> dict[str, Any]:
        path = self.root / f"{self._safe_id(run_id)}.json"
        if not path.is_file():
            raise FileNotFoundError(f"graph run not found: {run_id}")
        if path.stat().st_size > self.max_bytes:
            raise ValueError("stored graph run exceeds the configured record limit")
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("stored graph run is not an object")
        return result

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        return [self.get(path.stem) for path in sorted(self.root.glob("*.json"))]

    @staticmethod
    def _safe_id(value: object) -> str:
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise ValueError("invalid graph run id")
        return value
