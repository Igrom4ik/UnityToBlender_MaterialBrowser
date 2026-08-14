"""Index Unity material libraries without importing Blender.

Every path used here arrives as an argument: the add-on UI decides which Unity
folders are scanned and where caches live, this module never assumes a location.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Iterator


CACHE_SCHEMA = 2
_GUID = re.compile(r"\bguid:\s*([0-9a-fA-F]{32})\b")
_FILE_ID = re.compile(r"\bfileID:\s*(-?\d+)\b")
_MATERIAL_NAME = re.compile(r"^\s*m_Name:\s*(.*?)\s*$", re.MULTILINE)
_SHADER = re.compile(r"^\s*m_Shader:\s*\{(?P<reference>[^}]*)\}", re.MULTILINE)
_VECTOR2 = re.compile(r"x:\s*(?P<x>[^,}]+),\s*y:\s*(?P<y>[^,}]+)")
_COLOR = re.compile(
    r"r:\s*(?P<r>[^,}]+),\s*g:\s*(?P<g>[^,}]+),\s*b:\s*(?P<b>[^,}]+),\s*a:\s*(?P<a>[^,}]+)"
)
_SKIP_DIRECTORIES = {".git", "Library", "Logs", "obj", "Temp", "__pycache__"}
_TEXTURE_EXTENSIONS = {
    ".bmp", ".dds", ".exr", ".hdr", ".jpeg", ".jpg", ".png", ".psd",
    ".tga", ".tif", ".tiff", ".webp",
}
_SHADER_EXTENSIONS = {".shader", ".shadergraph"}


@dataclass(frozen=True)
class UnityTextureReference:
    property_name: str
    guid: str
    asset_path: Path | None
    scale: tuple[float, float] = (1.0, 1.0)
    offset: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True)
class UnityShaderReference:
    guid: str
    file_id: int
    asset_path: Path | None = None

    @property
    def is_builtin(self) -> bool:
        """Built-in shaders carry Unity's default resources GUID."""
        return self.guid == "0000000000000000f000000000000000"


@dataclass(frozen=True)
class UnityMaterial:
    name: str
    asset_path: Path
    textures: tuple[UnityTextureReference, ...]
    empty_texture_slots: tuple[str, ...] = ()
    floats: dict[str, float] = field(default_factory=dict)
    colors: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    ints: dict[str, int] = field(default_factory=dict)
    shader: UnityShaderReference | None = None
    valid_keywords: tuple[str, ...] = ()
    invalid_keywords: tuple[str, ...] = ()
    render_queue: int = -1
    lightmap_flags: int = 0
    double_sided_gi: bool = False


@dataclass(frozen=True)
class UnityLibrary:
    selection_root: Path
    assets_root: Path
    materials: tuple[UnityMaterial, ...]
    guid_assets: tuple[tuple[str, Path], ...] = ()
    indexed_at: str = ""


@dataclass(frozen=True)
class ScanProgress:
    fraction: float
    phase: str
    current_path: str = ""
    library: UnityLibrary | None = None


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _to_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None


def resolve_roots(path: str | Path) -> tuple[Path, Path]:
    """Return (selected search root, Assets root) for a project or subfolder."""
    selected = Path(path).expanduser().resolve()
    if (selected / "Assets").is_dir():
        return selected / "Assets", selected / "Assets"

    current = selected
    while current != current.parent:
        if current.name.casefold() == "assets":
            return selected, current
        current = current.parent
    return selected, selected


def unity_project_root(path: str | Path) -> Path | None:
    selected, assets_root = resolve_roots(path)
    if assets_root.name.casefold() == "assets":
        return assets_root.parent
    if (selected / "ProjectSettings").is_dir():
        return selected
    return None


def unity_project_is_open(path: str | Path) -> bool:
    """Use Unity's project lock file instead of probing the Unity process."""
    project_root = unity_project_root(path)
    return bool(project_root and (project_root / "Temp" / "UnityLockfile").exists())


