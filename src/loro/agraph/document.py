from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from ags import canonical_json as _canonical_json
from ags import graph_digest


class GraphDocumentError(ValueError):
    """Raised when an AGS document cannot be loaded safely."""


class _NoDuplicateLoader(yaml.SafeLoader):
    pass


_NoDuplicateLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for _first in "yYnNoO":
    _NoDuplicateLoader.yaml_implicit_resolvers.pop(_first, None)


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> Any:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise GraphDocumentError("AGS YAML mapping keys must be strings.")
        if key in mapping:
            raise GraphDocumentError(f"duplicate key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDuplicateLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


@dataclass(frozen=True)
class GraphDocument:
    path: Path
    data: dict[str, Any]
    canonical: bytes
    digest: str

    @property
    def graph_id(self) -> str:
        return str(self.data.get("id", ""))


def canonical_json(document: dict[str, Any]) -> bytes:
    try:
        return _canonical_json(document)
    except (TypeError, ValueError) as error:
        raise GraphDocumentError(f"AGS document is not JSON-compatible: {error}") from error


def _json_mapping(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GraphDocumentError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _load_yaml(text: str) -> Any:
    loader = _NoDuplicateLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def load_graph(path: str | Path, *, max_bytes: int = 5_000_000) -> GraphDocument:
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() not in {".json", ".yaml", ".yml"}:
        raise GraphDocumentError("Agentic Graph files must use .json, .yaml, or .yml.")
    if not source.is_file():
        raise GraphDocumentError(f"Agentic Graph file not found: {source}")
    if source.stat().st_size > max_bytes:
        raise GraphDocumentError(f"Agentic Graph exceeds the managed {max_bytes}-byte limit.")
    try:
        text = source.read_text(encoding="utf-8")
        raw = (
            _load_yaml(text)
            if source.suffix.lower() in {".yaml", ".yml"}
            else json.loads(text, object_pairs_hook=_json_mapping)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise GraphDocumentError(f"Unable to parse Agentic Graph: {error}") from error
    if not isinstance(raw, dict):
        raise GraphDocumentError("Agentic Graph root must be an object.")
    canonical = canonical_json(raw)
    return GraphDocument(source, raw, canonical, graph_digest(raw))
