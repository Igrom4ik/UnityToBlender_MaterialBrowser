"""Custom/Unlit_URP: texture and HDR color with optional alpha clip."""
from pathlib import Path
import bpy
SHADER='Custom/Unlit_URP'
VERSION='custom-unlit-urp/1'
def supports(document):
    return bool(document and document.get('shader',{}).get('name') == SHADER)
def build(material,document,metadata=None):
    from unity_pipeline_core.color_space import shader_document
    document=shader_document(document)
    raw=document.get('material',{});effective=document.get('effective',{})
    def val(k,default):
        for cat in ('floats','ints','colors'):
            if k in effective.get(cat,{}):return effective[cat][k]['value']
            if k in raw.get(cat,{}):return raw[cat][k]
        return default
    entry=raw.get('textures',{}).get('_BaseMap',{});path=entry.get('absolute_path')
    if entry.get('guid') and (not path or not Path(bpy.path.abspath(path)).is_file()):
        raise ValueError('Unlit_URP: unresolved _BaseMap: '+str(path))
    material.use_nodes=True;t=material.node_tree;t.nodes.clear();n=t.nodes;l=t.links
    output=n.new('ShaderNodeOutputMaterial');em=n.new('ShaderNodeEmission');em.name='Unity URP Unlit';em.inputs['Strength'].default_value=1
    multiply=n.new('ShaderNodeMixRGB');multiply.name='Unity _BaseMap x _BaseColor';multiply.blend_type='MULTIPLY';multiply.inputs[0].default_value=1
    color=tuple(val('_BaseColor',(1,1,1,1)));multiply.inputs[1].default_value=(1,1,1,1);multiply.inputs[2].default_value=color;l.new(multiply.outputs[0],em.inputs['Color'])
    alpha=None
    if path:
        image=bpy.data.images.load(bpy.path.abspath(path),check_existing=True);space=entry.get('meta',{}).get('colorspace','sRGB')
        if image.colorspace_settings.name!=space or image.alpha_mode!='CHANNEL_PACKED':image=image.copy();image.colorspace_settings.name=space;image.alpha_mode='CHANNEL_PACKED'
        tex=n.new('ShaderNodeTexImage');tex.name='Unity _BaseMap';tex.image=image;tex.extension={'repeat':'REPEAT','clamp':'EXTEND','mirror':'MIRROR'}.get(entry.get('meta',{}).get('wrap_mode','repeat').lower(),'REPEAT')
        uv=n.new('ShaderNodeUVMap');uv.uv_map=document.get('blender',{}).get('uv_names',[''])[0];mp=n.new('ShaderNodeMapping');mp.inputs['Scale'].default_value=(*entry.get('scale',[1,1]),1);mp.inputs['Location'].default_value=(*entry.get('offset',[0,0]),0);l.new(uv.outputs['UV'],mp.inputs['Vector']);l.new(mp.outputs['Vector'],tex.inputs['Vector']);l.new(tex.outputs['Color'],multiply.inputs[1]);alpha=tex.outputs['Alpha']
    keys=raw.get('keywords',{});clip=('_ALPHATEST_ON' in keys.get('valid',[])) if 'valid' in keys else bool(val('_AlphaClip',0))
    if clip:
        a=n.new('ShaderNodeMath');a.operation='MULTIPLY';a.inputs[0].default_value=1;a.inputs[1].default_value=color[3]
        if alpha:l.new(alpha,a.inputs[0])
        c=n.new('ShaderNodeMath');c.operation='LESS_THAN';c.inputs[1].default_value=val('_Cutoff',.5);l.new(a.outputs[0],c.inputs[0]);mix=n.new('ShaderNodeMixShader');clear=n.new('ShaderNodeBsdfTransparent');l.new(c.outputs[0],mix.inputs[0]);l.new(em.outputs[0],mix.inputs[1]);l.new(clear.outputs[0],mix.inputs[2]);l.new(mix.outputs[0],output.inputs['Surface'])
    else:l.new(em.outputs[0],output.inputs['Surface'])
    material.use_backface_culling=True
    if hasattr(material,'surface_render_method'):material.surface_render_method='DITHERED'
    for k,v in (metadata or {}).items():material[k]=v
    material['umd_shader_adapter']=VERSION;material['umd_unmapped']='';material['umd_shader_adapter_limitations']='Unity fog, color management and texture filtering may differ.'
    return material
