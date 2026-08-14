"""Build a Blender material from a conversion plan.

The tree is written out explicitly instead of hiding behind a node group: an
artist opening the material sees exactly which Unity map feeds which input, and
a wrong mapping can be fixed by hand without rebuilding the library.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from .core import profiles


NODE_PREFIX = "UMB"

_COLUMN_TEXTURE = -900
_COLUMN_HELPER = -560
_COLUMN_MIX = -320
_COLUMN_SHADER = 0


def _clear_tree(material) -> None:
    # Blender 5.x materials always carry a node tree and deprecate `use_nodes`;
    # only older versions need the flag flipped.
    if material.node_tree is None:
        material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)


def _new(tree, node_type: str, name: str, location):
    node = tree.nodes.new(node_type)
    node.name = f"{NODE_PREFIX} {name}"
    node.label = name
    node.location = location
    return node


def _link(tree, output, socket) -> None:
    if output is None or socket is None:
        return
    tree.links.new(output, socket)


def _socket(node, *names):
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    return None


def _load_image(path: str, colorspace: str):
    try:
        image = bpy.data.images.load(path, check_existing=True)
    except RuntimeError:
        return None
    try:
        image.colorspace_settings.name = colorspace
    except TypeError:
        pass
    return image


def _apply_render_method(material, plan) -> None:
    """Blender renamed blend settings between versions; support both."""
    transparent = plan.blend_mode in (profiles.BLEND_ALPHA, profiles.BLEND_CLIP)
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED" if transparent else "DITHERED"
    elif hasattr(material, "blend_method"):
        if plan.blend_mode == profiles.BLEND_ALPHA:
            material.blend_method = "BLEND"
        elif plan.blend_mode == profiles.BLEND_CLIP:
            material.blend_method = "CLIP"
            material.alpha_threshold = plan.alpha_cutoff
        else:
            material.blend_method = "OPAQUE"
    material.use_backface_culling = plan.backface_culling


def _connect_smoothness(tree, principled, smoothness_output, plan, row: int) -> None:
    """Unity stores smoothness; Blender wants roughness, so invert it.

    `_GlossMapScale` multiplies the map before the inversion, exactly as Unity
    does, otherwise a scaled map would come out at full gloss.
    """
    source = smoothness_output
    if abs(plan.smoothness_scale - 1.0) > 1e-6:
        scale = _new(tree, "ShaderNodeMath", "Smoothness Scale", (_COLUMN_HELPER, row + 160))
        scale.operation = "MULTIPLY"
        scale.inputs[1].default_value = plan.smoothness_scale
        _link(tree, source, scale.inputs[0])
        source = scale.outputs[0]

    invert = _new(tree, "ShaderNodeMath", "Smoothness To Roughness", (_COLUMN_MIX, row + 320))
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    _link(tree, source, invert.inputs[1])
    _link(tree, invert.outputs[0], _socket(principled, "Roughness"))


def _build_packed_normal(tree, principled, node, plan, row: int) -> None:
    """Unpack an `_NMG` map: RG is the normal, B metallic, A gloss.

    The blue channel of the normal is missing, so it is reconstructed as
    sqrt(1 - x^2 - y^2) instead of being left at 1, which would flatten the
    surface and skew the lighting.
    """
    separate = _new(tree, "ShaderNodeSeparateColor", "NMG Channels", (_COLUMN_HELPER, row + 320))
    _link(tree, node.outputs["Color"], separate.inputs["Color"])

    _link(tree, separate.outputs["Blue"], _socket(principled, "Metallic"))
    _connect_smoothness(tree, principled, node.outputs["Alpha"], plan, row)

    column = _COLUMN_HELPER + 180
    signed = []
    for index, channel in enumerate(("Red", "Green")):
        node_ = _new(
            tree, "ShaderNodeMath", f"NMG {channel} Signed", (column, row + 320 - index * 160)
        )
        node_.operation = "MULTIPLY_ADD"
        node_.inputs[1].default_value = 2.0
        node_.inputs[2].default_value = -1.0
        _link(tree, separate.outputs[channel], node_.inputs[0])
        signed.append(node_)

    squares = []
    for index, source in enumerate(signed):
        node_ = _new(tree, "ShaderNodeMath", f"NMG Square {index}", (column + 160, row + 320 - index * 160))
        node_.operation = "MULTIPLY"
        _link(tree, source.outputs[0], node_.inputs[0])
        _link(tree, source.outputs[0], node_.inputs[1])
        squares.append(node_)

    total = _new(tree, "ShaderNodeMath", "NMG Sum", (column + 320, row + 320))
    total.operation = "ADD"
    _link(tree, squares[0].outputs[0], total.inputs[0])
    _link(tree, squares[1].outputs[0], total.inputs[1])

    remainder = _new(tree, "ShaderNodeMath", "NMG Remainder", (column + 480, row + 320))
    remainder.operation = "SUBTRACT"
    remainder.inputs[0].default_value = 1.0
    remainder.use_clamp = True
    _link(tree, total.outputs[0], remainder.inputs[1])

    root = _new(tree, "ShaderNodeMath", "NMG Blue", (column + 640, row + 320))
    root.operation = "SQRT"
    _link(tree, remainder.outputs[0], root.inputs[0])

    encoded = _new(tree, "ShaderNodeMath", "NMG Blue Encoded", (column + 800, row + 320))
    encoded.operation = "MULTIPLY_ADD"
    encoded.inputs[1].default_value = 0.5
    encoded.inputs[2].default_value = 0.5
    _link(tree, root.outputs[0], encoded.inputs[0])

    combine = _new(tree, "ShaderNodeCombineColor", "NMG Normal", (column + 960, row + 320))
    _link(tree, separate.outputs["Red"], combine.inputs["Red"])
    _link(tree, separate.outputs["Green"], combine.inputs["Green"])
    _link(tree, encoded.outputs[0], combine.inputs["Blue"])

    normal_map = _new(tree, "ShaderNodeNormalMap", "Normal Map", (column + 1120, row + 320))
    normal_map.inputs["Strength"].default_value = plan.normal_strength
    _link(tree, combine.outputs["Color"], normal_map.inputs["Color"])
    _link(tree, normal_map.outputs["Normal"], _socket(principled, "Normal"))


def build_material(material, plan, *, metadata: dict | None = None):
    """Rebuild `material` from scratch following `plan`."""
    _clear_tree(material)
    tree = material.node_tree

    output = _new(tree, "ShaderNodeOutputMaterial", "Output", (_COLUMN_SHADER + 320, 0))
    principled = _new(tree, "ShaderNodeBsdfPrincipled", "Principled", (_COLUMN_SHADER, 0))
    _link(tree, principled.outputs["BSDF"], output.inputs["Surface"])

    base_color_socket = _socket(principled, "Base Color")
    if base_color_socket is not None:
        base_color_socket.default_value = plan.base_color
    for name, value in (
        ("Metallic", plan.metallic),
        ("Roughness", plan.roughness),
        ("Alpha", plan.alpha),
    ):
        socket = _socket(principled, name)
        if socket is not None:
            socket.default_value = value

    specular_socket = _socket(principled, "Specular IOR Level", "Specular")
    if specular_socket is not None:
        specular_socket.default_value = plan.specular
    specular_tint = _socket(principled, "Specular Tint")
    if specular_tint is not None and plan.workflow == "specular":
        try:
            specular_tint.default_value = plan.specular_color
        except (TypeError, ValueError):
            pass

    emission_socket = _socket(principled, "Emission Color", "Emission")
    emission_strength = _socket(principled, "Emission Strength")
    emissive = any(channel > 0.0 for channel in plan.emission_color[:3])
    if emission_socket is not None:
        emission_socket.default_value = plan.emission_color
    if emission_strength is not None:
        emission_strength.default_value = plan.emission_strength if emissive else 0.0

    coordinates = None
    mappings: dict[tuple, object] = {}
    row = 0
    base_color_output = None
    base_color_alpha = None

    for texture in plan.textures:
        image = _load_image(texture.image_path, texture.colorspace)
        if image is None:
            continue

        node = _new(
            tree,
            "ShaderNodeTexImage",
            f"{texture.role} {texture.property_name}",
            (_COLUMN_TEXTURE, row),
        )
        node.image = image
        row -= 320

        key = (texture.scale, texture.offset)
        if key != ((1.0, 1.0), (0.0, 0.0)):
            mapping = mappings.get(key)
            if mapping is None:
                if coordinates is None:
                    coordinates = _new(
                        tree,
                        "ShaderNodeTexCoord",
                        "Texture Coordinate",
                        (_COLUMN_TEXTURE - 620, 0),
                    )
                mapping = _new(
                    tree,
                    "ShaderNodeMapping",
                    f"Mapping {len(mappings) + 1}",
                    (_COLUMN_TEXTURE - 380, row + 320),
                )
                mapping.inputs["Scale"].default_value = (
                    texture.scale[0],
                    texture.scale[1],
                    1.0,
                )
                mapping.inputs["Location"].default_value = (
                    texture.offset[0],
                    texture.offset[1],
                    0.0,
                )
                _link(tree, coordinates.outputs["UV"], mapping.inputs["Vector"])
                mappings[key] = mapping
            _link(tree, mapping.outputs["Vector"], node.inputs["Vector"])

        if texture.role == profiles.BASE_COLOR:
            base_color_output = node.outputs["Color"]
            # `_BCH` stores height in alpha, and Unity uses albedo alpha for
            # smoothness when the channel switch says so: neither is opacity.
            if texture.packing != profiles.PACK_BCH and not plan.smoothness_from_albedo_alpha:
                base_color_alpha = node.outputs["Alpha"]
            if plan.smoothness_from_albedo_alpha:
                _connect_smoothness(tree, principled, node.outputs["Alpha"], plan, row)

        elif texture.role == profiles.NORMAL_MAP and texture.packing == profiles.PACK_NMG:
            _build_packed_normal(tree, principled, node, plan, row)

        elif texture.role == profiles.NORMAL_MAP:
            normal_map = _new(
                tree, "ShaderNodeNormalMap", "Normal Map", (_COLUMN_HELPER, row + 320)
            )
            normal_map.inputs["Strength"].default_value = plan.normal_strength
            _link(tree, node.outputs["Color"], normal_map.inputs["Color"])
            _link(tree, normal_map.outputs["Normal"], _socket(principled, "Normal"))

        elif texture.role == profiles.METALLIC_GLOSS:
            separate = _new(
                tree, "ShaderNodeSeparateColor", "Metallic Channels", (_COLUMN_HELPER, row + 320)
            )
            _link(tree, node.outputs["Color"], separate.inputs["Color"])
            _link(tree, separate.outputs["Red"], _socket(principled, "Metallic"))
            if not plan.smoothness_from_albedo_alpha:
                _connect_smoothness(tree, principled, node.outputs["Alpha"], plan, row)

        elif texture.role == profiles.SPECULAR_GLOSS:
            if specular_tint is not None:
                _link(tree, node.outputs["Color"], specular_tint)
            invert = _new(
                tree, "ShaderNodeMath", "Gloss To Roughness", (_COLUMN_MIX, row + 320)
            )
            invert.operation = "SUBTRACT"
            invert.inputs[0].default_value = 1.0
            _link(tree, node.outputs["Alpha"], invert.inputs[1])
            _link(tree, invert.outputs[0], _socket(principled, "Roughness"))

        elif texture.role == profiles.EMISSION:
            _link(tree, node.outputs["Color"], emission_socket)
            if emission_strength is not None:
                emission_strength.default_value = max(plan.emission_strength, 1.0)

        elif texture.role == profiles.OCCLUSION:
            node["ump_role"] = "occlusion"

    if base_color_output is not None and plan.tint_property:
        tint = _new(tree, "ShaderNodeMix", f"Tint {plan.tint_property}", (_COLUMN_MIX, 320))
        tint.data_type = "RGBA"
        tint.blend_type = "MULTIPLY"
        tint.inputs["Factor"].default_value = plan.tint_factor
        tint.inputs[7].default_value = plan.tint_color
        _link(tree, base_color_output, tint.inputs[6])
        base_color_output = tint.outputs[2]
        if not plan.tint_factor:
            # Kept at zero: the shader never proved it multiplies the albedo.
            tint.label = f"Tint {plan.tint_property} (off)"
            tint.use_custom_color = True
            tint.color = (0.35, 0.25, 0.25)

    if base_color_output is not None:
        occlusion = next(
            (
                node
                for node in tree.nodes
                if node.type == "TEX_IMAGE" and node.get("ump_role") == "occlusion"
            ),
            None,
        )
        if occlusion is not None:
            mix = _new(tree, "ShaderNodeMix", "Occlusion Mix", (_COLUMN_MIX, 160))
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = plan.occlusion_strength
            _link(tree, base_color_output, mix.inputs[6])
            _link(tree, occlusion.outputs["Color"], mix.inputs[7])
            base_color_output = mix.outputs[2]

        _link(tree, base_color_output, base_color_socket)

    if base_color_alpha is not None and plan.blend_mode != profiles.BLEND_OPAQUE:
        if plan.blend_mode == profiles.BLEND_CLIP:
            clip = _new(tree, "ShaderNodeMath", "Alpha Clip", (_COLUMN_MIX, -160))
            clip.operation = "GREATER_THAN"
            clip.inputs[1].default_value = plan.alpha_cutoff
            _link(tree, base_color_alpha, clip.inputs[0])
            _link(tree, clip.outputs[0], _socket(principled, "Alpha"))
        else:
            _link(tree, base_color_alpha, _socket(principled, "Alpha"))

    _apply_render_method(material, plan)

    for key, value in (metadata or {}).items():
        material[key] = value
    return material


def material_images(material) -> list:
    """Images used by a material node tree, for cleanup after writing a file."""
    if not material.use_nodes or material.node_tree is None:
        return []
    return [
        node.image
        for node in material.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image is not None
    ]


def image_paths(material) -> list[str]:
    return [
        bpy.path.abspath(image.filepath)
        for image in material_images(material)
        if image.filepath
    ]


def relink_images(material, old_root: str, new_root: str) -> int:
    """Repoint image paths when the Unity project moved to another location."""
    changed = 0
    old = Path(old_root)
    for image in material_images(material):
        current = Path(bpy.path.abspath(image.filepath))
        try:
            relative = current.relative_to(old)
        except ValueError:
            continue
        image.filepath = str(Path(new_root) / relative)
        changed += 1
    return changed
