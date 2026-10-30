"""Prepare mesh attributes and explicitly repair cached vertex-paint materials."""
from array import array
from pathlib import Path
import bpy
from bpy.types import Operator, Panel
from . import registration
from .localization import LocalizedDescription, text as lt
from .tiling import material_of


def ensure_color_attribute(obj):
    if obj is None or obj.type != 'MESH' or obj.data.library:
        return None
    attrs=obj.data.color_attributes
    attr=attrs.active_color
    if attr is None and len(attrs): attr=attrs[0]
    if attr is None:
        attr=attrs.new(name='Color',type='FLOAT_COLOR',domain='CORNER')
        attr.data.foreach_set('color',array('f',[0,0,0,1])*len(attr.data))
    index=list(attrs).index(attr)
    attrs.active_color_index=index
    attrs.render_color_index=index
    return attr


def supported(material):
    return material is not None and (material.get('vertex_paint_shader')=='Custom/VertexColorMix4_v2' or material.get('umd_shader_name')=='Custom/VertexColorMix4_v2' or material.get('ump_shader_name')=='Custom/VertexColorMix4_v2')


class UMD_OT_prepare_vertex_paint(LocalizedDescription,Operator):
    bl_idname='unity_material_drop.prepare_vertex_paint'
    bl_label='Prepare Vertex Paint'
    bl_description='Create a black color attribute if missing, activate it for painting and rendering, and repair old imported shader graphs'
    tooltip_ru='Создать чёрный атрибут цвета при его отсутствии, активировать для рисования и рендера, обновить старый импортированный граф'
    bl_options={'REGISTER','UNDO'}

    @classmethod
    def poll(cls,context):
        obj=context.active_object;m=material_of(context)
        return obj is not None and obj.type=='MESH' and obj.mode=='OBJECT' and obj.data.library is None and m is not None and m.library is None and supported(m)

    def execute(self,context):
        from . import properties,session,node_builder,__version__
        from ._core import converter,fast_index
        m=material_of(context)
        if m.get('umd_vertex_graph_version',0)<2:
            source=Path(str(m.get('umd_source','')))
            if not source.is_file() or source.suffix.lower()!='.mat':
                self.report({'ERROR'},lt('Reload this material from Unity first','Сначала пересоберите материал из Unity'))
                return {'CANCELLED'}
            prefs=properties.get_preferences(context)
            root=fast_index.assets_root_of(source)
            index=session.index_for(root,properties.cache_directory(prefs)) if root and prefs else None
            c,_=converter.resolve_and_convert(source,index=index,assets_root=root)
            if not getattr(c.plan,'vertex_paint',None) or c.missing_guids:
                self.report({'ERROR'},lt('Shader or textures are unresolved; index the Unity project','Шейдер или текстуры не найдены: индексируйте проект Unity'))
                return {'CANCELLED'}
            node_builder.build_material(m,c.plan,metadata=converter.metadata(c,builder_version=__version__), document=c.document)
        attr=ensure_color_attribute(context.active_object)
        if attr is None: return {'CANCELLED'}
        for n in m.node_tree.nodes:
            if n.type=='VERTEX_COLOR': n.layer_name=attr.name
        self.report({'INFO'},lt('Ready: paint black / red / green / blue', 'Готово: рисуйте чёрным / красным / зелёным / синим'))
        return {'FINISHED'}


def draw(layout,context):
    layout.operator(UMD_OT_prepare_vertex_paint.bl_idname,text=lt('Prepare Vertex Paint','Подготовить Vertex Paint'),icon='BRUSH_DATA')
    obj=context.active_object
    if obj and obj.type=='MESH' and obj.data.color_attributes.active_color:
        layout.label(text=obj.data.color_attributes.active_color.name,icon='GROUP_VCOL')
    m=material_of(context)
    if m and m.node_tree:
        for ch in ['Red','Green','Blue']:
            n=next((n for n in m.node_tree.nodes if n.name.endswith('_'+ch+'MaskIntensity')),None)
            if n: layout.prop(n.outputs[0],'default_value',text=ch)
    layout.label(text=lt('Black = base. RGB = painted layers.','Чёрный = основа. RGB = слои.'))


class UMD_PT_vertex_paint(Panel):
    bl_label='Vertex Paint'
    bl_space_type='NODE_EDITOR';bl_region_type='UI';bl_category='Unity Drop'
    @classmethod
    def poll(cls,context): return context.space_data.tree_type=='ShaderNodeTree' and supported(material_of(context))
    def draw(self,context): draw(self.layout,context)


class UMD_PT_material_vertex_paint(Panel):
    bl_label='Vertex Paint'
    bl_space_type='PROPERTIES';bl_region_type='WINDOW';bl_context='material'
    @classmethod
    def poll(cls,context): return supported(material_of(context))
    def draw(self,context): draw(self.layout,context)


_CLASSES=(UMD_OT_prepare_vertex_paint,UMD_PT_vertex_paint,UMD_PT_material_vertex_paint)
def register(): registration.register_classes(_CLASSES)
def unregister(): registration.unregister_classes(_CLASSES)
