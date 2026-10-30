"""Create the Blender material and put it on the object under the cursor.

Split from `converter.py` on purpose: everything that *decides* what the
material becomes is plain Python and testable without Blender, and this module
only carries the decision out in bpy.
"""

from __future__ import annotations

import os
import re

import bpy
from bpy_extras import view3d_utils

from . import node_builder, properties
from ._core import converter


_NUMERIC_SUFFIX = re.compile(r"\.\d{3}$")

CREATED = "created"
REBUILT = "rebuilt"
REUSED = "reused"
KEPT = "kept"

_MATERIAL_TYPES = {
    "MESH", "CURVE", "CURVES", "SURFACE", "META", "FONT", "VOLUME",
    "POINTCLOUD", "GPENCIL", "GREASEPENCIL",
}


def _same_file(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(right)
    )


def _unity_path(value: str) -> str:
    return str(value or "").replace("\\", "/").casefold()


def unity_identity(material) -> tuple[str, str, str]:
    """(guid, Unity path, absolute source) of a material either add-on built.

    `ump_*` belongs to the library add-on. Reading its keys too is what keeps a
    drop from adding a second copy of a material the library already put in this
    file -- to the artist those two are the same material, whichever tool made
    them.
    """
    guid = material.get("umd_guid") or material.get("ump_unity_guid") or ""
    path = material.get("umd_unity_path") or material.get("ump_unity_path") or ""
    source = material.get("umd_source") or ""
    return str(guid).casefold(), _unity_path(path), str(source)


def is_unity_material(material) -> bool:
    return any(unity_identity(material))


def built_by_drop(material) -> bool:
    return bool(material.get("umd_source"))


def _rank(material) -> tuple:
    """Order candidates so the same one always wins.

    Most used first, because that is the copy the file is actually built on;
    then the shortest name, which is the original rather than a `.001` of it.
    """
    return (-material.users, len(material.name), material.name)


def normalize_conversion_identity(conversion):
    """Older compiled cores may overwrite the material GUID with a missing texture GUID."""
    source_guid = conversion.document.get("source", {}).get("guid", "")
    if source_guid:
        conversion.guid = source_guid
    return conversion


def find_existing(conversion: converter.Conversion):
    """The material this file already holds for the same Unity asset.

    Matched by GUID first: a `.mat` that moved inside the project keeps its
    GUID and should still be recognised. Path matching is the fallback for
    projects where the `.meta` could not be read.
    """
    normalize_conversion_identity(conversion)
    guid = conversion.guid.casefold()
    unity_path = _unity_path(conversion.unity_path)
    candidates = [
        material for material in bpy.data.materials if is_unity_material(material)
    ]

    for match in (
        lambda item: guid and unity_identity(item)[0] == guid,
        lambda item: unity_path and unity_identity(item)[1] == unity_path,
        lambda item: _same_file(unity_identity(item)[2], str(conversion.path)),
    ):
        found = sorted((item for item in candidates if match(item)), key=_rank)
        if found:
            return found[0]
    return None


def duplicate_groups() -> list[list]:
    """Local Unity materials that stand for the same `.mat`, best copy first."""
    grouped: dict[tuple, list] = {}
    for material in bpy.data.materials:
        if material.library is not None or not is_unity_material(material):
            continue
        guid, unity_path, source = unity_identity(material)
        key = ("guid", guid) if guid else ("path", unity_path or source.casefold())
        grouped.setdefault(key, []).append(material)
    return [sorted(group, key=_rank) for group in grouped.values() if len(group) > 1]


def merge_duplicates() -> tuple[int, int]:
    """Point every user at one copy and delete the rest.

    Returns (materials removed, groups merged).
    """
    removed = 0
    groups = duplicate_groups()
    for keeper, *extras in groups:
        for material in extras:
            try:
                material.user_remap(keeper)
                material.use_fake_user = False
                bpy.data.materials.remove(material)
                removed += 1
            except (ReferenceError, RuntimeError):
                continue
        _drop_numeric_suffix(keeper)
    return removed, len(groups)


def _drop_numeric_suffix(material) -> None:
    """Give the surviving copy the plain name back.

    The copy that wins is the one the file actually uses, which is often the
    `.001`. Leaving that suffix behind would make a merged file still look like
    it is full of duplicates.
    """
    base = _NUMERIC_SUFFIX.sub("", material.name)
    if base == material.name or base in bpy.data.materials:
        return
    try:
        material.name = base
    except (AttributeError, RuntimeError):
        pass