def _walk_files(root: Path) -> Iterator[Path]:
    for directory, directories, filenames in os.walk(root):
        directories[:] = sorted(
            name for name in directories if name not in _SKIP_DIRECTORIES
        )
        for filename in sorted(filenames):
            yield Path(directory) / filename


def _guid_from_meta(meta_path: Path) -> tuple[str, Path] | None:
    try:
        header = meta_path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return None
    match = _GUID.search(header)
    asset_path = meta_path.with_suffix("")
    if match is None or not asset_path.is_file():
        return None
    return match.group(1).casefold(), asset_path


def build_guid_index(root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in _walk_files(root):
        if path.suffix.casefold() != ".meta":
            continue
        entry = _guid_from_meta(path)
        if entry is not None:
            index.setdefault(*entry)
    return index


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _section_lines(lines: list[str], start: int, header_indent: int) -> list[str]:
    """Collect the body of a YAML key until the next key at the same level."""
    body: list[str] = []
    for line in lines[start + 1:]:
        if not line.strip():
            continue
        indent = _indent_of(line)
        if indent <= header_indent and not line.lstrip().startswith("- "):
            break
        body.append(line)
    return body


def _saved_property_sections(content: str) -> dict[str, list[str]]:
    """Split m_SavedProperties into m_TexEnvs / m_Floats / m_Colors / m_Ints."""
    lines = content.splitlines()
    sections: dict[str, list[str]] = {}
    wanted = {"m_TexEnvs", "m_Floats", "m_Colors", "m_Ints"}
    inside = False
    saved_indent = 0

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        indent = _indent_of(line)
        if stripped.startswith("m_SavedProperties:"):
            inside = True
            saved_indent = indent
            continue
        if not inside:
            continue
        if indent <= saved_indent and not stripped.startswith("- "):
            break
        key = stripped.split(":", 1)[0]
        if key in wanted:
            sections[key] = _section_lines(lines, index, indent)
    return sections


def _entry_blocks(body: list[str]) -> Iterator[tuple[str, list[str]]]:
    """Yield (property name, nested lines) for each `- _Name:` list entry."""
    name: str | None = None
    block: list[str] = []
    for line in body:
        stripped = line.strip()
        if stripped.startswith("- "):
            if name is not None:
                yield name, block
            head = stripped[2:]
            name = _unquote(head.split(":", 1)[0])
            remainder = head.split(":", 1)[1] if ":" in head else ""
            block = [remainder] if remainder.strip() else []
            continue
        if name is not None:
            block.append(stripped)
    if name is not None:
        yield name, block


def _parse_texture_entries(
    body: list[str],
    guid_index: dict[str, Path],
) -> tuple[list[UnityTextureReference], list[str]]:
    textures: list[UnityTextureReference] = []
    empty_slots: list[str] = []

    for name, block in _entry_blocks(body):
        joined = "\n".join(block)
        texture_line = next(
            (line for line in block if line.startswith("m_Texture:")), ""
        )
        guid_match = _GUID.search(texture_line)
        if guid_match is None:
            empty_slots.append(name)
            continue

        scale = (1.0, 1.0)
        offset = (0.0, 0.0)
        scale_line = next((line for line in block if line.startswith("m_Scale:")), "")
        offset_line = next((line for line in block if line.startswith("m_Offset:")), "")
        scale_match = _VECTOR2.search(scale_line)
        offset_match = _VECTOR2.search(offset_line)
        if scale_match:
            x = _to_float(scale_match.group("x"))
            y = _to_float(scale_match.group("y"))
            scale = (1.0 if x is None else x, 1.0 if y is None else y)
        if offset_match:
            x = _to_float(offset_match.group("x"))
            y = _to_float(offset_match.group("y"))
            offset = (0.0 if x is None else x, 0.0 if y is None else y)

        guid = guid_match.group(1).casefold()
        textures.append(
            UnityTextureReference(
                property_name=name,
                guid=guid,
                asset_path=guid_index.get(guid),
                scale=scale,
                offset=offset,
            )
        )
        del joined
    return textures, empty_slots


def _parse_scalar_entries(body: list[str]) -> dict[str, float]:
    values: dict[str, float] = {}
    for name, block in _entry_blocks(body):
        raw = block[0] if block else ""
        value = _to_float(raw)
        if value is not None:
            values[name] = value
    return values


def _parse_color_entries(body: list[str]) -> dict[str, tuple[float, float, float, float]]:
    values: dict[str, tuple[float, float, float, float]] = {}
    for name, block in _entry_blocks(body):
        match = _COLOR.search("\n".join(block))
        if match is None:
            continue
        channels = [_to_float(match.group(key)) for key in ("r", "g", "b", "a")]
        if any(channel is None for channel in channels):
            continue
        values[name] = (channels[0], channels[1], channels[2], channels[3])
    return values


def _parse_keyword_list(content: str, key: str) -> tuple[str, ...]:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(f"{key}:"):
            continue
        inline = stripped.split(":", 1)[1].strip()
        if inline.startswith("[") and inline.endswith("]"):
            body = inline[1:-1].strip()
            if not body:
                return ()
            return tuple(_unquote(item) for item in body.split(","))
        keywords = [
            _unquote(item.strip()[2:])
            for item in _section_lines(lines, index, _indent_of(line))
            if item.strip().startswith("- ")
        ]
        return tuple(keyword for keyword in keywords if keyword)
    return ()


def _parse_int_field(content: str, key: str, default: int) -> int:
    match = re.search(rf"^\s*{re.escape(key)}:\s*(-?\d+)\s*$", content, re.MULTILINE)
    return int(match.group(1)) if match else default


def _parse_shader(content: str, guid_index: dict[str, Path]) -> UnityShaderReference | None:
    match = _SHADER.search(content)
    if match is None:
        return None
    reference = match.group("reference")
    guid_match = _GUID.search(reference)
    file_id_match = _FILE_ID.search(reference)
    if guid_match is None:
        return None
    guid = guid_match.group(1).casefold()
    return UnityShaderReference(
        guid=guid,
        file_id=int(file_id_match.group(1)) if file_id_match else 0,
        asset_path=guid_index.get(guid),
    )


def parse_material(path: Path, guid_index: dict[str, Path]) -> UnityMaterial:
    content = path.read_text(encoding="utf-8", errors="replace")
    name_match = _MATERIAL_NAME.search(content)
    name = _unquote(name_match.group(1)) if name_match else path.stem

    sections = _saved_property_sections(content)
    textures, empty_slots = _parse_texture_entries(
        sections.get("m_TexEnvs", []), guid_index
    )
    floats = _parse_scalar_entries(sections.get("m_Floats", []))
    colors = _parse_color_entries(sections.get("m_Colors", []))
    ints = {
        name_: int(value)
        for name_, value in _parse_scalar_entries(sections.get("m_Ints", [])).items()
    }

    return UnityMaterial(
        name=name,
        asset_path=path,
        textures=tuple(textures),
        empty_texture_slots=tuple(empty_slots),
        floats=floats,
        colors=colors,
        ints=ints,
        shader=_parse_shader(content, guid_index),
        valid_keywords=_parse_keyword_list(content, "m_ValidKeywords"),
        invalid_keywords=_parse_keyword_list(content, "m_InvalidKeywords"),
        render_queue=_parse_int_field(content, "m_CustomRenderQueue", -1),
        lightmap_flags=_parse_int_field(content, "m_LightmapFlags", 0),
        double_sided_gi=bool(_parse_int_field(content, "m_DoubleSidedGI", 0)),
    )


def scan_library_steps(path: str | Path) -> Iterator[ScanProgress]:
    """Incrementally index only the selected folder and return a cache-ready library."""
    selection_root, assets_root = resolve_roots(path)
    if not selection_root.is_dir():
        raise FileNotFoundError(f"Unity library folder does not exist: {selection_root}")

    material_paths: list[Path] = []
    meta_paths: list[Path] = []
    discovered = 0
    for file_path in _walk_files(selection_root):
        suffix = file_path.suffix.casefold()
        if suffix == ".mat":
            material_paths.append(file_path)
        elif suffix == ".meta":
            meta_paths.append(file_path)
        discovered += 1
        if discovered % 100 == 0:
            yield ScanProgress(0.05, "DISCOVER", str(file_path))

    material_paths.sort()
    meta_paths.sort()
    total = max(1, len(meta_paths) + len(material_paths))
    completed = 0
    guid_index: dict[str, Path] = {}

    for meta_path in meta_paths:
        entry = _guid_from_meta(meta_path)
        if entry is not None:
            guid_index.setdefault(*entry)
        completed += 1
        yield ScanProgress(0.05 + 0.70 * completed / total, "GUIDS", str(meta_path))

    materials: list[UnityMaterial] = []
    for material_path in material_paths:
        try:
            materials.append(parse_material(material_path, guid_index))
        except OSError:
            pass
        completed += 1
        yield ScanProgress(0.05 + 0.70 * completed / total, "MATERIALS", str(material_path))

    library = UnityLibrary(
        selection_root=selection_root,
        assets_root=assets_root,
        materials=tuple(materials),
        guid_assets=tuple(sorted(guid_index.items())),
        indexed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    yield ScanProgress(1.0, "COMPLETE", str(selection_root), library)


def index_library(path: str | Path) -> UnityLibrary:
    result = None
    for progress in scan_library_steps(path):
        if progress.library is not None:
            result = progress.library
    if result is None:
        raise RuntimeError("Unity library indexing produced no result")
    return result


def cache_key(path: str | Path) -> str:
    selection_root, _assets_root = resolve_roots(path)
    normalized = os.path.normcase(str(selection_root))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def cache_path(cache_directory: str | Path, path: str | Path) -> Path:
    return Path(cache_directory) / f"unity-library-{cache_key(path)}.json"


def _texture_to_data(texture: UnityTextureReference) -> dict:
    return {
        "property_name": texture.property_name,
        "guid": texture.guid,
        "asset_path": str(texture.asset_path) if texture.asset_path else None,
        "scale": list(texture.scale),
        "offset": list(texture.offset),
    }


def _material_to_data(material: UnityMaterial) -> dict:
    shader = material.shader
    return {
        "name": material.name,
        "asset_path": str(material.asset_path),
        "textures": [_texture_to_data(texture) for texture in material.textures],
        "empty_texture_slots": list(material.empty_texture_slots),
        "floats": material.floats,
        "colors": {name: list(value) for name, value in material.colors.items()},
        "ints": material.ints,
        "shader": None
        if shader is None
        else {
            "guid": shader.guid,
            "file_id": shader.file_id,
            "asset_path": str(shader.asset_path) if shader.asset_path else None,
        },
        "valid_keywords": list(material.valid_keywords),
        "invalid_keywords": list(material.invalid_keywords),
        "render_queue": material.render_queue,
        "lightmap_flags": material.lightmap_flags,
        "double_sided_gi": material.double_sided_gi,
    }


def _library_to_data(library: UnityLibrary) -> dict:
    return {
        "schema": CACHE_SCHEMA,
        "selection_root": str(library.selection_root),
        "assets_root": str(library.assets_root),
        "indexed_at": library.indexed_at,
        "guid_assets": [[guid, str(path)] for guid, path in library.guid_assets],
        "materials": [_material_to_data(material) for material in library.materials],
    }


def _material_from_data(item: dict) -> UnityMaterial:
    textures = tuple(
        UnityTextureReference(
            property_name=texture["property_name"],
            guid=texture["guid"],
            asset_path=Path(texture["asset_path"]) if texture.get("asset_path") else None,
            scale=tuple(texture.get("scale", (1.0, 1.0))),
            offset=tuple(texture.get("offset", (0.0, 0.0))),
        )
        for texture in item.get("textures", [])
    )
    shader_data = item.get("shader")
    shader = (
        None
        if not shader_data
        else UnityShaderReference(
            guid=shader_data["guid"],
            file_id=shader_data.get("file_id", 0),
            asset_path=Path(shader_data["asset_path"])
            if shader_data.get("asset_path")
            else None,
        )
    )
    return UnityMaterial(
        name=item["name"],
        asset_path=Path(item["asset_path"]),
        textures=textures,
        empty_texture_slots=tuple(item.get("empty_texture_slots", ())),
        floats=dict(item.get("floats", {})),
        colors={name: tuple(value) for name, value in item.get("colors", {}).items()},
        ints=dict(item.get("ints", {})),
        shader=shader,
        valid_keywords=tuple(item.get("valid_keywords", ())),
        invalid_keywords=tuple(item.get("invalid_keywords", ())),
        render_queue=item.get("render_queue", -1),
        lightmap_flags=item.get("lightmap_flags", 0),
        double_sided_gi=item.get("double_sided_gi", False),
    )


def _library_from_data(data: dict) -> UnityLibrary:
    if data.get("schema") != CACHE_SCHEMA:
        raise ValueError("Unsupported Unity library cache schema")
    materials = [_material_from_data(item) for item in data.get("materials", [])]
    return UnityLibrary(
        selection_root=Path(data["selection_root"]),
        assets_root=Path(data["assets_root"]),
        materials=tuple(materials),
        guid_assets=tuple((guid, Path(path)) for guid, path in data.get("guid_assets", [])),
        indexed_at=data.get("indexed_at", ""),
    )


def save_cache(cache_directory: str | Path, library: UnityLibrary) -> Path:
    destination = cache_path(cache_directory, library.selection_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(_library_to_data(library), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def load_cache(cache_directory: str | Path, path: str | Path) -> UnityLibrary | None:
    source = cache_path(cache_directory, path)
    if not source.is_file():
        return None
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        library = _library_from_data(data)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    expected_root, _assets_root = resolve_roots(path)
    if os.path.normcase(str(library.selection_root)) != os.path.normcase(str(expected_root)):
        return None
    return library


def remove_cache(cache_directory: str | Path, path: str | Path) -> bool:
    source = cache_path(cache_directory, path)
    if not source.exists():
        return False
    source.unlink()
    return True


def resolve_cached_libraries(libraries: list[UnityLibrary]) -> tuple[UnityMaterial, ...]:
    """Resolve references across several narrow cached folders and remove overlaps."""
    guid_index: dict[str, Path] = {}
    for library in libraries:
        for guid, path in library.guid_assets:
            guid_index.setdefault(guid, path)

    materials: dict[Path, UnityMaterial] = {}
    for library in libraries:
        for material in library.materials:
            textures = tuple(
                replace(texture, asset_path=guid_index.get(texture.guid, texture.asset_path))
                for texture in material.textures
            )
            shader = material.shader
            if shader is not None and shader.asset_path is None:
                shader = replace(shader, asset_path=guid_index.get(shader.guid))
            materials.setdefault(
                material.asset_path, replace(material, textures=textures, shader=shader)
            )
    return tuple(materials[path] for path in sorted(materials))


def resolve_cached_texture_assets(libraries: list[UnityLibrary]) -> tuple[Path, ...]:
    """Return unique texture assets indexed by all enabled cached folders."""
    paths = {
        path
        for library in libraries
        for _guid, path in library.guid_assets
        if path.suffix.casefold() in _TEXTURE_EXTENSIONS
    }
    return tuple(sorted(paths))


def resolve_cached_shader_assets(libraries: list[UnityLibrary]) -> dict[str, Path]:
    """Return shader assets by GUID across all cached folders."""
    shaders: dict[str, Path] = {}
    for library in libraries:
        for guid, path in library.guid_assets:
            if path.suffix.casefold() in _SHADER_EXTENSIONS:
                shaders.setdefault(guid, path)
    return shaders
