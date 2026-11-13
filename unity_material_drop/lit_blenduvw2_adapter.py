"""Extend the generic URP Lit conversion for MadOut's Lit_BlendUVW2."""
from pathlib import Path
import bpy
SHADERS={'Custom/Environment/Lit_BlendUVW2', 'Custom/Environment/Lit_BlendUVW2_Extra', 'Custom/Environment/Lit_BlendUVW2_Detail'}
VERSION='urp-lit-blenduvw2/3'
def supports(document):
    return bool(document and document.get('shader',{}).get('name') in SHADERS)
def apply(material,document):
    from unity_pipeline_core.color_space import shader_document
    document=shader_document(document)
    tree=material.node_tree;bs=next(n for n in tree.nodes if n.type=='BSDF_PRINCIPLED')
    raw=document.get('material',{});effective=document.get('effective',{})
    def val(key,default=0):
        for cat in ('floats','ints','colors'):
            if key in effective.get(cat,{}):return effective[cat][key]['value']
            if key in raw.get(cat,{}):return raw[cat][key]
        return default
    def node(typ,label):
        n=tree.nodes.new(typ);n.name='Unity '+label;n.label=label;return n
    def put(v,s):
        if hasattr(v,'node'):tree.links.new(v,s)
        else:s.default_value=v
    def op(kind,a,b=0):
        n=node('ShaderNodeMath',kind);n.operation=kind;put(a,n.inputs[0]);put(b,n.inputs[1]);return n.outputs[0]
    def vec(kind,a,b):
        n=node('ShaderNodeVectorMath',kind);n.operation=kind;put(a,n.inputs[0]);put(b,n.inputs[1]);return n.outputs[0]
    def scale(a,b):
        n=node('ShaderNodeVectorMath','Scale');n.operation='SCALE';put(a,n.inputs[0]);put(b,n.inputs['Scale']);return n.outputs[0]
    def image(prop):return next((n for n in tree.nodes if n.type=='TEX_IMAGE' and n.name.endswith(' '+prop)),None)
    base=image('_BaseMap');normal=image('_BumpMap');surface=bs.inputs['Base Color'];color=surface.links[0].from_socket if surface.is_linked else tuple(surface.default_value)[:3]
    detail_warnings=[]
    if document.get('shader',{}).get('name') == 'Custom/Environment/Lit_BlendUVW2_Detail':
        from .lit_detail_adapter import apply_detail
        color, detail_warnings = apply_detail(material, document, color, bs, normal, val)
    mode=val('_TintMode')
    if mode>.5:
        sep=node('ShaderNodeSeparateColor','Tint input');sep.mode='RGB' if mode<1.5 else 'HSV';put(color,sep.inputs[0])
        comb=node('ShaderNodeCombineColor','Tint result');comb.mode=sep.mode;sat=val('_TintSaturation',1);v=val('_TintValue',1)
        if mode<1.5:
            peak=op('MAXIMUM',op('MAXIMUM',sep.outputs[0],sep.outputs[1]),sep.outputs[2])
            for i in range(3):put(op('MULTIPLY',op('ADD',op('MULTIPLY',peak,1-sat),op('MULTIPLY',sep.outputs[i],sat)),v),comb.inputs[i])
        else:
            put(op('ADD',sep.outputs[0],val('_TintHue')),comb.inputs[0]);put(op('MULTIPLY',sep.outputs[1],sat),comb.inputs[1]);put(op('MULTIPLY',sep.outputs[2],v),comb.inputs[2])
        color=vec('ADD',comb.outputs[0],tuple(val('_TintColorAdd',(0,0,0,0)))[:3])
    if val('_AlphaColorOn') and base:
        alt=tuple(a*b for a,b in zip(tuple(val('_AlphaColor',(0,0,0,0)))[:3],tuple(val('_BaseColor',(1,1,1,1)))[:3]));alpha=base.outputs['Alpha']
        color=vec('ADD',scale(color,alpha),scale(alt,op('SUBTRACT',1,alpha)))
    # The base shader compiles this branch out; only Extra and Detail contain it.
    mask_variant=document.get('shader',{}).get('name') in {'Custom/Environment/Lit_BlendUVW2_Extra','Custom/Environment/Lit_BlendUVW2_Detail'}
    mask_missing=False
    if mask_variant and val('_SpecularMaskOn') > .5:
        entry=raw.get('textures',{}).get('_SpecularMask',{});mask_path=entry.get('absolute_path')
        if mask_path and Path(bpy.path.abspath(mask_path)).is_file():
            tex=node('ShaderNodeTexImage','Specular mask');tex.image=bpy.data.images.load(bpy.path.abspath(mask_path),check_existing=True)
            space=entry.get('meta',{}).get('colorspace','sRGB')
            if tex.image.colorspace_settings.name!=space:
                tex.image=tex.image.copy();tex.image.colorspace_settings.name=space
            uvname=document.get('blender',{}).get('uv_names',['','UVMap_001'])[0]
            if document.get('blender',{}).get('uv_attributes'):
                uv=node('ShaderNodeAttribute','Specular mask UV');uv.attribute_name=uvname;uvout=uv.outputs['Vector']
            else:
                uv=node('ShaderNodeUVMap','Specular mask UV');uv.uv_map=uvname;uvout=uv.outputs['UV']
            mapping=node('ShaderNodeMapping','Specular mask ST');mapping.inputs['Scale'].default_value=(*entry.get('scale',[1,1]),1);mapping.inputs['Location'].default_value=(*entry.get('offset',[0,0]),0)
            tree.links.new(uvout,mapping.inputs['Vector']);tree.links.new(mapping.outputs['Vector'],tex.inputs['Vector'])
            mask_color=tex.outputs['Color'];channels=node('ShaderNodeSeparateColor','Specular mask channels');put(mask_color,channels.inputs['Color']);mask_red=channels.outputs['Red']
        else:
            # The Unity shader's texture default is white. A non-empty unresolved GUID is a real missing dependency.
            mask_color=(1,1,1);mask_red=1;mask_missing=bool(entry.get('guid'))
        color=vec('MULTIPLY',color,vec('ADD',mask_color,(1,1,1)))
        put(op('SUBTRACT',1,op('MULTIPLY',mask_red,val('_Smoothness',.5))),bs.inputs['Roughness'])
    spec=bs.inputs.get('Specular IOR Level');f0=val('_DielectricF0',.04)/.08
    if spec:put(op('MULTIPLY',base.outputs['Alpha'],f0) if base and val('_SpecFromAlbedoAlpha') else f0,spec)
    textures=raw.get('textures',{});ao=textures.get('_Blend_UVW2_Texture',{});path=ao.get('absolute_path');keys=set(raw.get('keywords',{}).get('valid',[]));warnings=[]
    if path and Path(bpy.path.abspath(path)).is_file() and '_BLEND_UVW2_ON' in keys:
        n=node('ShaderNodeTexImage','UV1 baked AO');n.image=bpy.data.images.load(bpy.path.abspath(path),check_existing=True)
        space=ao.get('meta',{}).get('colorspace','sRGB')
        if n.image.colorspace_settings.name != space:
            n.image=n.image.copy();n.image.colorspace_settings.name=space
        uv=node('ShaderNodeUVMap','UV1 baked AO coordinates');names=document.get('blender',{}).get('uv_names',['','UVMap_001']);uv.uv_map=names[1] if len(names)>1 else 'UVMap_001';tree.links.new(uv.outputs['UV'],n.inputs['Vector']);color=vec('MULTIPLY',color,n.outputs['Color'])
    if hasattr(color,'node'):tree.links.new(color,surface)
    elif len(color)==3:surface.default_value=(*color,1)
    if normal and base and not val('_NormalOwnST'):
        if normal.inputs['Vector'].is_linked:tree.links.remove(normal.inputs['Vector'].links[0])
        if base.inputs['Vector'].is_linked:tree.links.new(base.inputs['Vector'].links[0].from_socket,normal.inputs['Vector'])
    if mask_missing:warnings.append('unresolved _SpecularMask; shader default white used')
    warnings.extend(detail_warnings)
    for key in ('_GeometryZOffset','_InterlacedClip','_VertexColorAlbedo'):
        if val(key):warnings.append(key)
    if path and '_BLEND_UVW2_ON' not in keys:warnings.append('AO texture present without _BLEND_UVW2_ON')
    if path and not Path(bpy.path.abspath(path)).is_file():warnings.append('unresolved UV1 AO texture')
    warnings.extend(['AOStreamingUV2 runtime material property block unavailable from .mat','Unity BRDF and lighting approximated'])
    material['umd_geometry_offset_enabled'] = ('_GEOMETRYZOFFSET_ON' in keys) if 'valid' in raw.get('keywords',{}) else bool(val('_GeometryZOffset'))
    material['umd_geometry_offset_min'] = float(val('_MinZOffset', .1))
    material['umd_geometry_offset_max'] = float(val('_MaxZOffset', 1.0))
    material['umd_geometry_offset_distance'] = float(val('_ZOffsetCameraDistance', 50.0))
    material['umd_shader_adapter']=VERSION;material['umd_shader_adapter_limitations']='; '.join(warnings)
    return material
