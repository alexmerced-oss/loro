from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from loro.agent_profiles.digest import profile_digest, spec_digest
from loro.agent_profiles.errors import ProfileError
from loro.agent_profiles.models import AgentProfileModel
from loro.config import AgentProfilesConfig, SafetyConfig
from loro.data_protection import DataProtectionEngine

TrustLabel = Literal["managed", "user", "project", "imported"]
_FILES = ("*.agent.yaml", "*.agent.yml", "*.agent.json", "*.agent.md")


class _StringSafeLoader(yaml.SafeLoader):
    pass


for key, resolvers in list(_StringSafeLoader.yaml_implicit_resolvers.items()):
    _StringSafeLoader.yaml_implicit_resolvers[key] = [
        item for item in resolvers if item[0] != "tag:yaml.org,2002:timestamp"
    ]


def _unique_mapping(loader: _StringSafeLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        if key in result:
            raise ProfileError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_StringSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


@dataclass(frozen=True)
class ResolvedProfile:
    document: AgentProfileModel
    source_path: Path
    trust: TrustLabel
    spec_digest: str
    profile_digest: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscoveredProfile:
    name: str
    revision: int
    description: str
    source_path: Path
    trust: TrustLabel
    shadowed: tuple[Path, ...] = ()


class AgentProfileRegistry:
    def __init__(
        self,
        config: AgentProfilesConfig,
        *,
        cwd: Path | None = None,
        safety: SafetyConfig | None = None,
    ) -> None:
        self.config = config
        self.cwd = (cwd or Path.cwd()).resolve()
        self.protection = DataProtectionEngine(safety or SafetyConfig())

    def _roots(self) -> list[tuple[TrustLabel, Path]]:
        roots: list[tuple[TrustLabel, Path]] = []
        roots.extend(("managed", Path(item).expanduser()) for item in self.config.managed_paths)
        if self.config.allow_user:
            roots.extend(("user", Path(item).expanduser()) for item in self.config.user_paths)
        if self.config.allow_project:
            roots.extend(("project", self.cwd / item) for item in self.config.project_paths)
        return roots

    def discover(self) -> list[DiscoveredProfile]:
        if not self.config.enabled:
            return []
        selected: dict[str, DiscoveredProfile] = {}
        seen_in_root: dict[tuple[Path, str], Path] = {}
        shadowed: dict[str, list[Path]] = {}
        count = 0
        for trust, root in self._roots():
            if not root.exists():
                continue
            root = root.resolve()
            for pattern in _FILES:
                for path in sorted(root.glob(pattern)):
                    count += 1
                    if count > self.config.max_profiles:
                        raise ProfileError("Agent profile count exceeds managed limit.")
                    if path.is_symlink():
                        raise ProfileError(f"Agent profile files cannot be symlinks: {path}")
                    _contained(root, path)
                    document = _load_document(path, self.config.max_bytes, metadata_only=True)
                    metadata = AgentProfileModel.model_validate(document).metadata
                    key = (root, metadata.name)
                    if key in seen_in_root:
                        raise ProfileError(
                            f"Agent profile name collision for {metadata.name}: "
                            f"{seen_in_root[key]} and {path}"
                        )
                    seen_in_root[key] = path
                    if metadata.name in selected:
                        shadowed.setdefault(metadata.name, []).append(
                            selected[metadata.name].source_path
                        )
                    selected[metadata.name] = DiscoveredProfile(
                        name=metadata.name,
                        revision=metadata.revision,
                        description=metadata.description,
                        source_path=path,
                        trust=trust,
                    )
        return sorted(
            (
                DiscoveredProfile(**{**item.__dict__, "shadowed": tuple(shadowed.get(name, []))})
                for name, item in selected.items()
            ),
            key=lambda item: item.name,
        )

    def get(self, name: str) -> DiscoveredProfile:
        found = next((item for item in self.discover() if item.name == name), None)
        if found is None:
            raise ProfileError(f"Agent profile not found: {name}")
        return found

    def load(self, name: str) -> ResolvedProfile:
        metadata = self.get(name)
        raw = _load_document(metadata.source_path, self.config.max_bytes)
        decision = self.protection.evaluate(
            json.dumps(raw, sort_keys=True, ensure_ascii=False), "agent_profile"
        )
        if decision.findings:
            kinds = ", ".join(sorted({item.kind for item in decision.findings}))
            raise ProfileError(f"Agent profile contains literal secret material: {kinds}")
        raw.get("metadata", {}).pop("trust", None)
        document = AgentProfileModel.model_validate(raw)
        dumped = document.model_dump(mode="json", by_alias=True, exclude_none=True)
        warnings = tuple(f"shadowed profile: {path}" for path in metadata.shadowed)
        return ResolvedProfile(
            document=document,
            source_path=metadata.source_path,
            trust=metadata.trust,
            spec_digest=spec_digest(dumped),
            profile_digest=profile_digest(dumped),
            warnings=warnings,
        )


def load_path(path: Path, *, max_bytes: int = 1_000_000) -> AgentProfileModel:
    return AgentProfileModel.model_validate(_load_document(path, max_bytes))


def _load_document(path: Path, max_bytes: int, *, metadata_only: bool = False) -> dict[str, Any]:
    content = path.read_bytes()
    if len(content) > max_bytes:
        raise ProfileError(f"Agent profile exceeds managed size limit: {path}")
    text = content.decode("utf-8")
    if path.name.endswith(".agent.json"):
        value = json.loads(text, object_pairs_hook=_unique_json_object)
    elif path.name.endswith(".agent.md"):
        value = _markdown_document(text)
    else:
        _reject_aliases(text)
        value = _safe_load(text)
    if not isinstance(value, dict):
        raise ProfileError(f"Agent profile must be an object: {path}")
    if metadata_only:
        return {"metadata": value.get("metadata", {}), "spec": {}}
    return value


def _markdown_document(text: str) -> dict[str, Any]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.DOTALL)
    if not match:
        raise ProfileError("Markdown agent profile requires YAML frontmatter.")
    _reject_aliases(match.group(1))
    value = _safe_load(match.group(1)) or {}
    if not isinstance(value, dict):
        raise ProfileError("Markdown agent profile frontmatter must be an object.")
    body = match.group(2).strip()
    role = value.setdefault("spec", {}).setdefault("role", {})
    if body and role.get("instructions"):
        raise ProfileError("Role instructions cannot appear in both frontmatter and body.")
    if body:
        role["instructions"] = body
    return value


def _contained(root: Path, path: Path) -> None:
    try:
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as error:
        raise ProfileError(f"Agent profile escapes discovery root: {path}") from error


def _reject_aliases(text: str) -> None:
    if any(isinstance(event, yaml.events.AliasEvent) for event in yaml.parse(text)):
        raise ProfileError("Agent profiles cannot contain YAML aliases.")


def _safe_load(text: str) -> Any:
    loader = _StringSafeLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def _unique_json_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ProfileError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result
