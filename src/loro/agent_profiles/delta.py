from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from loro.agent_profiles.compat import canonical_document
from loro.agent_profiles.digest import profile_digest, spec_digest
from loro.agent_profiles.errors import ConflictError, ProfileError
from loro.agent_profiles.models import AgentProfileModel, AgentStateDelta, HistoryEntry, StateEntry
from loro.agent_profiles.registry import load_path
from loro.config import AgentProfilesConfig, SafetyConfig
from loro.data_protection import DataProtectionEngine
from loro.fileio import atomic_write_text, file_lock


def apply_delta(
    path: Path,
    delta: AgentStateDelta,
    config: AgentProfilesConfig,
    safety: SafetyConfig,
    event_handler: Callable[[str, dict[str, Any]], None] | None = None,
) -> AgentProfileModel:
    try:
        with file_lock(path):
            document = load_path(path, max_bytes=config.max_bytes)
            if document.metadata.name != delta.profile:
                raise ProfileError("Delta profile name does not match target.")
            if document.metadata.revision != delta.base_revision:
                raise ConflictError(
                    f"Profile revision conflict: expected {delta.base_revision}, "
                    f"found {document.metadata.revision}."
                )
            if spec_digest(document) != delta.spec_digest:
                raise ConflictError("Profile spec digest changed since delta creation.")
            protection = DataProtectionEngine(safety)
            entries = {item.id: item for item in document.state}
            for operation in delta.operations:
                if not (operation.path == "/state" or operation.path.startswith("/state/")):
                    raise ProfileError(f"operation path {operation.path!r} is outside /state")
                _apply_operation(entries, operation.op, operation.path, operation.value, protection)
            document.state = list(entries.values())
            document.state = _enforce_retention(document.state, config.max_state_bytes)
            old_revision = document.metadata.revision
            document.metadata.revision += 1
            updated_at = datetime.now(UTC).isoformat()
            document.metadata.updated_at = updated_at
            if document.canonical_source is None:
                document.canonical_source = {}
            document.canonical_source.setdefault("state", {})["updated_at"] = updated_at
            document.history.append(
                HistoryEntry(
                    revision=old_revision,
                    session_id=delta.session_id,
                    timestamp=updated_at,
                    digest=profile_digest(document),
                )
            )
            _write_profile(path, document, config.max_bytes)
        if event_handler is not None:
            event_handler(
                "agent_profile.delta_applied",
                {
                    "profile": delta.profile,
                    "old_revision": old_revision,
                    "new_revision": document.metadata.revision,
                    "session_id": delta.session_id,
                },
            )
        return document
    except (ProfileError, OSError, ValueError) as error:
        if event_handler is not None:
            event_handler(
                "agent_profile.delta_rejected",
                {
                    "profile": delta.profile,
                    "base_revision": delta.base_revision,
                    "session_id": delta.session_id,
                    "reason": str(error),
                },
            )
        raise


def create_delta(
    profile: AgentProfileModel,
    spec_hash: str,
    content: str,
    *,
    session_id: str | None = None,
) -> AgentStateDelta:
    slug = re.sub(r"[^a-z0-9]+", "-", content.casefold()).strip("-")[:48] or "entry"
    entry = {"id": slug, "content": content, "updated_at": datetime.now(UTC).isoformat()}
    return AgentStateDelta(
        profile=profile.metadata.name,
        base_revision=profile.metadata.revision,
        spec_digest=spec_hash,
        session_id=session_id,
        operations=[{"op": "add", "path": f"/state/{slug}", "value": entry}],
    )


def _apply_operation(
    entries: dict[str, StateEntry],
    op: str,
    path: str,
    value: Any,
    protection: DataProtectionEngine,
) -> None:
    entry_id = path.removeprefix("/state/")
    if not entry_id or "/" in entry_id:
        raise ProfileError("State operations must target /state/ENTRY_ID.")
    if op == "remove":
        entries.pop(entry_id, None)
        return
    if not isinstance(value, dict):
        raise ProfileError("State add/replace value must be an object.")
    value = dict(value)
    value["id"] = entry_id
    value["content"] = protection.enforce(str(value.get("content", "")), "agent_profile").content
    if op == "replace" and entry_id not in entries:
        raise ProfileError(f"Cannot replace missing state entry: {entry_id}")
    entries[entry_id] = StateEntry.model_validate(value)


def _write_profile(path: Path, document: AgentProfileModel, max_bytes: int) -> None:
    payload = canonical_document(document)
    _enforce_lifecycle_retention(payload)
    if path.name.endswith(".agent.json"):
        content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    elif path.name.endswith(".agent.md"):
        body = payload["spec"]["role"].pop("instructions", "")
        content = "---\n" + yaml.safe_dump(payload, sort_keys=False) + "---\n\n" + body + "\n"
    else:
        content = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    if len(content.encode("utf-8")) > max_bytes:
        raise ProfileError("Updated agent profile exceeds managed size limit.")
    atomic_write_text(path, content)


def _enforce_lifecycle_retention(payload: dict[str, Any]) -> None:
    lifecycle = payload.get("spec", {}).get("lifecycle", {})
    retention = lifecycle.get("retention", {}) if isinstance(lifecycle, dict) else {}
    facts = payload.get("state", {}).get("facts", [])
    limit = retention.get("max_facts")
    if isinstance(limit, int) and isinstance(facts, list):
        while len(facts) > limit:
            index = next(
                (
                    i
                    for i, item in enumerate(facts)
                    if not isinstance(item, dict) or not item.get("pinned")
                ),
                None,
            )
            if index is None:
                raise ProfileError("Pinned facts exceed lifecycle.retention.max_facts.")
            facts.pop(index)
    history = payload.get("history", [])
    max_history = retention.get("max_history", 50)
    if isinstance(history, list) and isinstance(max_history, int):
        payload["history"] = history[-max_history:] if max_history else []


def _enforce_retention(entries: list[StateEntry], max_bytes: int) -> list[StateEntry]:
    def size(items: list[StateEntry]) -> int:
        return sum(len(item.content.encode("utf-8")) for item in items)

    kept = list(entries)
    while size(kept) > max_bytes:
        index = next((index for index, item in enumerate(kept) if not item.pinned), None)
        if index is None:
            raise ProfileError("Pinned agent state exceeds managed state limit.")
        kept.pop(index)
    return kept
