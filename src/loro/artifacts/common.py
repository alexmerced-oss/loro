import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from re import sub
from secrets import token_hex
from typing import Any

from loro.fileio import atomic_write_text


@dataclass(frozen=True)
class ArtifactResult:
    title: str
    kind: str
    paths: list[Path]
    summary: str


@dataclass(frozen=True)
class ProvenanceVerification:
    path: Path
    ok: bool
    artifacts: list[dict[str, Any]]
    issues: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "path": str(self.path),
            "ok": self.ok,
            "artifacts": self.artifacts,
            "issues": self.issues,
        }


def slugify(value: str, fallback: str = "artifact") -> str:
    normalized = sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or fallback


def unique_slug(value: str, fallback: str = "artifact") -> str:
    """Slug with a timestamp and random suffix so concurrent generations never collide.

    Two artifacts built from similar prompts in the same second used to resolve to the
    same filename and silently overwrite each other, including the provenance sidecar.
    """

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{slugify(value, fallback)}-{stamp}-{token_hex(3)}"


def formula_safe(value: str) -> str:
    """Neutralize leading characters that spreadsheet apps treat as a live formula."""

    if value[:1] in {"=", "+", "-", "@"}:
        return f"'{value}"
    return value


def ensure_output_dir(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def title_from_prompt(prompt: str, fallback: str) -> str:
    cleaned = " ".join(prompt.strip().split())
    if not cleaned:
        return fallback
    return cleaned[:80].rstrip(" .")


def write_provenance(
    *,
    result: ArtifactResult,
    prompt_preview: str,
    assumptions: list[str] | None = None,
) -> Path:
    path = result.paths[0].with_suffix(result.paths[0].suffix + ".provenance.json")
    artifacts = [_artifact_binding(item) for item in result.paths]
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "title": result.title,
        "kind": result.kind,
        "paths": [item["path"] for item in artifacts],
        "artifacts": artifacts,
        "prompt_preview": prompt_preview,
        "assumptions": assumptions
        or [
            "Generated from user-approved prompt text.",
            "No external governed data was queried by this artifact generator.",
        ],
        "created_at": datetime.now(UTC).isoformat(),
        "generator": "loro.artifacts",
    }
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def verify_provenance(path: Path) -> ProvenanceVerification:
    resolved = path.expanduser().resolve(strict=False)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return ProvenanceVerification(resolved, False, [], [f"invalid provenance: {error}"])
    if not isinstance(payload, dict):
        return ProvenanceVerification(
            resolved,
            False,
            [],
            ["provenance root must be an object"],
        )
    bindings = payload.get("artifacts")
    if payload.get("schema_version") != "1.0" or not isinstance(bindings, list):
        return ProvenanceVerification(
            resolved,
            False,
            [],
            ["provenance must use schema 1.0 with an artifacts list"],
        )
    checked: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            issues.append(f"artifact {index} is not an object")
            continue
        artifact_path = Path(str(binding.get("path", ""))).expanduser()
        expected_digest = str(binding.get("sha256", ""))
        expected_bytes = binding.get("bytes")
        if not artifact_path.is_absolute():
            artifact_path = (resolved.parent / artifact_path).resolve(strict=False)
        item: dict[str, Any] = {
            "path": str(artifact_path),
            "expected_sha256": expected_digest,
            "expected_bytes": expected_bytes,
            "ok": False,
        }
        if not artifact_path.is_file():
            issues.append(f"artifact missing: {artifact_path}")
        else:
            actual_digest, actual_bytes = _sha256_file(artifact_path)
            item.update({"actual_sha256": actual_digest, "actual_bytes": actual_bytes})
            item["ok"] = (
                expected_digest == actual_digest
                and isinstance(expected_bytes, int)
                and not isinstance(expected_bytes, bool)
                and expected_bytes == actual_bytes
            )
            if not item["ok"]:
                issues.append(f"artifact binding mismatch: {artifact_path}")
        checked.append(item)
    if not checked:
        issues.append("provenance contains no artifact bindings")
    return ProvenanceVerification(resolved, not issues, checked, issues)


def _artifact_binding(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=True)
    digest, size = _sha256_file(resolved)
    return {"path": str(resolved), "bytes": size, "sha256": digest}


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return "sha256:" + digest.hexdigest(), size
