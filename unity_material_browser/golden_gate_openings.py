"""Ручная подготовка границ окон для материала из библиотеки Unity."""
import bpy
from . import window_opening_attributes as prep
from .localization import text as lt

GUID='8427edd0c3070f14a902cd77d3cfa3ff'


def draw_button(panel,context):
    panel.layout.operator('umb.prepare_golden_gate_openings',
                          text=lt('Prepare window openings','Подготовить проёмы окон'), icon='UV')


class UMB_OT_PrepareGoldenGateOpenings(bpy.types.Operator):
    bl_idname='umb.prepare_golden_gate_openings'
    bl_label='Подготовить проёмы окон'
    bl_description='Вычислить границы проёмов по граням и UV активного объекта для материала Golden Gate'
    bl_options={'REGISTER','UNDO'}

    @classmethod
    def poll(cls,context):
        return context.mode=='OBJECT' and context.active_object is not None and context.active_object.type=='MESH'

    def execute(self,context):
        obj=context.active_object
        if obj.data.users != 1:
            self.report({'ERROR'},'Меш используется несколькими объектами; сначала сделайте независимую копию')
            return {'CANCELLED'}
        slots=[i for i,s in enumerate(obj.material_slots) if s.material and
               str(s.material.get('ump_unity_guid') or s.material.get('umd_guid') or '').casefold()==GUID]
        if len(slots)!=1:
            self.report({'ERROR'},'На объекте должен быть один слот материала M_Golden_gate_glass_depth_02')
            return {'CANCELLED'}
        try:
            groups=prep.opening_groups(obj,slots[0])
            prep.apply_groups(obj,groups)
        except (ValueError,RuntimeError) as error:
            self.report({'ERROR'},str(error))
            return {'CANCELLED'}
        self.report({'INFO'},'Подготовлено проёмов: %d; грани: %d' % (len(groups),sum(len(g['faces']) for g in groups)))
        return {'FINISHED'}
