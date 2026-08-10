from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml

from loro.config import SkillsConfig

SkillScope = Literal["managed", "user", "project"]
SkillState = Literal["enabled", "disabled", "quarantined"]
SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    path: Path
    scope: SkillScope
    trust: str
    digest: str
    state: SkillState
    allowed_tools: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "scope": self.scope,
            "trust": self.trust,
            "digest": self.digest,
            "state": self.state,
            "allowed_tools": list(self.allowed_tools),
        }


@dataclass(frozen=True)
class LoadedSkill:
    metadata: SkillMetadata
    instructions: str


class SkillRegistry:
    def __init__(self, config: SkillsConfig) -> None:
        self.config = config
        self.state = _read_json(Path(config.state_path).expanduser(), default={})

    def discover(self) -> list[SkillMetadata]:
        if not self.config.enabled:
            return []
        found: dict[str, SkillMetadata] = {}
        for scope, root in self._roots():
            if not root.exists():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                metadata = self._metadata(skill_file.parent, scope)
                if metadata.name in found:
                    previous = found[metadata.name]
                    raise SkillError(
                        f"Skill name collision for {metadata.name}: "
                        f"{previous.path} and {metadata.path}"
                    )
                found[metadata.name] = metadata
        return sorted(found.values(), key=lambda item: item.name)

    def get(self, name: str, *, require_enabled: bool = False) -> SkillMetadata:
        skill = next((item for item in self.discover() if item.name == name), None)
        if skill is None:
            raise SkillError(f"Skill not found: {name}")
        if require_enabled and skill.state != "enabled":
            raise SkillError(f"Skill is {skill.state}: {name}")
        return skill

    def load(self, name: str) -> LoadedSkill:
        metadata = self.get(name, require_enabled=True)
        skill_file = metadata.path / "SKILL.md"
        _, body = _parse_skill_file(skill_file)
        if len(body.encode("utf-8")) > self.config.max_instruction_bytes:
            raise SkillError(f"Skill instructions exceed managed limit: {name}")
        instructions = body.strip()
        skill_root = str(metadata.path)
        for placeholder in ("${LORO_SKILL_ROOT}", "${CLAUDE_PLUGIN_ROOT}", "{baseDir}"):
            instructions = instructions.replace(placeholder, skill_root)
        return LoadedSkill(metadata=metadata, instructions=instructions)

    def select(self, prompt: str) -> list[LoadedSkill]:
        explicit = re.findall(r"(?im)^\s*@skill\s+([a-z0-9][a-z0-9-]*)\s*$", prompt)
        if explicit:
            names = list(dict.fromkeys(explicit))
        else:
            words = set(re.findall(r"[a-z0-9]+", prompt.casefold()))
            candidates: list[tuple[int, str]] = []
            for skill in self.discover():
                if skill.state != "enabled":
                    continue
                terms = set(
                    re.findall(r"[a-z0-9]+", f"{skill.name} {skill.description}".casefold())
                )
                score = len(words & terms)
                if score:
                    candidates.append((score, skill.name))
            names = [name for _, name in sorted(candidates, key=lambda item: (-item[0], item[1]))]
        return [self.load(name) for name in names[: self.config.max_active]]

    def set_state(self, name: str, state: SkillState) -> SkillMetadata:
        skill = self.get(name)
        self.state[name] = {"state": state, "digest": skill.digest}
        _write_json(Path(self.config.state_path).expanduser(), self.state)
        return self.get(name)

    def read_supporting_file(self, name: str, relative_path: str) -> str:
        target = self.supporting_path(name, relative_path)
        content = target.read_bytes()
        if len(content) > self.config.max_bytes:
            raise SkillError("Skill supporting file exceeds managed size limit.")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SkillError("Skill supporting file is not UTF-8 text.") from error

    def supporting_path(self, name: str, relative_path: str) -> Path:
        skill = self.get(name, require_enabled=True)
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise SkillError("Skill supporting path must stay inside the package.")
        if relative.as_posix() == "SKILL.md":
            raise SkillError("Use skill loading rather than reading SKILL.md as a supporting file.")
        target = skill.path / relative
        _reject_symlink_path(skill.path, target)
        if not target.is_file():
            raise SkillError(f"Skill supporting file not found: {relative_path}")
        return target

    def install(
        self, source: Path, *, expected_digest: str, scope: SkillScope = "project"
    ) -> SkillMetadata:
        source = source.expanduser().resolve()
        candidate = self._metadata(source, scope)
        if candidate.digest != expected_digest:
            raise SkillError("Skill digest does not match the reviewed package.")
        destination_root = self._install_root(scope)
        destination = destination_root / candidate.name
        if destination.exists():
            raise SkillError(f"Skill destination already exists: {destination}")
        destination_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=False)
        try:
            installed = self._metadata(destination, scope)
            if installed.digest != expected_digest:
                raise SkillError("Installed skill digest changed during package copy.")
            return installed
        except Exception:
            shutil.rmtree(destination)
            raise

    def remove(self, name: str) -> Path:
        skill = self.get(name)
        if skill.scope == "managed":
            raise SkillError("Managed skills cannot be removed locally.")
        shutil.rmtree(skill.path)
        self.state.pop(name, None)
        _write_json(Path(self.config.state_path).expanduser(), self.state)
        return skill.path

    def propose(self, source: Path) -> Path:
        source = source.expanduser().resolve()
        candidate = self._metadata(source, "project")
        proposal = Path(self.config.proposal_path).expanduser() / str(uuid4())
        proposal.mkdir(parents=True, exist_ok=False)
        shutil.copytree(source, proposal / candidate.name, symlinks=False)
        _write_json(
            proposal / "proposal.json",
            {
                "name": candidate.name,
                "digest": candidate.digest,
                "package": candidate.name,
                "status": "pending",
            },
        )
        return proposal

    def review(self, proposal_id: str, *, accept: bool) -> dict[str, Any]:
        proposal = Path(self.config.proposal_path).expanduser() / proposal_id
        record_path = proposal / "proposal.json"
        record = _read_json(record_path, default=None)
        if not isinstance(record, dict):
            raise SkillError(f"Skill proposal not found: {proposal_id}")
        if record.get("status") != "pending":
            raise SkillError("Skill proposal has already been reviewed.")
        record["status"] = "accepted" if accept else "rejected"
        if accept:
            installed = self.install(
                proposal / str(record["package"]),
                expected_digest=str(record["digest"]),
                scope="project",
            )
            record["installed_path"] = str(installed.path)
        _write_json(record_path, record)
        return record

    def _metadata(self, package: Path, scope: SkillScope) -> SkillMetadata:
        _validate_package(package, self.config)
        frontmatter, _ = _parse_skill_file(package / "SKILL.md")
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not isinstance(name, str) or not SKILL_NAME.fullmatch(name) or len(name) > 64:
            raise SkillError("Skill name must be 1-64 lowercase letters, numbers, or hyphens.")
        if package.name != name:
            raise SkillError("Skill name must match its parent directory name.")
        if not isinstance(description, str) or not description.strip() or len(description) > 1024:
            raise SkillError("Skill description must be 1-1024 characters.")
        compatibility = frontmatter.get("compatibility")
        if compatibility is not None and (
            not isinstance(compatibility, str) or not compatibility or len(compatibility) > 500
        ):
            raise SkillError("Skill compatibility must be a non-empty string up to 500 characters.")
        license_value = frontmatter.get("license")
        if license_value is not None and (
            not isinstance(license_value, str) or not license_value.strip()
        ):
            raise SkillError("Skill license must be a non-empty string when provided.")
        extra_metadata = frontmatter.get("metadata")
        if extra_metadata is not None and (
            not isinstance(extra_metadata, dict)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in extra_metadata.items()
            )
        ):
            raise SkillError("Skill metadata must map string keys to string values.")
        allowed_value = frontmatter.get("allowed-tools")
        if allowed_value is None:
            allowed: list[str] = []
        elif isinstance(allowed_value, str):
            allowed = [item for item in allowed_value.split() if item]
        else:
            raise SkillError("Skill allowed-tools must be a space-separated string.")
        digest = _package_digest(package)
        state_record = self.state.get(name)
        state: SkillState = "enabled"
        if isinstance(state_record, dict):
            if state_record.get("digest") != digest:
                state = "quarantined"
            else:
                value = state_record.get("state")
                if value in {"enabled", "disabled", "quarantined"}:
                    state = value
        trust = "enterprise-managed" if scope == "managed" else "untrusted-local"
        return SkillMetadata(
            name=name,
            description=description.strip(),
            path=package,
            scope=scope,
            trust=trust,
            digest=digest,
            state=state,
            allowed_tools=tuple(allowed),
        )

    def _roots(self) -> list[tuple[SkillScope, Path]]:
        roots: list[tuple[SkillScope, Path]] = [
            ("managed", Path(value).expanduser()) for value in self.config.managed_paths
        ]
        if self.config.allow_user:
            roots.extend(("user", Path(value).expanduser()) for value in self.config.user_paths)
        if self.config.allow_project:
            roots.extend(
                ("project", Path(value).expanduser()) for value in self.config.project_paths
            )
        return roots

    def _install_root(self, scope: SkillScope) -> Path:
        if scope == "managed":
            raise SkillError("Managed skill installation is controlled outside Loro.")
        roots = self.config.user_paths if scope == "user" else self.config.project_paths
        if not roots:
            raise SkillError(f"No {scope} skill installation path is configured.")
        return Path(roots[0]).expanduser()


