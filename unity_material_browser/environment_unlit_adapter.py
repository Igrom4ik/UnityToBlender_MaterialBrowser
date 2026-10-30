"""Translate Custom/Environment/Unlit: opaque RGB = _BaseColor * _Tint."""
from pathlib import Path
import bpy

SHADER = "Custom/Environment/Unlit"
VERSION = "environment-unlit/1"

def supports(document):
    return bool(document and document.get("shader", {}).get("name") == SHADER)

def build(material, document, metadata=None):
    raw = document.get("material", {})
    effective = document.get("effective", {})
    tint = effective.get("colors", {}).get("_Tint", {}).get("value", raw.get("colors", {}).get("_Tint", [1, 1, 1, 0]))
    entry = raw.get("textures", {}).get("_BaseColor", {})
    path = entry.get("absolute_path")
    if entry.get("guid") and (not path or not Path(bpy.path.abspath(path)).is_file()):
        raise ValueError("Unlit requires resolved _BaseColor: " + str(path))
    if material.node_tree is None:
        material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (600, 0)
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.name = "Unity Unlit Emission"
    emission.location = (400, 0)
    emission.inputs["Strength"].default_value = 1
    tree.links.new(emission.outputs[0], output.inputs["Surface"])
    multiply = tree.nodes.new("ShaderNodeMixRGB")
    multiply.name = "Unity _BaseColor x _Tint"
    multiply.label = "_BaseColor × _Tint (RGB)"
    multiply.blend_type = "MULTIPLY"
    multiply.use_clamp = False
    multiply.inputs[0].default_value = 1
    multiply.inputs[1].default_value = (1, 1, 1, 1)
    multiply.inputs[2].default_value = tuple(tint[:3]) + (1,)
    multiply.location = (160, 0)
    tree.links.new(multiply.outputs[0], emission.inputs["Color"])
    if path:
        image = bpy.data.images.load(bpy.path.abspath(path), check_existing=True)
        colorspace = entry.get("meta", {}).get("colorspace", "sRGB")
        # The Unity shader ignores texture alpha entirely. Blender otherwise
        # premultiplies the Color output by alpha, even for an opaque shader.
        if image.colorspace_settings.name != colorspace or image.alpha_mode != "CHANNEL_PACKED":
            image = image.copy()
            image.colorspace_settings.name = colorspace
            image.alpha_mode = "CHANNEL_PACKED"
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.name = "Unity _BaseColor"
        texture.label = "_BaseColor"
        texture.image = image
        texture.extension = {"repeat": "REPEAT", "clamp": "EXTEND", "mirror": "MIRROR"}.get(entry.get("meta", {}).get("wrap_mode", "repeat").lower(), "REPEAT")
        texture.location = (-80, 0)
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.name = "Unity _BaseColor_ST"
        mapping.vector_type = "POINT"
        mapping.inputs["Scale"].default_value = (*entry.get("scale", [1, 1]), 1)
        mapping.inputs["Location"].default_value = (*entry.get("offset", [0, 0]), 0)
        mapping.location = (-330, 0)
        uv = tree.nodes.new("ShaderNodeUVMap")
        uv.name = "Unity UV0"
        uv.uv_map = document.get("blender", {}).get("uv_names", [""])[0]
        uv.location = (-550, 0)
        tree.links.new(uv.outputs["UV"], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
        tree.links.new(texture.outputs["Color"], multiply.inputs[1])
    material.use_backface_culling = True
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    for key, value in (metadata or {}).items():
        material[key] = value
    material["umd_shader_adapter"] = VERSION
    material["umd_unmapped"] = ""
    material["umd_shader_adapter_limitations"] = "Unity and Blender color management, tonemapping and texture filtering may differ."
    return material
