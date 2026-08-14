"""Universal shader interface reader.

Chooses a backend by file type, falls back to the built-in catalog for Unity's
own shaders, and degrades to `material_only` instead of guessing. Parsed
interfaces are cached per GUID because a project has thousands of materials and
only dozens of shaders.
"""

from __future__ import annotations

from pathlib import Path

from . import builtin, shadergraph, shaderlab
from .interface import (
    CONFIDENCE_FULL,
    CONFIDENCE_NONE,
    CONFIDENCE_PARTIAL,
    SCHEMA,
    ShaderInterface,
    ShaderProperty,
    WORKFLOW_METALLIC,
    WORKFLOW_SPECULAR,
    WORKFLOW_UNKNOWN,
    WORKFLOW_UNLIT,
    unresolved_interface,
)


# 2: ShaderLab keeps the `[Header(...)]` a property sits under, which is what
# proves an Amplify `_Color` belongs to the albedo.
PARSER_VERSION = 2

_FILE_BACKENDS = (shaderlab, shadergraph)


def backend_for(path: Path):
    suffix = path.suffix.casefold()
    for module in _FILE_BACKENDS:
        if suffix in module.EXTENSIONS:
            return module
    return None


def parse_file(path: str | Path) -> ShaderInterface:
    source = Path(path)
    module = backend_for(source)
    if module is None:
        return unresolved_interface(
            source.stem, f"No backend handles shader files of type {source.suffix}"
        )
    try:
        return module.parse(source)
    except OSError as error:
        return unresolved_interface(source.stem, f"Shader file could not be read: {error}")


class ShaderResolver:
    """Resolve shader references to interfaces, parsing every shader once."""

    def __init__(self, shader_paths: dict[str, Path] | None = None):
        self.shader_paths = {
            guid.casefold(): Path(path) for guid, path in (shader_paths or {}).items()
        }
        self._cache: dict[str, ShaderInterface] = {}

    def add_paths(self, shader_paths: dict[str, Path]) -> None:
        for guid, path in shader_paths.items():
            self.shader_paths.setdefault(guid.casefold(), Path(path))

    def resolve(
        self,
        guid: str,
        file_id: int = 0,
        asset_path: str | Path | None = None,
    ) -> ShaderInterface:
        key = f"{guid.casefold()}:{file_id}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        path = Path(asset_path) if asset_path else self.shader_paths.get(guid.casefold())
        if path is not None and path.is_file():
            interface = parse_file(path)
        elif builtin.is_builtin_reference(guid, file_id):
            interface = builtin.parse_reference(guid, file_id)
        else:
            interface = unresolved_interface(
                f"unresolved:{guid[:8]}",
                "Shader asset was not found in the indexed folders",
            )
        self._cache[key] = interface
        return interface

    @property
    def parsed(self) -> dict[str, ShaderInterface]:
        return dict(self._cache)


__all__ = [
    "CONFIDENCE_FULL",
    "CONFIDENCE_NONE",
    "CONFIDENCE_PARTIAL",
    "PARSER_VERSION",
    "SCHEMA",
    "ShaderInterface",
    "ShaderProperty",
    "ShaderResolver",
    "WORKFLOW_METALLIC",
    "WORKFLOW_SPECULAR",
    "WORKFLOW_UNKNOWN",
    "WORKFLOW_UNLIT",
    "builtin",
    "parse_file",
    "shadergraph",
    "shaderlab",
    "unresolved_interface",
]
