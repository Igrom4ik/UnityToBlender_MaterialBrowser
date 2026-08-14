"""Decide how Unity shader properties map onto Blender inputs.

A profile is a declarative rule set matched by shader GUID or name. Anything a
profile does not cover falls through to a heuristic that reads the shader
interface -- property type and attributes -- rather than guessing from names
alone. Unmapped properties are reported, never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from .shader_parser.interface import (
    HDR,
    NORMAL,
    MAIN_COLOR,
    MAIN_TEXTURE,
    NO_SCALE_OFFSET,
    ShaderInterface,
    TEXTURE,
    WORKFLOW_SPECULAR,
    WORKFLOW_UNKNOWN,
    WORKFLOW_UNLIT,
)


BASE_COLOR = "BASE_COLOR"
NORMAL_MAP = "NORMAL"
METALLIC_GLOSS = "METALLIC_GLOSS"
SPECULAR_GLOSS = "SPECULAR_GLOSS"
OCCLUSION = "OCCLUSION"
EMISSION = "EMISSION"
HEIGHT = "HEIGHT"
UNMAPPED = "UNMAPPED"

COMBINE_MULTIPLY = "multiply"
COMBINE_REPLACE = "replace"

BLEND_OPAQUE = "OPAQUE"
BLEND_CLIP = "CLIP"
BLEND_ALPHA = "BLEND"

_BASE_COLOR_NAMES = (
    "_maintex", "_basemap", "_basecolormap", "_albedomap", "_albedo",
    "_diffusemap", "_diffuse", "_colormap", "_texture",
)
_NORMAL_NAMES = ("_bumpmap", "_normalmap", "_normal", "_normaltex", "_bump")
_METALLIC_NAMES = ("_metallicglossmap", "_maskmap", "_metallicmap", "_metallic_gloss", "_mask")
_SPECULAR_NAMES = ("_specglossmap", "_specularmap", "_specmap")
_OCCLUSION_NAMES = ("_occlusionmap", "_aomap", "_ao", "_occlusion")
_EMISSION_NAMES = ("_emissionmap", "_emissivemap", "_emissioncolormap", "_emission")
_HEIGHT_NAMES = ("_parallaxmap", "_heightmap", "_displacementmap")

_COLOR_TINT_NAMES = ("_color", "_basecolor", "_maincolor", "_tintcolor", "_tint", "_mulcolor")
_METALLIC_SCALAR_NAMES = ("_metallic", "_metallicstrength")
# `_GlossMapScale` scales the smoothness map; it is never the smoothness itself.
_SMOOTHNESS_SCALAR_NAMES = ("_glossiness", "_smoothness", "_gloss")
_SMOOTHNESS_SCALE_NAMES = ("_glossmapscale", "_smoothnesstexturescale")
_SMOOTHNESS_CHANNEL_NAMES = ("_smoothnesstexturechannel",)
_SPECULAR_SCALAR_NAMES = ("_specular", "_specularlevel")
_SPECULAR_COLOR_NAMES = ("_speccolor", "_specularcolor")
_EMISSION_COLOR_NAMES = ("_emissioncolor", "_emissivecolor")
_CUTOFF_NAMES = ("_cutoff", "_alphacutoff", "_alphaclip")
_NORMAL_STRENGTH_NAMES = ("_bumpscale", "_normalscale", "_normalstrength")
_OCCLUSION_STRENGTH_NAMES = ("_occlusionstrength",)


PACK_NONE = "none"
PACK_NMG = "nmg"
PACK_BCA = "bca"
PACK_BCH = "bch"

# Channel packing used by the studio naming scheme, keyed by file name postfix.
# Longer postfixes must be tested first so `_BCA` never matches as `_BC`.
_POSTFIX_PACKING = (
    ("_nmg", NORMAL_MAP, PACK_NMG),
    ("_bca", BASE_COLOR, PACK_BCA),
    ("_bch", BASE_COLOR, PACK_BCH),
    ("_bc", BASE_COLOR, PACK_NONE),
    ("_ao", OCCLUSION, PACK_NONE),
    ("_n", NORMAL_MAP, PACK_NONE),
)


@dataclass
class TexturePlan:
    property_name: str
    role: str
    image_path: str
    colorspace: str = "sRGB"
    is_normal: bool = False
    alpha_is_transparency: bool = False
    scale: tuple[float, float] = (1.0, 1.0)
    offset: tuple[float, float] = (0.0, 0.0)
    combine: str = COMBINE_MULTIPLY
    channel: str = ""
    packing: str = PACK_NONE


@dataclass
class ConversionPlan:
    profile: str = "generic"
    workflow: str = WORKFLOW_UNKNOWN
    shader_name: str = ""
    base_color: tuple[float, float, float, float] = (0.8, 0.8, 0.8, 1.0)
    metallic: float = 0.0
    roughness: float = 0.5
    specular: float = 0.5
    specular_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    emission_color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    emission_strength: float = 1.0
    normal_strength: float = 1.0
    occlusion_strength: float = 1.0
    alpha: float = 1.0
    alpha_cutoff: float = 0.5
    smoothness_scale: float = 1.0
    smoothness_from_albedo_alpha: bool = False
    tint_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    tint_factor: float = 0.0
    tint_property: str = ""
    blend_mode: str = BLEND_OPAQUE
    backface_culling: bool = True
    textures: list[TexturePlan] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)

    def texture_for(self, role: str) -> TexturePlan | None:
        return next((item for item in self.textures if item.role == role), None)


@dataclass
class Profile:
    identifier: str
    shader_guids: tuple[str, ...] = ()
    shader_names: tuple[str, ...] = ()
    textures: dict = field(default_factory=dict)
    floats: dict = field(default_factory=dict)
    colors: dict = field(default_factory=dict)
    workflow: str = ""
    ignore: tuple[str, ...] = ()

    def matches(self, shader_guid: str, shader_name: str) -> bool:
        if shader_guid and shader_guid.casefold() in self.shader_guids:
            return True
        for pattern in self.shader_names:
            if re.fullmatch(pattern, shader_name, re.IGNORECASE):
                return True
        return False


DEFAULT_PROFILES_PATH = Path(__file__).with_name("default_profiles.json")


def load_profiles(path: str | Path | None) -> list[Profile]:
    """Profiles shipped with the tool, with the user's file taking precedence.

    A rule set is part of the converter, not of a library, so the defaults live
    in the repository (16.5). The first matching profile wins, so anything the
    user wrote is placed ahead of them.
    """
    profiles = _read_profiles(path) if path else []
    profiles.extend(_read_profiles(DEFAULT_PROFILES_PATH))
    return profiles


def _read_profiles(path: str | Path) -> list[Profile]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    profiles: list[Profile] = []
    for item in data.get("profiles", []):
        match = item.get("match", {})
        profiles.append(
            Profile(
                identifier=item.get("id", "profile"),
                shader_guids=tuple(
                    guid.casefold() for guid in match.get("shader_guid", [])
                ),
                shader_names=tuple(match.get("shader_name", [])),
                textures=item.get("textures", {}),
                floats=item.get("floats", {}),
                colors=item.get("colors", {}),
                workflow=item.get("workflow", ""),
                ignore=tuple(item.get("ignore", [])),
            )
        )
    return profiles


def _matches_any(name: str, candidates: tuple[str, ...]) -> bool:
    lowered = name.casefold()
    return any(lowered == candidate for candidate in candidates)


def _contains_any(name: str, candidates: tuple[str, ...]) -> bool:
    lowered = name.casefold()
    return any(candidate in lowered for candidate in candidates)


PRIORITY_ATTRIBUTE = 0
PRIORITY_EXACT = 1
PRIORITY_HEURISTIC = 2
PRIORITY_NONE = 3

_SECONDARY_PREFIXES = ("_detail", "_second")


def _texture_role(name: str, attributes: tuple[str, ...], workflow: str) -> tuple[str, int]:
    """Return the role and how certain it is.

    Certainty matters because roles are exclusive: `_DetailAlbedoMap` merely
    contains "albedo", and without ranking it would claim the base colour slot
    ahead of the real `_MainTex` simply by sorting earlier.
    """
    lowered = name.casefold()
    if lowered.startswith(_SECONDARY_PREFIXES):
        # Detail and secondary layers have no Principled equivalent here.
        return UNMAPPED, PRIORITY_NONE
    if NORMAL in attributes:
        return NORMAL_MAP, PRIORITY_ATTRIBUTE
    if MAIN_TEXTURE in attributes:
        return BASE_COLOR, PRIORITY_ATTRIBUTE
    if _matches_any(name, _NORMAL_NAMES):
        return NORMAL_MAP, PRIORITY_EXACT
    if _matches_any(name, _BASE_COLOR_NAMES):
        return BASE_COLOR, PRIORITY_EXACT
    if _matches_any(name, _METALLIC_NAMES):
        return METALLIC_GLOSS, PRIORITY_EXACT
    if _matches_any(name, _SPECULAR_NAMES):
        return SPECULAR_GLOSS, PRIORITY_EXACT
    if _matches_any(name, _OCCLUSION_NAMES):
        return OCCLUSION, PRIORITY_EXACT
    if _matches_any(name, _EMISSION_NAMES):
        return EMISSION, PRIORITY_EXACT
    if _matches_any(name, _HEIGHT_NAMES):
        return HEIGHT, PRIORITY_EXACT
    if _contains_any(name, ("normal", "bump")):
        return NORMAL_MAP, PRIORITY_HEURISTIC
    if _contains_any(name, ("emission", "emissive")):
        return EMISSION, PRIORITY_HEURISTIC
    if _contains_any(name, ("albedo", "diffuse", "basecolor")):
        return BASE_COLOR, PRIORITY_HEURISTIC
    if _contains_any(name, ("metallic", "gloss", "smooth")):
        role = SPECULAR_GLOSS if workflow == WORKFLOW_SPECULAR else METALLIC_GLOSS
        return role, PRIORITY_HEURISTIC
    if _contains_any(name, ("occlusion", "_ao")):
        return OCCLUSION, PRIORITY_HEURISTIC
    return UNMAPPED, PRIORITY_NONE


def packing_from_filename(path: str) -> tuple[str, str]:
    """Read the studio naming postfix off a texture file name.

    The file name is often more reliable than the shader property: a custom
    shader may feed a packed `_NMG` map through a slot called `_BumpMap`, and
    hanging it on the normal input unchanged would lose metallic and gloss and
    distort the normal.
    """
    stem = Path(path).stem
    stem = re.sub(r"\.\d{3,}$", "", stem).casefold()
    for postfix, role, packing in _POSTFIX_PACKING:
        if stem.endswith(postfix):
            return role, packing
    return "", PACK_NONE


def known_attributes(interface: ShaderInterface | None) -> dict[str, tuple[str, ...]]:
    if interface is None:
        return {}
    return {item.name: item.attributes for item in interface.properties}


def known_groups(interface: ShaderInterface | None) -> dict[str, str]:
    if interface is None:
        return {}
    return {item.name: item.group for item in interface.properties}


# Inspector headings that say a colour property belongs to the albedo. Amplify
# never writes `[MainColor]`, but it does group `_Color` under `Header(Albedo)`
# together with the albedo map, which is the same statement in another form.
_ALBEDO_GROUPS = frozenset(
    {"albedo", "base", "base color", "basecolor", "main color", "maincolor", "diffuse"}
)


def _is_albedo_group(group: str) -> bool:
    return group.strip().casefold().replace("_", " ") in _ALBEDO_GROUPS


def _tint_is_trusted(
    tint_name: str,
    entry: dict,
    attributes: dict[str, tuple[str, ...]],
    groups: dict[str, str],
    shader_block: dict,
    profile: "Profile | None",
) -> bool:
    """Decide whether a colour property really tints the albedo.

    Unity's own shaders multiply `_Color` into the albedo, so that mapping is
    safe. A custom shader may keep `_Color` for something else entirely --
    `Bordur_1` stores a pure red `_Color` while its Amplify graph tints with
    `_MulColor` -- so multiplying blindly would repaint half a library. When the
    intent is not provable the tint is skipped and reported instead.

    The evidence comes from the document first, because phase B runs on JSON
    with no interface at hand, and from the interface when one was passed.
    """
    if not tint_name:
        return False
    if profile is not None and tint_name in profile.colors:
        return True
    if entry.get("main_color") or MAIN_COLOR in attributes.get(tint_name, ()):
        return True
    if _is_albedo_group(entry.get("group", "")) or _is_albedo_group(groups.get(tint_name, "")):
        return True
    return shader_block.get("backend") == "builtin"


def _color_name(effective: dict, names: tuple[str, ...]) -> str:
    for name in effective.get("colors", {}):
        if _matches_any(name, names):
            return name
    return ""


def _scalar(effective: dict, names: tuple[str, ...]) -> float | None:
    """Look names up in priority order, not in whatever order the file had.

    Order matters: a Unity material stores `_Glossiness` next to `_GlossMapScale`,
    and picking whichever appeared first turned a fully rough material
    (`_Glossiness: 0`) into a mirror.
    """
    values = {name.casefold(): entry for name, entry in effective.get("floats", {}).items()}
    for candidate in names:
        entry = values.get(candidate)
        if entry is not None:
            return float(entry.get("value", 0.0))
    return None


def _color(effective: dict, names: tuple[str, ...]) -> tuple[float, float, float, float] | None:
    values = {name.casefold(): entry for name, entry in effective.get("colors", {}).items()}
    for candidate in names:
        entry = values.get(candidate)
        if entry is None:
            continue
        channels = list(entry.get("value", []))
        while len(channels) < 4:
            channels.append(1.0)
        return tuple(float(channel) for channel in channels[:4])
    return None


def _blend_mode(document: dict, effective: dict) -> str:
    keywords = {
        keyword.casefold()
        for keyword in document.get("material", {}).get("keywords", {}).get("valid", [])
    }
    if "_alphatest_on" in keywords:
        return BLEND_CLIP
    if "_alphablend_on" in keywords or "_alphapremultiply_on" in keywords:
        return BLEND_ALPHA

    mode = _scalar(effective, ("_mode",))
    if mode is not None:
        rounded = int(round(mode))
        if rounded == 1:
            return BLEND_CLIP
        if rounded in (2, 3):
            return BLEND_ALPHA

    surface = _scalar(effective, ("_surface",))
    if surface is not None and int(round(surface)) == 1:
        return BLEND_ALPHA

    render_queue = document.get("material", {}).get("render_queue", -1)
    if isinstance(render_queue, int) and render_queue >= 3000:
        return BLEND_ALPHA
    return BLEND_OPAQUE


def build_plan(
    document: dict,
    interface: ShaderInterface | None = None,
    profiles: list[Profile] | None = None,
    trust_all_tints: bool = False,
) -> ConversionPlan:
    """Turn a side-car document into an explicit build plan for the node tree."""
    shader_block = document.get("shader", {})
    effective = document.get("effective", {})
    material_block = document.get("material", {})
    workflow = shader_block.get("workflow", WORKFLOW_UNKNOWN)

    profile = next(
        (
            item
            for item in (profiles or [])
            if item.matches(shader_block.get("guid", ""), shader_block.get("name", ""))
        ),
        None,
    )
    if profile is not None and profile.workflow:
        workflow = profile.workflow

    plan = ConversionPlan(
        profile=profile.identifier if profile else "generic",
        workflow=workflow,
        shader_name=shader_block.get("name", ""),
    )

    tint = _color(effective, _COLOR_TINT_NAMES)
    tint_name = _color_name(effective, _COLOR_TINT_NAMES)
    tint_trusted = _tint_is_trusted(
        tint_name,
        effective.get("colors", {}).get(tint_name, {}),
        known_attributes(interface),
        known_groups(interface),
        shader_block,
        profile,
    )

    metallic = _scalar(effective, _METALLIC_SCALAR_NAMES)
    if metallic is not None:
        plan.metallic = max(0.0, min(1.0, metallic))

    smoothness = _scalar(effective, _SMOOTHNESS_SCALAR_NAMES)
    if smoothness is not None:
        plan.roughness = max(0.0, min(1.0, 1.0 - smoothness))

    smoothness_scale = _scalar(effective, _SMOOTHNESS_SCALE_NAMES)
    if smoothness_scale is not None:
        plan.smoothness_scale = max(0.0, smoothness_scale)

    smoothness_channel = _scalar(effective, _SMOOTHNESS_CHANNEL_NAMES)
    # Unity: 0 keeps smoothness in the metallic map alpha, 1 moves it to albedo alpha.
    plan.smoothness_from_albedo_alpha = bool(
        smoothness_channel is not None and int(round(smoothness_channel)) == 1
    )

    specular = _scalar(effective, _SPECULAR_SCALAR_NAMES)
    if specular is not None:
        plan.specular = max(0.0, min(1.0, specular))

    specular_color = _color(effective, _SPECULAR_COLOR_NAMES)
    if specular_color is not None:
        plan.specular_color = specular_color

    emission = _color(effective, _EMISSION_COLOR_NAMES)
    if emission is not None:
        plan.emission_color = emission

    cutoff = _scalar(effective, _CUTOFF_NAMES)
    if cutoff is not None:
        plan.alpha_cutoff = cutoff

    normal_strength = _scalar(effective, _NORMAL_STRENGTH_NAMES)
    if normal_strength is not None:
        plan.normal_strength = normal_strength

    occlusion_strength = _scalar(effective, _OCCLUSION_STRENGTH_NAMES)
    if occlusion_strength is not None:
        plan.occlusion_strength = occlusion_strength

    plan.blend_mode = _blend_mode(document, effective)
    if workflow == WORKFLOW_UNLIT:
        plan.metallic = 0.0
        plan.roughness = 1.0

    known = interface.by_name if interface is not None else {}

    candidates: list[tuple[int, str, str, dict, tuple[str, ...], str]] = []
    for name in effective.get("textures", {}):
        entry = material_block.get("textures", {}).get(name, {})
        if not entry.get("absolute_path"):
            continue
        if profile is not None and name in profile.ignore:
            continue

        rule = (profile.textures if profile else {}).get(name, {})
        attributes = known[name].attributes if name in known else ()
        if rule.get("role"):
            role, priority = rule["role"], -1  # an explicit profile always wins
        else:
            role, priority = _texture_role(name, attributes, workflow)

        file_role, packing = packing_from_filename(entry.get("absolute_path", ""))
        if rule.get("packing"):
            packing = rule["packing"]
        if file_role and priority > PRIORITY_EXACT:
            role, priority = file_role, PRIORITY_EXACT
        candidates.append((priority, name, role, entry, attributes, packing))

    used_roles: set[str] = set()
    for priority, name, role, entry, attributes, packing in sorted(
        candidates, key=lambda item: (item[0], item[1])
    ):
        rule = (profile.textures if profile else {}).get(name, {})
        if role == UNMAPPED or role in used_roles:
            plan.unmapped.append(name)
            continue

        meta = entry.get("meta", {})
        scale = tuple(entry.get("scale", (1.0, 1.0)))
        offset = tuple(entry.get("offset", (0.0, 0.0)))
        if NO_SCALE_OFFSET in attributes:
            scale, offset = (1.0, 1.0), (0.0, 0.0)
        # Layered shaders keep tiling in their own float properties instead of
        # the texture slot, so a profile can say where to read it from.
        scale_from = rule.get("scale_from")
        if scale_from:
            values = [_scalar(effective, (item.casefold(),)) for item in scale_from]
            if all(value is not None for value in values):
                scale = (float(values[0]), float(values[1]))

        is_normal = role == NORMAL_MAP or meta.get("texture_type") == "normal"
        colorspace = "sRGB" if role in (BASE_COLOR, EMISSION) else "Non-Color"
        if meta.get("colorspace") == "Non-Color":
            colorspace = "Non-Color"
        if is_normal or packing == PACK_NMG:
            colorspace = "Non-Color"

        plan.textures.append(
            TexturePlan(
                property_name=name,
                role=role,
                image_path=entry["absolute_path"],
                colorspace=colorspace,
                is_normal=is_normal,
                alpha_is_transparency=bool(meta.get("alpha_is_transparency")),
                scale=(float(scale[0]), float(scale[1])),
                offset=(float(offset[0]), float(offset[1])),
                combine=rule.get("combine", COMBINE_MULTIPLY),
                channel=rule.get("channel", ""),
                packing=packing,
            )
        )
        used_roles.add(role)
        if packing == PACK_NMG:
            # The packed map already carries metallic and gloss, so a separate
            # mask map would fight with it.
            used_roles.add(METALLIC_GLOSS)

    if tint is not None:
        has_base_color_map = plan.texture_for(BASE_COLOR) is not None
        plan.tint_property = tint_name
        plan.tint_color = tint
        if not has_base_color_map:
            plan.base_color = tint
            plan.alpha = tint[3] if tint[3] > 0.0 else 1.0
        elif tint[:3] != (1.0, 1.0, 1.0):
            # The tint node is always built so the value stays visible and one
            # slider turns it on. It only multiplies by default when the shader
            # proves that is what Unity does, because a custom shader may keep
            # `_Color` for something else entirely.
            plan.tint_factor = 1.0 if (tint_trusted or trust_all_tints) else 0.0
            if not plan.tint_factor:
                plan.unmapped.append(tint_name)

    if HDR in {attribute for item in known.values() for attribute in item.attributes}:
        plan.emission_strength = 1.0
    return plan
