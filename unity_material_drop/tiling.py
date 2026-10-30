"""Add editable UV tiling without discarding existing texture transforms."""

import bpy
from bpy.types import Operator, Panel

from . import registration
from .localization import LocalizedDescription, text as lt


def material_of(context):
    space = getattr(context, "space_data", None)
    if getattr(space, "type", "") == "NODE_EDITOR":
        material = getattr(space, "id", None)
        return material if isinstance(material, bpy.types.Material) else None
    material = getattr(context, "material", None)
    if material is not None:
        return material
    obj = context.active_object
    return obj.active_material if obj else None


def controls(tree):
    return next((n for n in tree.nodes if n.get("umd_tiling_controls") and n.type == "COMBXYZ"), None)


def add_tiling(material):
    tree = material.node_tree
    textures = [n for n in tree.nodes if n.type == "TEX_IMAGE"]
    if not textures:
        return 0
    scale = controls(tree)
    if scale is None:
        scale = tree.nodes.new("ShaderNodeCombineXYZ")
        scale.name = "UMD Tiling XY"
        scale.label = "Tiling X / Y"
        scale["umd_tiling_controls"] = True
        for socket in scale.inputs:
            socket.default_value = 1.0
        scale.location = (min(n.location.x for n in textures) - 600, max(n.location.y for n in textures) + 240)
    mappings = {}
    added = 0
    uv = None
    for texture in textures:
        vector = texture.inputs["Vector"]
        source = vector.links[0].from_socket if vector.is_linked else None
        if source is not None and source.node.get("umd_tiling_mapping"):
            continue
        key = source.as_pointer() if source is not None else None
        mapping = mappings.get(key)
        if mapping is None:
            mapping = tree.nodes.new("ShaderNodeMapping")
            mapping.name = "UMD Tiling Mapping"
            mapping.label = "UV Tiling"
            mapping["umd_tiling_mapping"] = True
            mapping.vector_type = "POINT"
            mapping.location = (texture.location.x - 320, texture.location.y)
            if source is None:
                if uv is None:
                    uv = tree.nodes.new("ShaderNodeTexCoord")
                    uv.name = "UMD Tiling Coordinates"
                    uv.location = (scale.location.x, scale.location.y - 260)
                source = uv.outputs["UV"]
            tree.links.new(source, mapping.inputs["Vector"])
            tree.links.new(scale.outputs["Vector"], mapping.inputs["Scale"])
            mappings[key] = mapping
        tree.links.new(mapping.outputs["Vector"], vector)
        if texture.projection == "FLAT":
            texture.extension = "REPEAT"
        added += 1
    return added


class UMD_OT_add_tiling(LocalizedDescription, Operator):
    bl_idname = "unity_material_drop.add_tiling"
    bl_label = "Add tiling nodes"
    bl_description = "Add shared X/Y tiling to image textures while preserving existing vector inputs"
    tooltip_ru = "Добавить общий тайлинг X/Y к текстурам, сохранив существующие подключения координат"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        material = material_of(context)
        return material is not None and material.library is None and material.node_tree is not None

    def execute(self, context):
        material = material_of(context)
        if not any(n.type == "TEX_IMAGE" for n in material.node_tree.nodes):
            self.report({"WARNING"}, lt("No image textures in this material", "В материале нет узлов текстур"))
            return {"CANCELLED"}
        added = add_tiling(material)
        self.report({"INFO"}, lt(f"Tiling: {added} textures connected", f"Тайлинг: подключено текстур — {added}"))
        return {"FINISHED"}


def draw_tiling(layout, context):
    layout.operator(UMD_OT_add_tiling.bl_idname, text=lt("Add tiling nodes", "Добавить тайлинг"), icon="NODETREE")
    material = material_of(context)
    scale = controls(material.node_tree) if material and material.node_tree else None
    if scale is not None:
        row = layout.row(align=True)
        row.prop(scale.inputs[0], "default_value", text="X")
        row.prop(scale.inputs[1], "default_value", text="Y")


class UMD_PT_shader_tiling(Panel):
    bl_label = "Tiling / Тайлинг"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Unity Drop"

    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == "ShaderNodeTree" and UMD_OT_add_tiling.poll(context)

    def draw(self, context):
        draw_tiling(self.layout, context)


class UMD_PT_material_tiling(Panel):
    bl_label = "Tiling / Тайлинг"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        return UMD_OT_add_tiling.poll(context)

    def draw(self, context):
        draw_tiling(self.layout, context)


_CLASSES = (UMD_OT_add_tiling, UMD_PT_shader_tiling, UMD_PT_material_tiling)


def register():
    registration.register_classes(_CLASSES)


def unregister():
    registration.unregister_classes(_CLASSES)
