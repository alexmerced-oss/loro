"""Governed workspace, attachment, extension, and scheduling services for the Web UI."""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from loro.config import LoroConfig, MCPServerConfig, load_config, replace_config_section

MAX_INLINE_BYTES = 256_000
MAX_CONTEXT_BYTES = 750_000
MAX_UPLOAD_BYTES = 10_000_000
SKIP = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}


class ContextReference(BaseModel):
    path: str = Field(min_length=1, max_length=1_000)


class UploadInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", max_length=200)
    content_base64: str = Field(min_length=1, max_length=14_000_000)


class ScheduleInput(BaseModel):
    graph_path: str = Field(min_length=1, max_length=500)
    interval_minutes: int = Field(ge=1, le=525_600)
    enabled: bool = True


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_path(root: Path, raw: str, *, allow_internal_attachments: bool = False) -> Path:
    workspace = root.resolve()
    candidate = (workspace / raw).resolve()
    try:
        relative = candidate.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("Workspace paths must stay inside the active project.") from exc
    if any(part in SKIP for part in relative.parts):
        raise ValueError("That workspace path is excluded.")
    if relative.parts[:1] == (".loro",) and not (
        allow_internal_attachments and relative.parts[1:2] == ("attachments",)
    ):
        raise ValueError("Internal Loro state cannot be used as workspace context.")
    return candidate


