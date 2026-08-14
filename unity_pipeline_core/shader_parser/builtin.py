"""Shader interfaces for Unity's built-in shaders, which have no source file.

Entries are matched by the fileID stored under Unity's default resources GUID,
or by shader name. The catalog is deliberately incomplete: a missing entry
becomes an explicit `unresolved` state instead of a silent guess.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

from .interface import (
    CONFIDENCE_FULL,
    ShaderInterface,
    ShaderProperty,
    WORKFLOW_UNKNOWN,
    unresolved_interface,
)


BACKEND = "builtin"
DEFAULT_RESOURCES_GUID = "0000000000000000f000000000000000"
_CATALOG_PATH = Path(__file__).with_name("builtin_shaders.json")


@functools.lru_cache(maxsize=1)
def _catalog() -> dict:
    try:
        return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"file_ids": {}, "shaders": {}}


def catalog_names() -> tuple[str, ...]:
    return tuple(sorted(_catalog().get("shaders", {})))


def name_for_file_id(file_id: int) -> str:
    return _catalog().get("file_ids", {}).get(str(file_id), "")


def is_builtin_reference(guid: str, file_id: int) -> bool:
    """True when a material points at Unity's default resources."""
    return guid.casefold() == DEFAULT_RESOURCES_GUID and bool(name_for_file_id(file_id))


def parse_name(name: str) -> ShaderInterface:
    entry = _catalog().get("shaders", {}).get(name)
    if entry is None:
        return unresolved_interface(
            name or "unknown",
            f"Built-in shader is not present in the catalog: {name}",
        )
    properties = tuple(
        ShaderProperty.from_data(item) for item in entry.get("properties", [])
    )
    return ShaderInterface(
        name=name,
        backend=BACKEND,
        workflow=entry.get("workflow", WORKFLOW_UNKNOWN),
        confidence=CONFIDENCE_FULL,
        properties=properties,
        source_hash=f"builtin:{name}",
    )


def parse_reference(guid: str, file_id: int) -> ShaderInterface:
    name = name_for_file_id(file_id)
    if not name:
        return unresolved_interface(
            f"builtin:{file_id}",
            f"Unknown built-in shader fileID {file_id} (guid {guid})",
        )
    return parse_name(name)
