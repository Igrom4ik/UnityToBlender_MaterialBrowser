"""Carry out a recipe inside 3ds Max. The only module that decides nothing.

Imports `pymxs`, so it runs solely inside Max (GUI session or 3dsmaxbatch).
Everything worth arguing about was settled in `recipe.py`, which is tested
without Max.
"""

from __future__ import annotations

import json

from .recipe import BUMP_MAP, MaterialRecipe


# One AppData slot holds the whole provenance record as JSON. The number is
# arbitrary but must never change: it is how the record is found again.
APPDATA_ID = 1969001


def _runtime():
    from pymxs import runtime  # noqa: PLC0415  (only available inside 3ds Max)

    return runtime


def _color(runtime, value: tuple[float, float, float]):
    """Unity and the plan work in 0..1; MAXScript colours are 0..255."""
    red, green, blue = (max(0.0, min(1.0, channel)) * 255.0 for channel in value)
    return runtime.color(red, green, blue)


def _bitmap(runtime, bitmap):
    """A bitmap texture with Unity's tiling and the right colour space.

    Gamma has to be given when the file is opened: assigning the filename alone
    lets Max guess it from its own colour management, which turns normal and
    mask maps into washed out colour data.
    """
    texture = runtime.Bitmaptexture()
    texture.bitmap = runtime.openBitMap(bitmap.path, gamma=bitmap.gamma)
    texture.filename = bitmap.path

    coordinates = texture.coords
    coordinates.U_Tiling, coordinates.V_Tiling = bitmap.tiling
    coordinates.U_Offset, coordinates.V_Offset = bitmap.offset
    return texture


def build_material(recipe: MaterialRecipe):
    """Return a Physical Material built from the recipe."""
    runtime = _runtime()

    material = runtime.PhysicalMaterial()
    material.name = recipe.name
    material.Base_Color = _color(runtime, recipe.base_color)
    material.metalness = recipe.metalness
    material.roughness = recipe.roughness
    material.Transparency = recipe.transparency
    material.emission = recipe.emission
    material.emit_color = _color(runtime, recipe.emit_color)

    for slot, bitmap in recipe.maps.items():
        texture = _bitmap(runtime, bitmap)
        if slot == BUMP_MAP and bitmap.is_normal:
            # A normal map is not a bump map: it goes through Normal Bump, and
            # the amount is stated because Max starts it at 0.3.
            normal = runtime.Normal_Bump()
            normal.normal_map = texture
            texture = normal
            material.bump_map_amt = recipe.bump_amount

        setattr(material, slot, texture)
        setattr(material, f"{slot}_on", True)

    _write_provenance(runtime, material, recipe)
    return material


def _write_provenance(runtime, material, recipe: MaterialRecipe) -> None:
    """Store where the material came from, the way materials can carry data.

    `setUserProp` is for scene nodes; a material is a MAXWrapper, and AppData is
    what survives a save and reload on one. Everything goes into a single slot as
    JSON so the record can grow without hunting for free ids.
    """
    payload = json.dumps(
        {
            "ump_unity_guid": recipe.unity_guid,
            "ump_unity_path": recipe.unity_path,
            "ump_shader_name": recipe.shader_name,
        },
        ensure_ascii=False,
    )
    runtime.setAppData(material, APPDATA_ID, payload)


def read_provenance(material) -> dict:
    """Read back what `_write_provenance` stored, or an empty record."""
    runtime = _runtime()
    raw = runtime.getAppData(material, APPDATA_ID)
    if not raw:
        return {}
    try:
        return json.loads(str(raw))
    except ValueError:
        return {}