def unloaded_textures(material, plan) -> list[str]:
    """Textures the plan asked for that Blender refused to load.

    Unity happily uses formats Blender has no reader for -- `.psd` above all --
    and a silently missing map is the kind of thing an artist only notices three
    hours later.
    """
    tree = material.node_tree
    if tree is None:
        return [texture.property_name for texture in plan.textures]
    present = {node.name for node in tree.nodes if node.type == "TEX_IMAGE"}
    unlit_base = tree.nodes.get("Unity _BaseColor") if material.get("umd_shader_adapter") == "environment-unlit/1" else None
    packed = str(material.get("umd_shader_adapter", "")).startswith("project-packed-formulas/")
    return [
        texture.property_name
        for texture in plan.textures
        if not (texture.property_name == "_BaseColor" and unlit_base is not None and unlit_base.type == "TEX_IMAGE" and unlit_base.image is not None)
        and f"{node_builder.NODE_PREFIX} {texture.role} {texture.property_name}" not in present
        and not (packed and (node := tree.nodes.get(f"{node_builder.NODE_PREFIX} {texture.property_name}")) is not None and node.type == "TEX_IMAGE" and node.image is not None)
    ]


def pending_update(conversion: converter.Conversion, preferences):
    """The material this drop would overwrite, when Unity has moved on.

    Returned before anything is written, so the caller can put the question to
    the user instead of quietly replacing a node tree they may have edited.
    """
    if not preferences.reuse_existing:
        return None
    existing = find_existing(conversion)
    if existing is None or existing.library is not None or not built_by_drop(existing):
        return None
    return existing if (converter.is_stale(existing, conversion) or _vertex_upgrade(existing, conversion) or _shader_upgrade(existing, conversion)) else None


def build(conversion: converter.Conversion, preferences, *, version: str = "", rebuild: bool = True):
    """Return (material, what happened) for one converted Unity material."""
    normalize_conversion_identity(conversion)
    metadata = converter.metadata(conversion, builder_version=version)
    existing = find_existing(conversion) if preferences.reuse_existing else None

    if existing is not None:
        # A material that came from a library, or that the library add-on built,
        # is reused but never rewritten: it is not this add-on's to rebuild.
        if existing.library is not None or not built_by_drop(existing):
            return existing, REUSED
        if not (converter.is_stale(existing, conversion) or _vertex_upgrade(existing, conversion) or _shader_upgrade(existing, conversion)):
            return existing, REUSED
        if not rebuild:
            return existing, KEPT
        node_builder.build_material(existing, conversion.plan, metadata=metadata, document=conversion.document)
        return existing, REBUILT

    material = bpy.data.materials.new(conversion.name)
    try:
        node_builder.build_material(material, conversion.plan, metadata=metadata, document=conversion.document)
    except Exception:
        bpy.data.materials.remove(material)
        raise
    return material, CREATED


def _view_under_mouse(context, event):
    """The 3D viewport region the mouse is in, with its view matrix.

    A drop can be dispatched from any region of the area, so the region on the
    context is not necessarily the one holding the view. Window coordinates are
    the only thing that is always right, so the region is found from those.
    """
    area = getattr(context, "area", None)
    if area is None or area.type != "VIEW_3D":
        return None, None
    windows = [region for region in area.regions if region.type == "WINDOW"]
    region = next(
        (
            item
            for item in windows
            if item.x <= event.mouse_x < item.x + item.width
            and item.y <= event.mouse_y < item.y + item.height
        ),
        None,
    )
    if region is None:
        return None, None
    context_region = getattr(context, "region", None)
    region_data = getattr(context, "region_data", None)
    if region_data is None or context_region is not region:
        region_data = getattr(area.spaces.active, "region_3d", None)
    return (region, region_data) if region_data is not None else (None, None)


def object_under_cursor(context, event) -> tuple[object, int]:
    """Raycast the visible scene, returning (object, material slot index).

    The slot comes from the face that was actually hit, so dropping onto one
    part of a multi-material object replaces that part -- the same thing
    Blender's own material drop does.
    """
    region, region_data = _view_under_mouse(context, event)
    if region is None:
        return None, 0
    coordinate = (event.mouse_x - region.x, event.mouse_y - region.y)

    direction = view3d_utils.region_2d_to_vector_3d(region, region_data, coordinate)
    origin = view3d_utils.region_2d_to_origin_3d(region, region_data, coordinate)
    if not region_data.is_perspective:
        # An orthographic origin sits on the view plane, so anything behind the
        # pivot would be missed; step back far enough to include it.
        origin = origin - direction * (region_data.view_distance * 2.0 + 1.0)

    depsgraph = context.evaluated_depsgraph_get()
    hit, _location, _normal, index, evaluated, _matrix = context.scene.ray_cast(
        depsgraph, origin, direction
    )
    if not hit or evaluated is None:
        return None, 0

    target = evaluated.original or evaluated
    slot = 0
    polygons = getattr(getattr(evaluated, "data", None), "polygons", None)
    if polygons is not None and 0 <= index < len(polygons):
        slot = polygons[index].material_index
    return target, max(0, min(slot, max(0, len(target.material_slots) - 1)))


