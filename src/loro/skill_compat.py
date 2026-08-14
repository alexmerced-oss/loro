from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

import yaml

from loro.config import (
    LoroConfig,
    MCPCredentialProfileConfig,
    MCPServerConfig,
    SkillsConfig,
)
from loro.skills import SKILL_NAME, SkillError, SkillMetadata, SkillRegistry, _package_digest

CompatibilityKind = Literal["claude", "pi"]
SkillScope = Literal["user", "project"]
_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$|^\$([A-Za-z_][A-Za-z0-9_]*)$")
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class SkillCompatibilityError(SkillError):
    """Raised when an external skill package cannot be imported safely."""


@dataclass(frozen=True)
class SkillImportCandidate:
    name: str
    description: str
    source: Path
    relative_source: str
    compatible: bool
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.relative_source,
            "compatible": self.compatible,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class MCPImportCandidate:
    name: str
    server: MCPServerConfig | None
    credential_profile: MCPCredentialProfileConfig | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def compatible(self) -> bool:
        return self.server is not None and not self.errors

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "compatible": self.compatible,
            "server": self.server.model_dump(exclude_none=True) if self.server else None,
            "credential_profile": (
                self.credential_profile.model_dump(exclude_none=True)
                if self.credential_profile
                else None
            ),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class CompatibilityReport:
    kind: CompatibilityKind
    source: Path
    digest: str
    skills: tuple[SkillImportCandidate, ...]
    mcp_servers: tuple[MCPImportCandidate, ...]
    unsupported_components: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def importable(self) -> bool:
        return (
            bool(self.skills) and not self.errors and all(item.compatible for item in self.skills)
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": str(self.source),
            "digest": self.digest,
            "importable": self.importable,
            "skills": [item.to_payload() for item in self.skills],
            "mcp_servers": [item.to_payload() for item in self.mcp_servers],
            "unsupported_components": list(self.unsupported_components),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def inspect_compatibility(
    source: Path,
    kind: CompatibilityKind,
    skills_config: SkillsConfig,
) -> CompatibilityReport:
    root = source.expanduser().resolve()
    if not root.is_dir():
        raise SkillCompatibilityError(f"Compatibility source must be a directory: {source}")
    digest = _tree_digest(root, skills_config)
    package = _read_json_file(root / "package.json") if kind == "pi" else {}
    manifest = _read_json_file(root / ".claude-plugin" / "plugin.json") if kind == "claude" else {}
    skill_paths = (
        _claude_skill_paths(root, manifest) if kind == "claude" else _pi_skill_paths(root, package)
    )
    candidates = tuple(_inspect_skill(root, path, kind) for path in skill_paths)
    duplicate_names = {
        item.name for item in candidates if sum(x.name == item.name for x in candidates) > 1
    }
    errors: list[str] = []
    if not candidates:
        errors.append("No SKILL.md packages were found.")
    if duplicate_names:
        errors.append("Imported skill names collide: " + ", ".join(sorted(duplicate_names)))
    unsupported = _unsupported_components(root, kind, manifest, package)
    mcp_payloads = _claude_mcp_payloads(root, manifest) if kind == "claude" else []
    mcp_servers = tuple(_translate_mcp_server(name, payload) for name, payload in mcp_payloads)
    duplicate_mcp_names = {
        item.name
        for item in mcp_servers
        if sum(other.name == item.name for other in mcp_servers) > 1
    }
    if duplicate_mcp_names:
        errors.append(
            "Imported MCP server names collide after normalization: "
            + ", ".join(sorted(duplicate_mcp_names))
        )
    return CompatibilityReport(
        kind=kind,
        source=root,
        digest=digest,
        skills=candidates,
        mcp_servers=mcp_servers,
        unsupported_components=tuple(unsupported),
        errors=tuple(errors),
    )


def import_compatible_skills(
    report: CompatibilityReport,
    registry: SkillRegistry,
    *,
    expected_digest: str,
    scope: SkillScope,
) -> list[SkillMetadata]:
    if report.digest != expected_digest:
        raise SkillCompatibilityError("Compatibility source digest does not match the review.")
    if _tree_digest(report.source, registry.config) != expected_digest:
        raise SkillCompatibilityError("Compatibility source changed after review.")
    if not report.importable:
        details = list(report.errors)
        details.extend(error for skill in report.skills for error in skill.errors)
        raise SkillCompatibilityError("Compatibility import is blocked: " + "; ".join(details))
    installed: list[SkillMetadata] = []
    try:
        with TemporaryDirectory(prefix="loro-skill-import-") as temporary:
            staging = Path(temporary)
            normalized_skills = [
                _normalize_skill(
                    candidate,
                    staging / candidate.name,
                    report.kind,
                    report.source,
                )
                for candidate in report.skills
            ]
            if _tree_digest(report.source, registry.config) != expected_digest:
                raise SkillCompatibilityError("Compatibility source changed while staging.")
            for normalized in normalized_skills:
                installed.append(
                    registry.install(
                        normalized,
                        expected_digest=_package_digest(normalized),
                        scope=scope,
                    )
                )
    except Exception:
        for skill in reversed(installed):
            shutil.rmtree(skill.path, ignore_errors=True)
        raise
    return installed


def apply_mcp_import(config: LoroConfig, report: CompatibilityReport) -> list[str]:
    if report.errors:
        raise SkillCompatibilityError(
            "Compatibility report is blocked: " + "; ".join(report.errors)
        )
    incompatible = [item.name for item in report.mcp_servers if not item.compatible]
    if incompatible:
        raise SkillCompatibilityError(
            "MCP import contains incompatible servers: " + ", ".join(incompatible)
        )
    collisions = sorted(set(config.mcp.servers) & {item.name for item in report.mcp_servers})
    if collisions:
        raise SkillCompatibilityError("MCP server names already exist: " + ", ".join(collisions))
    added: list[str] = []
    for item in report.mcp_servers:
        if item.server is None:
            continue
        server = item.server
        if item.credential_profile:
            profile_name = f"{item.name}-imported"
            if profile_name in config.mcp.credential_profiles:
                raise SkillCompatibilityError(
                    f"MCP credential profile already exists: {profile_name}"
                )
            config.mcp.credential_profiles[profile_name] = item.credential_profile
            server = server.model_copy(update={"credential_profile": profile_name})
        config.mcp.servers[item.name] = server
        added.append(item.name)
    if added:
        config.mcp.enabled = True
    return added


def _inspect_skill(root: Path, package: Path, kind: CompatibilityKind) -> SkillImportCandidate:
    warnings: list[str] = []
    errors: list[str] = []
    frontmatter, body = _parse_external_skill(package / "SKILL.md")
    raw_name = frontmatter.get("name")
    name = str(raw_name).strip() if isinstance(raw_name, str) else _normalized_name(package.name)
    if not raw_name:
        warnings.append(f"Missing name will be normalized to {name!r}.")
    elif SKILL_NAME.fullmatch(name) is None or len(name) > 64:
        normalized = _normalized_name(name)
        warnings.append(f"Skill name {name!r} will be normalized to {normalized!r}.")
        name = normalized
    description_value = frontmatter.get("description")
    description = (
        description_value.strip()
        if isinstance(description_value, str) and description_value.strip()
        else _derived_description(body, name, kind)
    )
    if not description_value:
        warnings.append("Missing description will use a conservative imported description.")
    if len(description) > 1024:
        description = description[:1024]
        warnings.append("Description will be truncated to 1024 characters.")
    allowed = frontmatter.get("allowed-tools")
    if allowed is not None and not (
        isinstance(allowed, str)
        or (isinstance(allowed, list) and all(isinstance(item, str) for item in allowed))
    ):
        errors.append("allowed-tools must be a string or list of strings.")
    compatibility = frontmatter.get("compatibility")
    if compatibility is not None and (
        not isinstance(compatibility, str) or not compatibility or len(compatibility) > 500
    ):
        errors.append("compatibility must be a non-empty string up to 500 characters.")
    license_value = frontmatter.get("license")
    if license_value is not None and (
        not isinstance(license_value, str) or not license_value.strip()
    ):
        errors.append("license must be a non-empty string.")
    extra_metadata = frontmatter.get("metadata")
    if extra_metadata is not None and (
        not isinstance(extra_metadata, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in extra_metadata.items()
        )
    ):
        errors.append("metadata must map string keys to string values.")
    if kind == "claude" and "${CLAUDE_PLUGIN_ROOT}" in body:
        warnings.append("Claude plugin-root references will resolve inside the imported skill.")
    if any(path.is_symlink() for path in package.rglob("*")):
        errors.append("Skill packages containing symlinks cannot be imported.")
    relative = "." if package == root else package.relative_to(root).as_posix()
    return SkillImportCandidate(
        name=name,
        description=description,
        source=package,
        relative_source=relative,
        compatible=not errors,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def _normalize_skill(
    candidate: SkillImportCandidate,
    destination: Path,
    kind: CompatibilityKind,
    package_root: Path,
) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    frontmatter, body = _parse_external_skill(candidate.source / "SKILL.md")
    excluded = _host_component_names(kind) if candidate.relative_source == "." else set()
    for source_path in candidate.source.rglob("*"):
        relative = source_path.relative_to(candidate.source)
        if not relative.parts or relative.parts[0] in excluded or relative.as_posix() == "SKILL.md":
            continue
        if source_path.is_symlink():
            raise SkillCompatibilityError("Imported skills cannot contain symlinks.")
        target = destination / relative
        if source_path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source_path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target)
    if kind == "claude" and candidate.source != package_root:
        for source_path in package_root.iterdir():
            if source_path.name in _host_component_names(kind) or source_path.is_symlink():
                continue
            target = destination / source_path.name
            if target.exists():
                continue
            if source_path.is_dir():
                shutil.copytree(source_path, target, symlinks=True)
            elif source_path.is_file():
                shutil.copy2(source_path, target)
    normalized: dict[str, Any] = {
        "name": candidate.name,
        "description": candidate.description,
    }
    for key in ("license", "compatibility", "metadata"):
        if key in frontmatter:
            normalized[key] = frontmatter[key]
    allowed = frontmatter.get("allowed-tools")
    if isinstance(allowed, list):
        normalized["allowed-tools"] = " ".join(allowed)
    elif isinstance(allowed, str):
        normalized["allowed-tools"] = allowed
    rendered = (
        "---\n" + yaml.safe_dump(normalized, sort_keys=False).strip() + "\n---\n\n" + body.lstrip()
    )
    (destination / "SKILL.md").write_text(rendered, encoding="utf-8")
    return destination


def _parse_external_skill(path: Path) -> tuple[dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SkillCompatibilityError(
            f"Imported SKILL.md must be readable UTF-8: {path}"
        ) from error
    if not text.startswith("---\n"):
        return {}, text
    closing = text.find("\n---\n", 4)
    if closing < 0:
        raise SkillCompatibilityError(f"Skill frontmatter is not closed: {path}")
    source = text[4:closing]
    try:
        if any(isinstance(event, yaml.events.AliasEvent) for event in yaml.parse(source)):
            raise SkillCompatibilityError("Imported skill frontmatter cannot contain YAML aliases.")
        metadata = yaml.safe_load(source) or {}
    except yaml.YAMLError as error:
        raise SkillCompatibilityError(f"Invalid imported skill frontmatter: {error}") from error
    if not isinstance(metadata, dict):
        raise SkillCompatibilityError("Imported skill frontmatter must be a mapping.")
    return metadata, text[closing + 5 :]


def _claude_skill_paths(root: Path, manifest: dict[str, Any]) -> list[Path]:
    paths: set[Path] = set()
    if (root / "SKILL.md").is_file():
        paths.add(root)
    paths.update(path.parent for path in (root / "skills").glob("*/SKILL.md"))
    for value in _as_list(manifest.get("skills")):
        target = _inside(root, value)
        if (target / "SKILL.md").is_file():
            paths.add(target)
        elif target.is_dir():
            paths.update(path.parent for path in target.glob("*/SKILL.md"))
    return sorted(paths)


def _pi_skill_paths(root: Path, package: dict[str, Any]) -> list[Path]:
    paths: set[Path] = set()
    if (root / "SKILL.md").is_file():
        paths.add(root)
    paths.update(path.parent for path in (root / "skills").glob("**/SKILL.md"))
    pi_manifest = package.get("pi") if isinstance(package.get("pi"), dict) else {}
    for value in _as_list(pi_manifest.get("skills")):
        target = _inside(root, value)
        if (target / "SKILL.md").is_file():
            paths.add(target)
        elif target.is_dir():
            paths.update(path.parent for path in target.glob("**/SKILL.md"))
    return sorted(paths)


def _claude_mcp_payloads(root: Path, manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    payloads: list[tuple[str, dict[str, Any]]] = []
    sources: list[Any] = []
    default = _read_json_file(root / ".mcp.json")
    if default:
        sources.append(default)
    value = manifest.get("mcpServers")
    if isinstance(value, dict):
        sources.append({"mcpServers": value})
    else:
        for path in _as_list(value):
            sources.append(_read_json_file(_inside(root, path)))
    for source in sources:
        servers = source.get("mcpServers", source) if isinstance(source, dict) else {}
        if isinstance(servers, dict):
            for name, payload in servers.items():
                if isinstance(name, str) and isinstance(payload, dict):
                    payloads.append((name, payload))
    return sorted(payloads, key=lambda item: item[0])


def _translate_mcp_server(raw_name: str, payload: dict[str, Any]) -> MCPImportCandidate:
    try:
        name = _normalized_identifier(raw_name)
    except SkillCompatibilityError as error:
        return MCPImportCandidate(name=raw_name, server=None, errors=(str(error),))
    warnings: list[str] = []
    errors: list[str] = []
    if name != raw_name:
        warnings.append(f"Server name {raw_name!r} will be normalized to {name!r}.")
    env_names: list[str] = []
    environment = payload.get("env", {})
    if environment and not isinstance(environment, dict):
        errors.append("MCP env must be a mapping.")
    elif isinstance(environment, dict):
        for key, value in environment.items():
            match = _ENV_REFERENCE.fullmatch(value) if isinstance(value, str) else None
            referenced = (match.group(1) or match.group(2)) if match else None
            if not isinstance(key, str) or referenced != key:
                errors.append(
                    "MCP env values must be same-name ${VARIABLE} references; "
                    "literals are rejected."
                )
                break
            env_names.append(key)
    credential_profile: MCPCredentialProfileConfig | None = None
    headers = payload.get("headers", {})
    if headers:
        if not isinstance(headers, dict) or set(headers) != {"Authorization"}:
            errors.append("Only an environment-backed Authorization bearer header can be imported.")
        else:
            authorization = headers.get("Authorization")
            match = re.fullmatch(r"Bearer \$\{([A-Za-z_][A-Za-z0-9_]*)\}", authorization or "")
            if not match:
                errors.append(
                    "Authorization must use Bearer ${VARIABLE}; literal headers are rejected."
                )
            else:
                credential_profile = MCPCredentialProfileConfig(
                    type="bearer", token_env=match.group(1)
                )
    server: MCPServerConfig | None = None
    try:
        url = payload.get("url")
        transport_type = str(payload.get("type") or "").casefold()
        if url or transport_type in {"http", "streamable-http", "streamable_http"}:
            if not isinstance(url, str):
                raise ValueError("Streamable HTTP MCP server requires a URL.")
            server = MCPServerConfig(transport="streamable_http", url=url)
        elif transport_type == "sse":
            raise ValueError(
                "Legacy SSE MCP transport is not imported; configure a Streamable HTTP endpoint."
            )
        else:
            command = payload.get("command")
            args = payload.get("args", [])
            if (
                not isinstance(command, str)
                or not isinstance(args, list)
                or not all(isinstance(item, str) for item in args)
            ):
                raise ValueError("Stdio MCP server requires a string command and string args.")
            cwd = str(payload["cwd"]) if payload.get("cwd") else None
            values = [command, *args, *([cwd] if cwd else [])]
            if any("${CLAUDE_PLUGIN_ROOT}" in value for value in values):
                raise ValueError(
                    "Plugin-local MCP executables are not imported; install a reviewed command "
                    "separately or use a remote Streamable HTTP endpoint."
                )
            server = MCPServerConfig(
                transport="stdio",
                command=command,
                args=args,
                cwd=cwd,
                env_allowlist=env_names,
            )
    except (TypeError, ValueError) as error:
        errors.append(str(error))
    return MCPImportCandidate(
        name=name,
        server=server if not errors else None,
        credential_profile=credential_profile,
        warnings=tuple(warnings),
        errors=tuple(dict.fromkeys(errors)),
    )


def _unsupported_components(
    root: Path,
    kind: CompatibilityKind,
    manifest: dict[str, Any],
    package: dict[str, Any],
) -> list[str]:
    components: list[str] = []
    names = _host_component_names(kind)
    for name in sorted(names):
        if name in {".claude-plugin", ".mcp.json", "package.json", "skills"}:
            continue
        if (root / name).exists():
            components.append(name)
    if kind == "claude":
        for key in ("agents", "commands", "hooks", "lspServers", "monitors", "outputStyles"):
            if manifest.get(key) and key not in components:
                components.append(key)
    else:
        pi_manifest = package.get("pi") if isinstance(package.get("pi"), dict) else {}
        for key in ("extensions", "prompts", "themes"):
            if pi_manifest.get(key) and key not in components:
                components.append(key)
    return sorted(set(components))


def _host_component_names(kind: CompatibilityKind) -> set[str]:
    if kind == "claude":
        return {
            ".claude-plugin",
            ".mcp.json",
            ".lsp.json",
            "agents",
            "bin",
            "commands",
            "hooks",
            "monitors",
            "output-styles",
            "scripts",
            "settings.json",
            "skills",
        }
    return {"extensions", "package.json", "prompts", "skills", "themes"}


def _tree_digest(root: Path, config: SkillsConfig) -> str:
    files = [path for path in root.rglob("*") if path.is_file() or path.is_symlink()]
    if len(files) > config.max_files:
        raise SkillCompatibilityError("Compatibility source exceeds managed file-count limit.")
    digest = hashlib.sha256()
    total = 0
    for path in sorted(files):
        if path.is_symlink():
            raise SkillCompatibilityError("Compatibility sources cannot contain symlinks.")
        total += path.stat().st_size
        if total > config.max_bytes:
            raise SkillCompatibilityError("Compatibility source exceeds managed byte limit.")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _inside(root: Path, value: str) -> Path:
    if not isinstance(value, str):
        raise SkillCompatibilityError("Plugin resource paths must be strings.")
    relative = Path(value.removeprefix("./"))
    if relative.is_absolute() or ".." in relative.parts:
        raise SkillCompatibilityError("Plugin resource paths must stay inside the source.")
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise SkillCompatibilityError("Plugin resource paths must stay inside the source.")
    return target


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SkillCompatibilityError(f"Invalid compatibility manifest {path}: {error}") from error
    if not isinstance(payload, dict):
        raise SkillCompatibilityError(f"Compatibility manifest must be an object: {path}")
    return payload


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise SkillCompatibilityError("Plugin resource paths must be a string or list of strings.")


def _normalized_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:64]
    return normalized or "imported-skill"


def _normalized_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-._")[:64]
    if not normalized or _SAFE_ID.fullmatch(normalized) is None:
        raise SkillCompatibilityError(f"Cannot normalize imported identifier: {value!r}")
    return normalized


def _derived_description(body: str, name: str, kind: CompatibilityKind) -> str:
    for line in body.splitlines():
        candidate = line.strip().lstrip("#").strip()
        if candidate:
            return f"Imported {kind} skill {name}: {candidate}"[:1024]
    return f"Imported {kind} skill {name}."
