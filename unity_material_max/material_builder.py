"""Carry out a recipe inside 3ds Max. The only module that decides nothing.

Imports `pymxs`, so it runs solely inside Max (GUI session or 3dsmaxbatch).
Everything worth arguing about was settled in `recipe.py`, which is tested
without Max.
"""

from __future__ import annotations

import json
from pathlib import Path

from .recipe import (
    BUMP_MAP,
    CHANNEL_ALPHA,
    CHANNEL_BLUE,
    CHANNEL_INV_ALPHA,
    ChannelRecipe,
    MaterialRecipe,
)


# One AppData slot holds the whole provenance record as JSON. The number is
# arbitrary but must never change: it is how the record is found again.
APPDATA_ID = 1969001

OSL_DIR = Path(__file__).resolve().parent / "osl"
NORMAL_SHADER = "unity_nmg_normal.osl"
BLUE_SHADER = "unity_blue_channel.osl"

# Bitmaptexture.monoOutput: 0 is RGB intensity, 1 is the alpha channel.
MONO_ALPHA = 1


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


def _osl_map(runtime, shader: str, source):
    """An OSL map running one of the shaders shipped beside this module.

    OSL is how a channel is taken apart exactly: it is the only stock way to
    compute the missing blue of an `_NMG` normal, and the only one that picks a
    single channel without weighting it. Returns None when this 3ds Max cannot
    do OSL, so the caller can fall back and say so.
    """
    path = OSL_DIR / shader
    if not path.is_file():
        return None

    factory = getattr(runtime, "OSLMap", None)
    if factory is None:
        return None

    try:
        osl = factory()
        osl.OSLPath = str(path)
        # The shader's input becomes a property pair on the map once the file
        # is loaded; the map half is what takes another texmap.
        for attribute in ("Packed_map", "Packed"):
            try:
                setattr(osl, attribute, source)
            except Exception:  # noqa: BLE001  (wrong half, try the other)
                continue
            break
        else:
            return None
    except Exception:  # noqa: BLE001  (no OSL here, or a refused shader)
        return None
    return osl


def _channel_map(runtime, channel: ChannelRecipe, notes: list):
    """A texmap yielding one channel of a packed bitmap."""
    source = _bitmap(runtime, channel.source)

    if channel.channel == CHANNEL_BLUE:
        blue = _osl_map(runtime, BLUE_SHADER, source)
        if blue is None:
            notes.append(
                f"{Path(channel.source.path).name}: metallic sits in the blue channel "
                "and this 3ds Max has no OSL to read it out"
            )
        return blue

    # Alpha needs no shader: a bitmap can output it directly, and the Output
    # rollout scales and inverts it -- which is Unity's gloss to roughness.
    source.monoOutput = MONO_ALPHA
    if channel.channel == CHANNEL_INV_ALPHA:
        output = getattr(source, "output", None)
        if output is None:
            notes.append(
                f"{Path(channel.source.path).name}: gloss could not be inverted into roughness"
            )
            return None
        if abs(channel.scale - 1.0) > 1e-6:
            # `_GlossMapScale` multiplies before the inversion, as in Unity.
            output.RGB_level = channel.scale
        output.invert = True
    return source


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
            if bitmap.unpack_z:
                # Blue holds metallic, so the normal is rebuilt before Normal
                # Bump ever sees it.
                rebuilt = _osl_map(runtime, NORMAL_SHADER, texture)
                if rebuilt is None:
                    recipe.notes.append(
                        f"{Path(bitmap.path).name}: _NMG normal needs OSL to restore "
                        "its blue channel; wired as it is"
                    )
                else:
                    texture = rebuilt
            # A normal map is not a bump map: it goes through Normal Bump, and
            # the amount is stated because Max starts it at 0.3.
            normal = runtime.Normal_Bump()
            normal.normal_map = texture
            texture = normal
            material.bump_map_amt = recipe.bump_amount

        setattr(material, slot, texture)
        setattr(material, f"{slot}_on", True)

    for slot, channel in recipe.channels.items():
        texture = _channel_map(runtime, channel, recipe.notes)
        if texture is None:
            continue
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
