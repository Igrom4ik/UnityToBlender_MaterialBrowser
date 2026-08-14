"""What to build in 3ds Max, decided without 3ds Max.

A `ConversionPlan` says what the Unity material means; a `MaterialRecipe` says
which Physical Material slots that turns into. Keeping the decision here means
it is covered by the ordinary test suite -- the `pymxs` layer on top only
carries the recipe out, so the part that can go wrong silently is the part that
is tested.

Anything Physical Material cannot express is recorded in `notes` instead of
being approximated quietly, the same rule the Blender side follows with
`unmapped`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from unity_pipeline_core import profiles


# Physical Material slots, by their MAXScript property names.
BASE_COLOR_MAP = "base_color_map"
METALNESS_MAP = "metalness_map"
ROUGHNESS_MAP = "roughness_map"
BUMP_MAP = "bump_map"
EMISSION_MAP = "emission_map"
CUTOUT_MAP = "cutout_map"

# Unity stores non-colour data linearly; Max decides colour space by the gamma
# a bitmap is loaded with, so it is part of the recipe rather than a later fix.
GAMMA_SRGB = 2.2
GAMMA_LINEAR = 1.0

_ROLE_TO_SLOT = {
    profiles.BASE_COLOR: BASE_COLOR_MAP,
    profiles.METALLIC_GLOSS: METALNESS_MAP,
    profiles.SPECULAR_GLOSS: METALNESS_MAP,
    profiles.NORMAL_MAP: BUMP_MAP,
    profiles.EMISSION: EMISSION_MAP,
}


@dataclass(frozen=True)
class BitmapRecipe:
    path: str
    gamma: float = GAMMA_SRGB
    tiling: tuple[float, float] = (1.0, 1.0)
    offset: tuple[float, float] = (0.0, 0.0)
    is_normal: bool = False
    """Goes into a Normal Bump map rather than straight into the slot."""


@dataclass
class MaterialRecipe:
    name: str
    base_color: tuple[float, float, float] = (0.8, 0.8, 0.8)
    metalness: float = 0.0
    roughness: float = 0.5
    transparency: float = 0.0
    emission: float = 0.0
    emit_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # Max defaults this to 0.3, which quietly flattens every normal map. The
    # recipe always states it.
    bump_amount: float = 1.0
    maps: dict[str, BitmapRecipe] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    unity_guid: str = ""
    unity_path: str = ""
    shader_name: str = ""


def recipe_from_plan(plan, name: str, *, guid: str = "", unity_path: str = "") -> MaterialRecipe:
    """Translate a conversion plan into Physical Material terms."""
    recipe = MaterialRecipe(
        name=name,
        base_color=tuple(plan.base_color[:3]),
        metalness=plan.metallic,
        roughness=plan.roughness,
        transparency=1.0 - plan.alpha if plan.alpha < 1.0 else 0.0,
        bump_amount=plan.normal_strength,
        emit_color=tuple(plan.emission_color[:3]),
        unity_guid=guid,
        unity_path=unity_path,
        shader_name=plan.shader_name,
    )
    if any(channel > 0.0 for channel in plan.emission_color[:3]):
        recipe.emission = plan.emission_strength

    for texture in plan.textures:
        slot = _ROLE_TO_SLOT.get(texture.role)
        if slot is None:
            recipe.notes.append(f"{texture.property_name}: role {texture.role} has no slot")
            continue
        if slot in recipe.maps:
            recipe.notes.append(f"{texture.property_name}: {slot} is already taken")
            continue

        recipe.maps[slot] = BitmapRecipe(
            path=texture.image_path,
            gamma=GAMMA_SRGB if texture.colorspace == "sRGB" else GAMMA_LINEAR,
            tiling=tuple(texture.scale),
            offset=tuple(texture.offset),
            is_normal=texture.is_normal,
        )

        # Packed maps carry more than one channel of meaning, and splitting them
        # is a node chain rather than a slot assignment (M2).
        if texture.packing == profiles.PACK_NMG:
            recipe.notes.append(
                f"{texture.property_name}: _NMG packs normal, metallic and gloss; "
                "only the normal is wired up so far"
            )
        elif texture.packing == profiles.PACK_BCA:
            recipe.notes.append(
                f"{texture.property_name}: alpha is transparency; cutout is not wired up yet"
            )

    occlusion = plan.texture_for(profiles.OCCLUSION)
    if occlusion is not None:
        recipe.notes.append(
            f"{occlusion.property_name}: Physical Material has no occlusion slot; "
            "it needs a composite into base colour"
        )
    if plan.tint_factor:
        recipe.notes.append(
            f"{plan.tint_property}: proven tint needs a Color Correction over the base map"
        )
    for name_ in plan.unmapped:
        recipe.notes.append(f"{name_}: unmapped by the conversion plan")

    return recipe


def recipe_from_document(document: dict, rules=None) -> MaterialRecipe:
    """The whole of phase B's thinking, from a side-car document."""
    plan = profiles.build_plan(document, None, rules if rules is not None else profiles.load_profiles(None))
    material = document.get("material", {})
    return recipe_from_plan(
        plan,
        material.get("asset_name") or material.get("name", "material"),
        guid=document.get("source", {}).get("guid", ""),
        unity_path=document.get("source", {}).get("path", ""),
    )
