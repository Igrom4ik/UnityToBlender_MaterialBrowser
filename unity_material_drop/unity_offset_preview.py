"""Opt-in Geometry Nodes preview for Unity camera-relative vertex displacement.

This never runs during material import.  Register the operator and let the user
invoke it explicitly, after saving a full scene backup.  Current camera is the
scene render camera, not the interactive viewport navigation camera.
"""
import bpy

MODIFIER_NAME = 'Unity Camera Offset Preview'


def _parameters(obj, scene):
    if obj.type != 'MESH':
        raise ValueError('Object must be a mesh')
    if scene.camera is None:
        raise ValueError('Scene needs an active camera')
    if len(obj.material_slots) != 1 or obj.material_slots[0].material is None:
        raise ValueError('Exactly one assigned material slot is required')
    if obj.modifiers.get(MODIFIER_NAME):
        raise ValueError('Preview modifier already exists')
    mat = obj.material_slots[0].material
    if not mat.get('umd_geometry_offset_enabled', False):
        raise ValueError('Material does not enable Unity geometry offset')
    minimum = float(mat.get('umd_geometry_offset_min', .1))
    maximum = float(mat.get('umd_geometry_offset_max', 1.0))
    distance = float(mat.get('umd_geometry_offset_distance', 50.0))
    if distance <= 0:
        raise ValueError('Camera distance must be positive')
    if abs(obj.matrix_world.determinant()) < 1e-10:
        raise ValueError('Object transform must be invertible')
    return minimum, maximum, distance


def create_preview(obj, scene):
    """Apply a removable, non-destructive modifier after validating its target."""
    minimum, maximum, distance = _parameters(obj, scene)
    group = bpy.data.node_groups.new('Unity Camera Offset Preview', 'GeometryNodeTree')
    try:
        group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        nodes, links = group.nodes, group.links
        def node(kind, name):
            n = nodes.new(kind); n.label = name; return n
        def link(a, b): links.new(a, b)
        def connect(value, socket):
            if hasattr(value, 'node'): link(value, socket)
            else: socket.default_value = value
        def math_node(op, a, b=0):
            n=node('ShaderNodeMath', op);n.operation=op;connect(a,n.inputs[0]);connect(b,n.inputs[1]);return n.outputs[0]
        def vector_node(op, a, b=None):
            n=node('ShaderNodeVectorMath', op);n.operation=op;connect(a,n.inputs[0])
            if b is not None:connect(b,n.inputs[1])
            return n.outputs[0]
        inputs=node('NodeGroupInput','Original geometry');output=node('NodeGroupOutput','Preview geometry')
        self_node=node('GeometryNodeSelfObject','Object');camera=node('GeometryNodeInputActiveCamera','Scene camera')
        objinfo=node('GeometryNodeObjectInfo','Object world transform');objinfo.transform_space='ORIGINAL';link(self_node.outputs[0],objinfo.inputs['Object'])
        caminfo=node('GeometryNodeObjectInfo','Camera world transform');caminfo.transform_space='ORIGINAL';link(camera.outputs[0],caminfo.inputs['Object'])
        position=node('GeometryNodeInputPosition','Object-space position');world=node('FunctionNodeTransformPoint','Vertex in world');link(position.outputs[0],world.inputs['Vector']);link(objinfo.outputs['Transform'],world.inputs['Transform'])
        inverse_camera=node('FunctionNodeInvertMatrix','World to camera');link(caminfo.outputs['Transform'],inverse_camera.inputs['Matrix'])
        view=node('FunctionNodeTransformPoint','Vertex in camera space');link(world.outputs[0],view.inputs['Vector']);link(inverse_camera.outputs[0],view.inputs['Transform'])
        separate=node('ShaderNodeSeparateXYZ','Camera depth');link(view.outputs[0],separate.inputs[0])
        depth=math_node('MINIMUM',math_node('MAXIMUM',math_node('DIVIDE',math_node('ABSOLUTE',separate.outputs['Z']),distance),0),1)
        amount=math_node('ADD',minimum,math_node('MULTIPLY',maximum-minimum,math_node('MULTIPLY',depth,depth)))
        direction=vector_node('NORMALIZE',vector_node('SUBTRACT',caminfo.outputs['Location'],world.outputs[0]))
        scale=node('ShaderNodeVectorMath','Offset toward camera');scale.operation='SCALE';link(direction,scale.inputs[0]);link(amount,scale.inputs[3])
        inverse_object=node('FunctionNodeInvertMatrix','World to object');link(objinfo.outputs['Transform'],inverse_object.inputs['Matrix'])
        local=node('FunctionNodeTransformDirection','Offset in object space');link(scale.outputs[0],local.inputs['Direction']);link(inverse_object.outputs[0],local.inputs['Transform'])
        setp=node('GeometryNodeSetPosition','Preview displacement');link(inputs.outputs['Geometry'],setp.inputs['Geometry']);link(local.outputs[0],setp.inputs['Offset']);link(setp.outputs['Geometry'],output.inputs['Geometry'])
        modifier=obj.modifiers.new(MODIFIER_NAME,'NODES');modifier.node_group=group
    except Exception:
        bpy.data.node_groups.remove(group)
        raise
    return modifier


class OBJECT_OT_unity_camera_offset_preview(bpy.types.Operator):
    """Add a removable preview modifier; original mesh stays unchanged."""
    bl_idname='object.unity_camera_offset_preview'
    bl_label='Unity Camera Offset Preview'
    bl_options={'REGISTER','UNDO'}

    def execute(self, context):
        targets=list(context.selected_objects)
        if not targets:
            self.report({'ERROR'},'Select mesh objects');return {'CANCELLED'}
        try:
            for obj in targets:_parameters(obj,context.scene)
        except ValueError as ex:
            self.report({'ERROR'},f'{obj.name}: {ex}');return {'CANCELLED'}
        for obj in targets:create_preview(obj,context.scene)
        self.report({'INFO'},f'Added camera offset preview to {len(targets)} objects')
        return {'FINISHED'}


def register():bpy.utils.register_class(OBJECT_OT_unity_camera_offset_preview)
def unregister():bpy.utils.unregister_class(OBJECT_OT_unity_camera_offset_preview)
