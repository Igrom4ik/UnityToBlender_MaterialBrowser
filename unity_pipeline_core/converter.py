"""Turn one dropped `.mat` into a build plan, without Blender.

The library add-on takes a detour through a JSON tree on disk because it builds
thousands of materials and wants them rebuildable offline. A drop converts one
material that the user is looking at right now, so the same document is built in
memory and thrown away -- the `.mat` on disk is already the source of truth.

Everything that decides *what* the material becomes lives here; `builder.py`
only carries it out in bpy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import time

from . import fast_index, material_json, profiles, unity_library, color_space
from .shader_parser import PARSER_VERSION, ShaderResolver


@dataclass
class Conversion:
    """Everything a drop needs to know about one Unity material."""

    path: Path
    name: str
    guid: str = ""
    source_hash: str = ""
    unity_path: str = ""
    catalog: str = ""
    document: dict = field(default_factory=dict)
    plan: object = None
    shader_name: str = ""
    shader_guid: str = ""
    shader_resolved: bool = False
    missing_guids: list[str] = field(default_factory=list)
    unresolved_textures: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing_guids

    @property
    def texture_count(self) -> int:
        return len(getattr(self.plan, "textures", ()) or ())


def catalog_of(path: Path, assets_root: Path | None) -> str:
    if assets_root is None:
        return path.parent.name
    try:
        relative = path.parent.relative_to(assets_root)
    except ValueError:
        return path.parent.name
    parts = [part for part in relative.parts if part not in (".", "")]
    return "/".join([assets_root.name, *parts]) if parts else assets_root.name


def convert(
    path: str | Path,
    *,
    index: fast_index.ProjectIndex | None = None,
    extra: dict[str, str | Path] | None = None,
    assets_root: str | Path | None = None,
    rules: list | None = None,
    trust_all_tints: bool = False,
) -> Conversion:
    """Read a `.mat` and decide what Blender material it becomes.

    Unresolved references are reported, never invented: a texture whose GUID is
    not in the index leaves its slot empty and its name in `missing_guids`, so
    the caller can widen the search and convert again instead of shipping a
    material that quietly lost a map.
    """
    material_path = Path(path)
    root = Path(assets_root) if assets_root else fast_index.assets_root_of(material_path)

    lazy = fast_index.LazyGuidIndex(index, extra)
    material = unity_library.parse_material(material_path, lazy)

    reference = material.shader
    shader_paths = {}
    if reference is not None and reference.asset_path is not None:
        shader_paths[reference.guid] = reference.asset_path
    resolver = ShaderResolver(shader_paths)
    interface = (
        resolver.resolve(reference.guid, reference.file_id, reference.asset_path)
        if reference is not None
        else resolver.resolve("", 0, None)
    )

    catalog = catalog_of(material_path, root)
    document = material_json.build_document(
        material, interface, assets_root=root, catalog=catalog
    )
    guid = fast_index.guid_of_asset(material_path)
    document["source"]["guid"] = guid

    plan = profiles.build_plan(document, interface, rules or [], trust_all_tints)

    entries = document["material"]["textures"]
    used = document["effective"]["textures"]
    unresolved = sorted(name for name in used if not entries.get(name, {}).get("resolved"))

    # Only references the shader actually uses count as missing. A `.mat` keeps
    # texture slots from whatever shader it wore before, and Unity ignores them;
    # treating those GUIDs as unresolved would send every drop off to index the
    # whole project to look for files nothing renders.
    missing = []
    if reference is not None and not interface.is_resolved and reference.guid:
        missing.append(reference.guid)
    for name in unresolved:
        texture_guid = entries.get(name, {}).get("guid", "")
        if texture_guid:
            missing.append(texture_guid)

    return Conversion(
        path=material_path,
        name=material.name or material_path.stem,
        guid=guid,
        source_hash=document["source"]["hash"],
        unity_path=document["source"]["path"],
        catalog=catalog,
        document=document,
        plan=plan,
        shader_name=interface.name,
        shader_guid=reference.guid if reference is not None else "",
        shader_resolved=interface.is_resolved,
        missing_guids=sorted(set(missing)),
        unresolved_textures=unresolved,
        unmapped=list(plan.unmapped),
        warnings=list(document["conversion"]["warnings"]),
    )


def resolve_and_convert(
    path: str | Path,
    *,
    index: fast_index.ProjectIndex | None = None,
    assets_root: str | Path | None = None,
    rules: list | None = None,
    trust_all_tints: bool = False,
    nearby_seconds: float = 1.5,
    nearby_budget: int = 6000,
) -> tuple[Conversion, dict[str, str]]:
    """Convert, and when references are missing look around the material first.

    The nearby pass is bounded on purpose: it is there to catch the common case
    where the maps sit next to the `.mat`, not to become a slow full scan by
    accident. Whatever it finds is returned so the caller can fold it into the
    cached index.
    """
    material_path = Path(path)
    root = Path(assets_root) if assets_root else fast_index.assets_root_of(material_path)
    conversion = convert(
        material_path,
        index=index,
        assets_root=root,
        rules=rules,
        trust_all_tints=trust_all_tints,
    )
    if conversion.is_complete or root is None or nearby_seconds <= 0.0:
        return conversion, {}

    found, _remaining = fast_index.resolve_nearby(
        material_path.parent,
        root,
        conversion.missing_guids,
        file_budget=nearby_budget,
        deadline=time.monotonic() + nearby_seconds,
    )
    if not found:
        return conversion, {}

    conversion = convert(
        material_path,
        index=index,
        extra=found,
        assets_root=root,
        rules=rules,
        trust_all_tints=trust_all_tints,
    )
    return conversion, found


def unity_values(conversion: Conversion) -> dict:
    """Every Unity value that applies, kept whether Blender can use it or not.

    A Principled BSDF has nowhere to put `_ZOffsetCameraDistance` or a damage
    mask toggle, and dropping them on the floor loses the only copy that
    travelled with the material. They ride along as data instead, so the panel
    can show what Unity actually holds and nothing has to be looked up by hand.
    """
    effective = conversion.document.get("effective", {})
    values: dict[str, list] = {}
    for name, entry in sorted(effective.get("floats", {}).items()):
        values[name] = [round(float(entry.get("value", 0.0)), 6), entry.get("origin", "")]
    for name, entry in sorted(effective.get("ints", {}).items()):
        values[name] = [int(entry.get("value", 0)), entry.get("origin", "")]
    for name, entry in sorted(effective.get("colors", {}).items()):
        values[name] = [
            [round(float(channel), 6) for channel in entry.get("value", ())],
            entry.get("origin", ""),
        ]
    return values


def metadata(conversion: Conversion, *, builder_version: str) -> dict:
    """Custom properties stamped onto the Blender material.

    They are what makes a re-drop an update instead of a duplicate, and what
    lets `Reload from Unity` work in a file opened on another machine.
    """
    plan = conversion.plan
    return {
        "umd_source": str(conversion.path),
        "umd_unity_path": conversion.unity_path,
        "umd_guid": conversion.guid,
        "umd_hash": conversion.source_hash,
        "umd_catalog": conversion.catalog,
        "umd_shader_name": conversion.shader_name,
        "umd_shader_guid": conversion.shader_guid,
        "umd_profile": getattr(plan, "profile", ""),
        "umd_workflow": getattr(plan, "workflow", ""),
        "umd_image_source_version": 1,
        "umd_color_space_version": color_space.VERSION,
        "umd_color_space": conversion.document.get("source", {}).get("color_space", {}).get("space", "Unknown"),
        "umd_parser_version": PARSER_VERSION,
        "umd_builder_version": builder_version,
        "umd_unresolved": ", ".join(conversion.unresolved_textures),
        "umd_unmapped": ", ".join(conversion.unmapped),
        "umd_unity_values": json.dumps(
            unity_values(conversion), ensure_ascii=False, separators=(",", ":")
        ),
    }


def is_stale(properties, conversion: Conversion) -> bool:
    """True when the `.mat` changed since this material was built from it."""
    return (str(properties.get("umd_hash", "")) != conversion.source_hash
            or properties.get("umd_image_source_version") != 1
            or properties.get("umd_color_space_version") != color_space.VERSION
            or properties.get("umd_color_space") != conversion.document.get("source", {}).get("color_space", {}).get("space", "Unknown"))