class WorkspaceService:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def files(self, query: str = "", limit: int = 300) -> list[dict[str, Any]]:
        needle = query.strip().casefold()
        rows: list[dict[str, Any]] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            if any(part in SKIP for part in relative.parts):
                continue
            if relative.parts[:1] == (".loro",) and relative.parts[1:2] != ("attachments",):
                continue
            name = relative.as_posix()
            if needle and needle not in name.casefold():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            rows.append(
                {
                    "path": name,
                    "size": size,
                    "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                    "previewable": size <= MAX_INLINE_BYTES,
                }
            )
            if len(rows) >= max(1, min(limit, 500)):
                break
        return sorted(rows, key=lambda item: item["path"].casefold())

    def read(self, raw: str) -> tuple[bytes, str, str]:
        path = _safe_path(self.root, raw, allow_internal_attachments=True)
        if not path.is_file():
            raise FileNotFoundError(raw)
        if path.stat().st_size > 5_000_000:
            raise ValueError("Browser previews are limited to 5 MB.")
        return (
            path.read_bytes(),
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            path.name,
        )

    def save_upload(self, conversation_id: str, payload: UploadInput) -> dict[str, Any]:
        name = Path(payload.name).name
        if not name or name in {".", ".."}:
            raise ValueError("Upload name is invalid.")
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Upload content is not valid base64.") from exc
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("Attachments must be 10 MB or smaller.")
        folder = self.root / ".loro" / "attachments" / conversation_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{uuid4().hex[:10]}-{name}"
        path.write_bytes(content)
        return {
            "path": path.relative_to(self.root).as_posix(),
            "name": name,
            "size": len(content),
            "media_type": payload.media_type,
            "uploaded": True,
        }

    def context(self, references: list[ContextReference]) -> tuple[str, list[dict[str, Any]]]:
        blocks: list[str] = []
        manifest: list[dict[str, Any]] = []
        total = 0
        for reference in references:
            path = _safe_path(self.root, reference.path, allow_internal_attachments=True)
            if not path.is_file():
                raise FileNotFoundError(reference.path)
            relative = path.relative_to(self.root).as_posix()
            size = path.stat().st_size
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            item: dict[str, Any] = {"path": relative, "size": size, "media_type": media_type}
            text_like = media_type.startswith("text/") or media_type in {
                "application/json",
                "application/yaml",
                "application/xml",
            }
            if text_like and size <= MAX_INLINE_BYTES and total + size <= MAX_CONTEXT_BYTES:
                try:
                    body = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    item["delivery"] = "workspace_path"
                    blocks.append(f"- Binary attachment at `{relative}` ({media_type}).")
                else:
                    item["delivery"] = "inline"
                    total += size
                    blocks.append(f"### {relative}\n```\n{body}\n```")
            else:
                item["delivery"] = "workspace_path"
                blocks.append(f"- Attachment at `{relative}` ({size} bytes, {media_type}).")
            manifest.append(item)
        suffix = "\n\nGoverned workspace context:\n\n" + "\n\n".join(blocks) if blocks else ""
        return suffix, manifest

    def changes(self) -> dict[str, Any]:
        def git(*args: str) -> str:
            completed = subprocess.run(  # nosec B603 B607
                ["git", *args],
                cwd=self.root,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return completed.stdout[:1_000_000]

        return {
            "status": git("status", "--short"),
            "diff": git("diff", "--no-ext-diff", "--"),
            "staged_diff": git("diff", "--cached", "--no-ext-diff", "--"),
        }

    def artifacts(self) -> list[dict[str, Any]]:
        extensions = {
            ".md",
            ".txt",
            ".json",
            ".yaml",
            ".yml",
            ".csv",
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".html",
        }
        rows = [
            item
            for item in self.files(limit=500)
            if Path(item["path"]).suffix.lower() in extensions
        ]
        return sorted(rows, key=lambda item: item["path"], reverse=True)[:200]

    def extensions(self) -> dict[str, Any]:
        config = load_config(self.root)
        skills: list[dict[str, Any]] = []
        if config.skills.enabled:
            paths = [*config.skills.managed_paths]
            if config.skills.allow_user:
                paths.extend(config.skills.user_paths)
            if config.skills.allow_project:
                paths.extend(config.skills.project_paths)
            for raw in paths:
                base = Path(raw).expanduser()
                if not base.is_absolute():
                    base = self.root / base
                if not base.is_dir():
                    continue
                for skill in sorted(base.iterdir()):
                    if skill.is_dir() and (skill / "SKILL.md").is_file():
                        owned = self.root in skill.resolve().parents
                        text = (skill / "SKILL.md").read_text(encoding="utf-8") if owned else ""
                        description = ""
                        if owned:
                            match = re.search(r"(?m)^description:\s*[\"']?(.*?)[\"']?\s*$", text)
                            description = match.group(1) if match else ""
                        body = re.sub(r"^---[\s\S]*?---\s*", "", text).strip() if owned else ""
                        skills.append(
                            {
                                "name": skill.name,
                                "path": str(skill),
                                "enabled": True,
                                "editable": owned,
                                "description": description,
                                "body": body,
                            }
                        )
        return {
            "mcp_enabled": config.mcp.enabled,
            "mcp_servers": [
                {
                    "name": name,
                    "enabled": server.enabled,
                    "transport": server.transport,
                    "configured": bool(server.url or server.command),
                    "command": getattr(server, "command", None),
                    "args": getattr(server, "args", []),
                    "url": getattr(server, "url", None),
                    "cwd": getattr(server, "cwd", None),
                    "protocol_mode": getattr(server, "protocol_mode", "auto"),
                    "timeout_seconds": getattr(server, "timeout_seconds", 30),
                    "env_allowlist": getattr(server, "env_allowlist", []),
                    "extensions": server.extensions,
                }
                for name, server in config.mcp.servers.items()
            ],
            "mcp_extensions": [
                {"name": name, **extension.model_dump(exclude={"settings"})}
                for name, extension in config.mcp.extensions.items()
            ],
            "skills": skills[: config.skills.max_files],
        }

    def manage_extension(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create, update, or remove project-owned skills and MCP definitions."""
        kind = str(payload.get("kind") or "").strip()
        action = str(payload.get("action") or "save").strip()
        name = str(payload.get("name") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", name):
            raise ValueError(
                "Extension names must use lowercase letters, numbers, hyphens, or underscores."
            )
        if kind == "skill":
            root = (self.root / ".loro" / "skills").resolve()
            target = (root / name).resolve()
            target.relative_to(root)
            if action == "delete":
                if target.is_dir():
                    shutil.rmtree(target)
                return {"ok": True, "removed": name}
            description = str(payload.get("description") or "").strip()
            body = str(payload.get("body") or "").strip()
            if not description or not body:
                raise ValueError("Skill description and instructions are required.")
            target.mkdir(parents=True, exist_ok=True)
            (target / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n\n{body}\n",
                encoding="utf-8",
            )
            return {"ok": True, "saved": name}
        if kind == "mcp":
            config = load_config(self.root)
            servers = dict(config.mcp.servers)
            if action == "delete":
                servers.pop(name, None)
            else:
                raw = {
                    key: value
                    for key, value in payload.items()
                    if key in MCPServerConfig.model_fields
                }
                servers[name] = MCPServerConfig.model_validate(raw)
            updated = config.model_copy(
                update={
                    "mcp": config.mcp.model_copy(
                        update={"enabled": bool(servers), "servers": servers}
                    )
                }
            )
            validated = LoroConfig.model_validate(updated.model_dump())
            replace_config_section(self.root / ".loro" / "config.local.toml", validated, "mcp")
            return {"ok": True, "saved": name, "removed": action == "delete"}
        raise ValueError("Extension kind must be skill or mcp.")

    def workspaces(self) -> list[dict[str, Any]]:
        candidates = [self.root]
        try:
            candidates.extend(
                child
                for child in self.root.parent.iterdir()
                if child.is_dir() and (child / ".loro").is_dir()
            )
        except OSError:
            pass
        return [
            {
                "name": path.name,
                "path": str(path.resolve()),
                "active": path.resolve() == self.root,
                "launch_argv": ["loro", "web", "-C", str(path.resolve())],
            }
            for path in sorted(set(candidates))
        ]


class ScheduleStore:
    """Small interval scheduler; execution remains governed by GraphService."""

    def __init__(self, root: Path, start_graph: Callable[[str], Any]) -> None:
        self.path = root.resolve() / ".loro" / "webui-schedules.json"
        self.start_graph = start_graph
        self.lock = threading.Lock()

    def list(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return list(value) if isinstance(value, list) else []
        except (OSError, ValueError, TypeError):
            return []

    def _save(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def create(self, payload: ScheduleInput) -> dict[str, Any]:
        now = _now()
        record = {
            "id": f"schedule-{uuid4().hex}",
            "graph_path": payload.graph_path,
            "interval_minutes": payload.interval_minutes,
            "enabled": payload.enabled,
            "created_at": now.isoformat(),
            "next_run_at": (now + timedelta(minutes=payload.interval_minutes)).isoformat(),
            "last_run_at": None,
            "last_run_id": None,
            "last_error": None,
        }
        with self.lock:
            records = self.list()
            records.append(record)
            self._save(records)
        return record

    def update(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        with self.lock:
            records = self.list()
            record = next((item for item in records if item.get("id") == schedule_id), None)
            if record is None:
                raise KeyError("Schedule not found.")
            record["enabled"] = enabled
            if enabled:
                record["next_run_at"] = (
                    _now() + timedelta(minutes=int(record["interval_minutes"]))
                ).isoformat()
            self._save(records)
            return record

    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or _now()
        changed: list[dict[str, Any]] = []
        with self.lock:
            records = self.list()
            for record in records:
                if (
                    not record.get("enabled")
                    or datetime.fromisoformat(record["next_run_at"]) > current
                ):
                    continue
                try:
                    handle = self.start_graph(str(record["graph_path"]))
                    record["last_run_id"] = handle.run_id
                    record["last_error"] = None
                except Exception as exc:  # scheduler records the governed refusal
                    record["last_error"] = str(exc)
                record["last_run_at"] = current.isoformat()
                record["next_run_at"] = (
                    current + timedelta(minutes=int(record["interval_minutes"]))
                ).isoformat()
                changed.append(dict(record))
            if changed:
                self._save(records)
        return changed
