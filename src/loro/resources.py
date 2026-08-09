from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ResourceNormalizationError(PermissionError):
    """Raised when a requested resource cannot be normalized safely."""


@dataclass(frozen=True)
class NormalizedResource:
    kind: str
    fields: dict[str, Any]

    @property
    def target(self) -> str:
        return json.dumps(
            {"kind": self.kind, **self.fields},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def to_payload(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.fields}


def filesystem_resource(
    path: str | Path,
    *,
    operation: str,
    workspace_roots: list[str] | tuple[str, ...] = (),
    cwd: Path | None = None,
) -> NormalizedResource:
    requested = Path(path).expanduser()
    base = (cwd or Path.cwd()).expanduser().resolve(strict=False)
    absolute = requested if requested.is_absolute() else base / requested
    resolved = absolute.resolve(strict=False)
    roots = [_resolve_root(root, base) for root in workspace_roots]
    matched_root = next((root for root in roots if _is_within(resolved, root)), None)
    if roots and matched_root is None:
        raise ResourceNormalizationError(
            f"Filesystem resource is outside configured workspace roots: {resolved}"
        )
    return NormalizedResource(
        kind="filesystem",
        fields={
            "operation": operation,
            "path": str(resolved),
            "workspace_root": str(matched_root) if matched_root else "",
        },
    )


def shell_resource(args: list[str], *, operation: str = "run") -> NormalizedResource:
    if not args or not all(isinstance(item, str) and item for item in args):
        raise ResourceNormalizationError("Shell arguments must be non-empty strings.")
    if any("\x00" in item for item in args):
        raise ResourceNormalizationError("Shell arguments cannot contain NUL bytes.")
    executable_input = args[0]
    if os.sep in executable_input or (os.altsep and os.altsep in executable_input):
        executable = str(Path(executable_input).expanduser().resolve(strict=False))
    else:
        executable = shutil.which(executable_input) or executable_input
    return NormalizedResource(
        kind="shell",
        fields={
            "operation": operation,
            "executable": executable,
            "executable_name": Path(executable_input).name,
            "resolved_executable_name": Path(executable).name,
            "arguments": args[1:],
            "command": shlex.join(args),
        },
    )


def git_resource(
    *,
    operation: str,
    cwd: str | Path,
    workspace_roots: list[str] | tuple[str, ...] = (),
    paths: list[str] | None = None,
    revision: str | None = None,
    message: str | None = None,
) -> NormalizedResource:
    repository_scope = filesystem_resource(
        cwd,
        operation="git repository",
        workspace_roots=workspace_roots,
    )
    repository = Path(str(repository_scope.fields["path"]))
    normalized_paths = [
        str(
            filesystem_resource(
                path,
                operation="git path",
                workspace_roots=[str(repository)],
                cwd=repository,
            ).fields["path"]
        )
        for path in (paths or [])
    ]
    fields: dict[str, Any] = {
        "operation": operation,
        "repository": str(repository),
        "workspace_root": repository_scope.fields["workspace_root"],
        "paths": normalized_paths,
    }
    if revision is not None:
        fields["revision"] = revision
    if message is not None:
        fields["message"] = message
    return NormalizedResource(kind="git", fields=fields)


def memory_resource(
    *,
    operation: str,
    tenant: str,
    scope_type: str = "",
    scope_key: str = "",
    backend: str = "",
) -> NormalizedResource:
    return NormalizedResource(
        kind="memory",
        fields={
            "operation": operation,
            "tenant": tenant,
            "scope_type": scope_type,
            "scope_key": scope_key,
            "backend": backend,
        },
    )


def polaris_resource(
    args: list[str],
    *,
    default_catalog: str | None = None,
) -> NormalizedResource:
    if not args or not all(isinstance(item, str) and item for item in args):
        raise ResourceNormalizationError("Polaris arguments must be non-empty strings.")
    values = _option_values(args)
    operation = ".".join(args[:2]) if len(args) > 1 else args[0]
    positional = [item for item in args[2:] if not item.startswith("--")]
    return NormalizedResource(
        kind="polaris",
        fields={
            "operation": operation,
            "catalog": values.get("catalog", default_catalog or ""),
            "namespace": values.get("namespace", ""),
            "table": values.get("table", ""),
            "resource": values.get("resource", positional[0] if positional else ""),
            "role": values.get("catalog-role", values.get("principal-role", "")),
            "policy": values.get("policy", ""),
            "arguments": args,
        },
    )


def provider_resource(
    *,
    operation: str,
    provider: str,
    model: str,
    base_url: str | None = None,
) -> NormalizedResource:
    return NormalizedResource(
        kind="provider",
        fields={
            "operation": operation,
            "provider": provider,
            "model": model,
            "base_url": base_url or "",
        },
    )


def mcp_resource(
    *,
    operation: str,
    server_id: str,
    transport: str,
    endpoint: str,
    name: str = "",
    arguments: Mapping[str, Any] | None = None,
) -> NormalizedResource:
    canonical_arguments = json.dumps(
        dict(arguments or {}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return NormalizedResource(
        kind="mcp",
        fields={
            "operation": operation,
            "server_id": server_id,
            "transport": transport,
            "endpoint": endpoint,
            "name": name,
            "argument_names": sorted((arguments or {}).keys()),
            "arguments_digest": hashlib.sha256(canonical_arguments.encode("utf-8")).hexdigest(),
        },
    )


def resource_from_payload(payload: Mapping[str, Any]) -> NormalizedResource:
    kind = payload.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise ResourceNormalizationError("Resource fixture requires a non-empty kind.")
    return NormalizedResource(
        kind=kind.strip(),
        fields={str(key): value for key, value in payload.items() if key != "kind"},
    )


def _resolve_root(root: str, base: Path) -> Path:
    candidate = Path(root).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    normalized_path = os.path.normcase(str(path))
    normalized_root = os.path.normcase(str(root))
    try:
        return os.path.commonpath([normalized_path, normalized_root]) == normalized_root
    except ValueError:
        return False


def _option_values(args: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    index = 0
    while index < len(args):
        item = args[index]
        if item.startswith("--") and index + 1 < len(args):
            values[item.removeprefix("--")] = args[index + 1]
            index += 2
            continue
        index += 1
    return values