def assign(material, target, *, mode: str, slot: int = 0) -> bool:
    """Put `material` on `target` the way the preferences ask for."""
    if target is None or mode == properties.ASSIGN_NONE:
        return False
    if target.type not in _MATERIAL_TYPES:
        return False
    data = target.data
    if data is None or not hasattr(data, "materials") or getattr(data, "library", None):
        return False

    try:
        if mode == properties.ASSIGN_APPEND or not target.material_slots:
            data.materials.append(material)
            target.active_material_index = len(target.material_slots) - 1
            return True
        if mode == properties.ASSIGN_REPLACE_ALL:
            for material_slot in target.material_slots:
                material_slot.material = material
            target.active_material_index = 0
            return True
        index = max(0, min(slot, len(target.material_slots) - 1))
        target.material_slots[index].material = material
        target.active_material_index = index
        return True
    except (AttributeError, RuntimeError, ReferenceError):
        return False


def ensure_slot(target, material) -> int:
    """Index of the slot holding `material`, adding one if it has none."""
    for index, material_slot in enumerate(target.material_slots):
        if material_slot.material == material:
            return index
    target.data.materials.append(material)
    return max(0, len(target.material_slots) - 1)


def assign_to_selected_faces(material, targets) -> tuple[list, int]:
    """Put the material on the selected faces of meshes open in Edit Mode.

    Written against bmesh rather than `object.material_slot_assign` so it does
    not depend on the operator context a drop happens to arrive with, and so it
    can say how many faces it actually touched.

    Returns (objects changed, faces changed).
    """
    import bmesh

    changed = []
    faces_changed = 0
    for target in targets:
        if target is None or target.type != "MESH" or target.mode != "EDIT":
            continue
        mesh = target.data
        if getattr(mesh, "library", None):
            continue
        try:
            mesh_bm = bmesh.from_edit_mesh(mesh)
            selected = [face for face in mesh_bm.faces if face.select]
            if not selected:
                continue
            index = ensure_slot(target, material)
            for face in selected:
                face.material_index = index
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
            target.active_material_index = index
        except (AttributeError, RuntimeError, ReferenceError, ValueError):
            continue
        changed.append(target)
        faces_changed += len(selected)
    return changed, faces_changed


def add_slot_only(material, targets) -> list:
    """Give each object a slot for the material without touching any face."""
    changed = []
    for target in targets:
        if target is None or target.type not in _MATERIAL_TYPES:
            continue
        data = target.data
        if data is None or not hasattr(data, "materials") or getattr(data, "library", None):
            continue
        try:
            ensure_slot(target, material)
        except (AttributeError, RuntimeError, ReferenceError):
            continue
        changed.append(target)
    return changed


def assign_to_many(material, targets, *, mode: str, slot: int = 0) -> list:
    assigned = []
    seen = set()
    for target in targets:
        if target is None or target.name in seen:
            continue
        seen.add(target.name)
        if assign(material, target, mode=mode, slot=slot):
            assigned.append(target)
    return assigned


def _vertex_upgrade(material, conversion):
    return bool(getattr(conversion.plan, "vertex_paint", None)) and material.get("umd_vertex_graph_version", 0) < 2


def _shader_upgrade(material, conversion):
    from . import environment_unlit_adapter
    if environment_unlit_adapter.supports(conversion.document):
        return material.get("umd_shader_adapter") != environment_unlit_adapter.VERSION
    from . import shader_adapter, packed_shader_adapter
    source_shader = conversion.document.get("shader", {}).get("name", "")
    if material.get("umd_shader_name", "") != source_shader:
        return True
    if material.get("umd_shader_adapter") == shader_adapter.VERSION and not shader_adapter.supports(conversion.document):
        return True
    if shader_adapter.supports(conversion.document):
        return material.get("umd_shader_adapter") != shader_adapter.VERSION
    if packed_shader_adapter.supports(conversion.document):
        return material.get("umd_shader_adapter") != "project-packed-formulas/2"
    versions={"Custom/Environment/Window":"window-ray-box/1", "Custom/Environment/FakeStairs":"fake-stairs-view-parallax/1"}
    version=versions.get(conversion.document.get("shader",{}).get("name"))
    if version:return material.get("umd_shader_adapter") != version
    return False
