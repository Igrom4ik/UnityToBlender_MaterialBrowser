"""Side-car JSON document written next to every material in the library.

The document is the source of truth: a `.blend` is always rebuildable from it
without touching the Unity project. `material` stays a literal record of the
`.mat` file, while `effective` holds what actually applies once shader defaults
are merged in.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from . import texture_meta
from .shader_parser import ShaderInterface
from .shader_parser.interface import CONFIDENCE_NONE, MAIN_COLOR, TEXTURE


SCHEMA = "ump.material/1"

ORIGIN_MATERIAL = "material"
ORIGIN_SHADER_DEFAULT = "shader_default"

STATUS_OK = "OK"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"


def file_hash(path: str | Path) -> str:
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def file_stamp(path: str | Path) -> str:
    """Cheap dependency stamp: no hashing of large texture files."""
    try:
        stat = Path(path).stat()
    except OSError:
        return "missing"
    return f"{int(stat.st_mtime)}:{stat.st_size}"


def unity_relative_path(path: str | Path, assets_root: str | Path | None) -> str:
    """Machine independent key such as `Assets/ART/.../Bordur_1.mat`."""
    target = Path(path)
    if assets_root is None:
        return target.as_posix()
    root = Path(assets_root)
    try:
        relative = target.relative_to(root)
    except ValueError:
        return target.as_posix()
    prefix = root.name if root.name else "Assets"
    return f"{prefix}/{relative.as_posix()}"


def _texture_entry(texture, assets_root) -> dict:
    settings = (
        texture_meta.read_settings(texture.asset_path)
        if texture.asset_path
        else texture_meta.settings_from_name(f"{texture.property_name}.png")
    )
    return {
        "guid": texture.guid,
        "path": unity_relative_path(texture.asset_path, assets_root)
        if texture.asset_path
        else None,
        "absolute_path": str(texture.asset_path) if texture.asset_path else None,
        "resolved": texture.asset_path is not None,
        "scale": list(texture.scale),
        "offset": list(texture.offset),
        "meta": settings.to_data(),
    }


def _shader_block(material, interface: ShaderInterface, assets_root) -> dict:
    reference = material.shader
    return {
        "guid": reference.guid if reference else "",
        "file_id": reference.file_id if reference else 0,
        "name": interface.name,
        "backend": interface.backend,
        "workflow": interface.workflow,
        "confidence": interface.confidence,
        "resolved": interface.is_resolved,
        "path": unity_relative_path(reference.asset_path, assets_root)
        if reference and reference.asset_path
        else None,
        "hash": interface.source_hash,
        "parser_version": interface.parser_version,
        "notes": list(interface.notes),
    }


def build_effective(material, interface: ShaderInterface) -> dict:
    """Merge material values over shader defaults and separate stale entries."""
    known = interface.by_name
    hidden = [item.name for item in interface.properties if item.is_hidden]
    resolved_shader = interface.confidence != CONFIDENCE_NONE

    textures: dict[str, dict] = {}
    floats: dict[str, dict] = {}
    colors: dict[str, dict] = {}
    ints: dict[str, dict] = {}
    stale: list[str] = []

    for texture in material.textures:
        name = texture.property_name
        if resolved_shader and name not in known:
            stale.append(name)
            continue
        if resolved_shader and known[name].is_hidden:
            continue
        textures[name] = {"origin": ORIGIN_MATERIAL, "guid": texture.guid}

    for name, value in material.floats.items():
        if resolved_shader and name not in known:
            stale.append(name)
            continue
        if resolved_shader and known[name].is_hidden:
            continue
        floats[name] = {"value": value, "origin": ORIGIN_MATERIAL}

    for name, value in material.colors.items():
        if resolved_shader and name not in known:
            stale.append(name)
            continue
        if resolved_shader and known[name].is_hidden:
            continue
        colors[name] = {"value": list(value), "origin": ORIGIN_MATERIAL}

    for name, value in material.ints.items():
        if resolved_shader and name not in known:
            stale.append(name)
            continue
        if resolved_shader and known[name].is_hidden:
            continue
        ints[name] = {"value": value, "origin": ORIGIN_MATERIAL}

    for item in interface.properties:
        if item.is_hidden or item.type == TEXTURE:
            continue
        if item.name in floats or item.name in colors or item.name in ints:
            continue
        default = item.default
        if isinstance(default, (tuple, list)):
            colors[item.name] = {
                "value": [float(channel) for channel in default],
                "origin": ORIGIN_SHADER_DEFAULT,
            }
        elif isinstance(default, bool):
            floats[item.name] = {
                "value": 1.0 if default else 0.0,
                "origin": ORIGIN_SHADER_DEFAULT,
            }
        elif isinstance(default, (int, float)):
            floats[item.name] = {
                "value": float(default),
                "origin": ORIGIN_SHADER_DEFAULT,
            }

    # What the shader says about each colour travels with the document: phase B
    # rebuilds from JSON alone and has no interface to ask (3.1), so without this
    # a proven tint would look exactly like a guessed one.
    for name, entry in colors.items():
        item = known.get(name)
        if item is None:
            continue
        if MAIN_COLOR in item.attributes:
            entry["main_color"] = True
        if item.group:
            entry["group"] = item.group

    return {
        "textures": textures,
        "floats": floats,
        "colors": colors,
        "ints": ints,
        "stale": sorted(set(stale)),
        "hidden": sorted(hidden),
    }


def build_document(
    material,
    interface: ShaderInterface,
    *,
    assets_root: str | Path | None = None,
    catalog: str = "",
) -> dict:
    textures = {
        texture.property_name: _texture_entry(texture, assets_root)
        for texture in material.textures
    }
    texture_deps = {
        texture.guid: file_stamp(texture.asset_path)
        for texture in material.textures
        if texture.asset_path
    }

    effective = build_effective(material, interface)
    # Only textures the shader actually uses count as missing. A slot left over
    # from a previous shader is already reported as stale, and Unity does not
    # render it either -- counting it would send the user hunting for a file
    # nothing needs.
    unresolved_textures = [
        name
        for name, entry in textures.items()
        if not entry["resolved"] and name in effective["textures"]
    ]
    status = STATUS_OK
    warnings: list[str] = []
    if unresolved_textures:
        status = STATUS_PARTIAL
        warnings.append(
            "Textures were not found by GUID: " + ", ".join(sorted(unresolved_textures))
        )
    if not interface.is_resolved:
        status = STATUS_PARTIAL
        warnings.extend(interface.notes or ("Shader could not be resolved",))

    return {
        "schema": SCHEMA,
        "source": {
            "guid": "",
            "path": unity_relative_path(material.asset_path, assets_root),
            "absolute_path": str(material.asset_path),
            "hash": file_hash(material.asset_path),
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "extractor": "yaml",
            "texture_deps": texture_deps,
        },
        "shader": _shader_block(material, interface, assets_root),
        "material": {
            "name": material.name,
            "catalog": catalog,
            "render_queue": material.render_queue,
            "lightmap_flags": material.lightmap_flags,
            "double_sided_gi": material.double_sided_gi,
            "keywords": {
                "valid": list(material.valid_keywords),
                "invalid": list(material.invalid_keywords),
            },
            "textures": textures,
            "empty_texture_slots": list(material.empty_texture_slots),
            "floats": material.floats,
            "colors": {name: list(value) for name, value in material.colors.items()},
            "ints": material.ints,
        },
        "effective": effective,
        "conversion": {
            "profile": "",
            "node_group": "",
            "status": status,
            "unmapped": [],
            "warnings": warnings,
            "built_at": "",
            "builder_version": "",
        },
    }


def write_document(path: str | Path, document: dict) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    os.replace(temporary, destination)
    return destination


def read_document(path: str | Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("schema", "")).startswith("ump.material/"):
        return None
    return data
