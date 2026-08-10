import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from re import sub
from secrets import token_hex
from typing import Any


@dataclass(frozen=True)
class ArtifactResult:
    title: str
    kind: str
    paths: list[Path]
    summary: str


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
    payload: dict[str, Any] = {
        "title": result.title,
        "kind": result.kind,
        "paths": [str(item) for item in result.paths],
        "prompt_preview": prompt_preview,
        "assumptions": assumptions
        or [
            "Generated from user-approved prompt text.",
            "No external governed data was queried by this artifact generator.",
        ],
        "created_at": datetime.now(UTC).isoformat(),
        "generator": "loro.mvp.artifacts",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