def _parse_skill_file(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise SkillError(f"Skill frontmatter is missing: {path}")
    closing = text.find("\n---\n", 4)
    if closing < 0:
        raise SkillError(f"Skill frontmatter is not closed: {path}")
    frontmatter = text[4:closing]
    try:
        if any(isinstance(event, yaml.events.AliasEvent) for event in yaml.parse(frontmatter)):
            raise SkillError("Skill frontmatter cannot contain YAML aliases.")
        metadata = yaml.safe_load(frontmatter) or {}
    except SkillError:
        raise
    except yaml.YAMLError as error:
        raise SkillError(f"Invalid skill frontmatter: {error}") from error
    if not isinstance(metadata, dict):
        raise SkillError("Skill frontmatter must be a mapping.")
    return metadata, text[closing + 5 :]


def _validate_package(package: Path, config: SkillsConfig) -> None:
    if package.is_symlink():
        raise SkillError(f"Skill package roots cannot be symlinks: {package}")
    if not package.is_dir() or not (package / "SKILL.md").is_file():
        raise SkillError(f"Skill package requires SKILL.md: {package}")
    files = [path for path in package.rglob("*") if path.is_file() or path.is_symlink()]
    if len(files) > config.max_files:
        raise SkillError("Skill package exceeds managed file-count limit.")
    total = 0
    for path in files:
        if path.is_symlink():
            raise SkillError(f"Skill packages cannot contain symlinks: {path}")
        relative = path.relative_to(package)
        if len(relative.parts) > config.max_reference_depth + 2:
            raise SkillError("Skill package exceeds managed reference-depth limit.")
        total += path.stat().st_size
    if total > config.max_bytes:
        raise SkillError("Skill package exceeds managed byte limit.")


def _package_digest(package: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in package.rglob("*") if item.is_file()):
        relative = path.relative_to(package).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _reject_symlink_path(root: Path, target: Path) -> None:
    current = root
    for part in target.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise SkillError("Skill supporting paths cannot traverse symlinks.")


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
