"""Unity Window view-ray box interior using explicit source UV4 attributes."""
from pathlib import Path
import json
import bpy
SHADER='Custom/Environment/Window_URP'
VERSION='window-urp-ray-box/1'

def build_window(material,plan,document,metadata=None):
    if document.get('shader',{}).get('name')!=SHADER:return False
    from unity_pipeline_core.color_space import shader_document
    document=shader_document(document)
    raw=document.get('material',{});eff=document.get('effective',{})
    keys=raw.get('keywords',{}); authored='valid' in keys; keywords=set(keys.get('valid',[]))
    def val(k,d):
        for cat in ('floats','ints','colors'):
            if k in eff.get(cat,{}):return eff[cat][k]['value']
            if k in raw.get(cat,{}):return raw[cat][k]
        return d
    textures=raw.get('textures',{});images={}
    for key in ('_MainTex','_Normals'):
        p=textures.get(key,{}).get('absolute_path')
        if not p or not Path(bpy.path.abspath(p)).is_file():
            if textures.get(key,{}).get('guid'):raise ValueError('Window texture unresolved '+key+': '+str(p))
            images[key]=None;continue
        image=bpy.data.images.load(bpy.path.abspath(p),check_existing=True);space='sRGB' if key=='_MainTex' else 'Non-Color'
        if image.colorspace_settings.name!=space or image.alpha_mode!='CHANNEL_PACKED':
            image=image.copy();image.colorspace_settings.name=space;image.alpha_mode='CHANNEL_PACKED'
        images[key]=image
    if material.node_tree is None:material.use_nodes=True
    nt=material.node_tree;nt.nodes.clear();counter=0
    def n(kind,label):
        nonlocal counter
        node=nt.nodes.new(kind);node.name='Unity Window '+label;node.label=label;node.location=(-1800+(counter%12)*240,-(counter//12)*230);counter+=1;return node
    def put(v,s):
        if hasattr(v,'node'):nt.links.new(v,s)
        else:s.default_value=v
    def m(kind,a,b=0):
        x=n('ShaderNodeMath',kind);x.operation=kind;put(a,x.inputs[0]);put(b,x.inputs[1]);return x.outputs[0]
    def clamp(a):return m('MINIMUM',m('MAXIMUM',a,0),1)
    def combine(x,y,z):
        node=n('ShaderNodeCombineXYZ','Combine');[put(v,node.inputs[i]) for i,v in enumerate((x,y,z))];return node.outputs[0]
    def vector(kind,a,b):
        node=n('ShaderNodeVectorMath',kind);node.operation=kind;put(a,node.inputs[0]);put(b,node.inputs[1]);return node.outputs['Value'] if kind in ('DOT_PRODUCT','DISTANCE') else node.outputs[0]
    def split(v):
        node=n('ShaderNodeSeparateXYZ','Components');put(v,node.inputs[0]);return list(node.outputs)
    def attr(name):
        node=n('ShaderNodeAttribute',name);node.attribute_name=name;return node.outputs['Vector']
    def mix(a,b,t):
        node=n('ShaderNodeMixRGB','Lerp');put(t,node.inputs[0]);put(a,node.inputs[1]);put(b,node.inputs[2]);return node.outputs[0]
    def mul(a,b):
        node=n('ShaderNodeMixRGB','Multiply');node.blend_type='MULTIPLY';node.inputs[0].default_value=1;put(a,node.inputs[1]);put(b,node.inputs[2]);return node.outputs[0]
    uvnode=n('ShaderNodeUVMap','Live primary UV')
    uvnode.uv_map=document.get('blender',{}).get('primary_uv_map','')
    uv=split(uvnode.outputs['UV'])
    geo=n('ShaderNodeNewGeometry','Surface and view')
    # NormalMap uses the native UV tangent basis including mirrored island sign.
    def tangent_axis(name,color):
        axis=n('ShaderNodeNormalMap',name);axis.space='TANGENT'
        axis.uv_map=uvnode.uv_map;axis.inputs['Color'].default_value=color
        return axis.outputs['Normal']
    t=tangent_axis('UV tangent axis',(1,.5,.5,1))
    bitan=tangent_axis('UV bitangent axis',(.5,1,.5,1))
    normal=geo.outputs['Normal']
    view=[vector('DOT_PRODUCT',geo.outputs['Incoming'],d) for d in (t,bitan,normal)]
    camera=n('ShaderNodeCameraData','Camera distance');fade=m('SUBTRACT',1,clamp(m('DIVIDE',m('SUBTRACT',camera.outputs['View Distance'],20),20)))
    fake=('_USEFAKEDEPTH_ON' in keywords) if authored else bool(val('_UseFakeDepth',0));depth=val('_Depth',.15)
    if fake:
        uvscale=split(attr('UnityWindowUV0ZW'));amin=split(attr('UnityWindowAtlasMin'));amax=split(attr('UnityWindowAtlasMax'))
        size=[m('SUBTRACT',amax[i],amin[i]) for i in range(2)]+[1]
        start=[m('DIVIDE',m('SUBTRACT',uv[i],amin[i]),size[i]) for i in range(2)]+[0]
        ray=[m('DIVIDE',m('MULTIPLY',view[i],m('MULTIPLY',uvscale[i],-1) if i<2 else -1),size[i]) for i in range(3)]
        bounds=[1,1,-depth]
        # Blender DIVIDE returns zero for zero denominator. Unity reciprocal
        # tends to infinity; finite signed epsilon preserves the slab minimum.
        safe_ray=[m('MULTIPLY',m('SUBTRACT',1,m('MULTIPLY',2,m('LESS_THAN',r,0))),m('MAXIMUM',m('ABSOLUTE',r),1e-6)) for r in ray]
        far=[m('MAXIMUM',m('DIVIDE',m('SUBTRACT',bounds[i],start[i]),safe_ray[i]),m('DIVIDE',m('MULTIPLY',start[i],-1),safe_ray[i])) for i in range(3)]
        hitdist=m('MINIMUM',m('MINIMUM',far[0],far[1]),far[2]);hit=[m('ADD',start[i],m('MULTIPLY',hitdist,ray[i])) for i in range(3)]
        entry=textures.get('_MainTex',{});scale=entry.get('scale',[1,1]);offset=entry.get('offset',[0,0]);sample=[m('ADD',m('MULTIPLY',m('ADD',amin[i],m('MULTIPLY',hit[i],size[i])),scale[i]),offset[i]) for i in range(2)]
        wallmask=m('SUBTRACT',1,m('MULTIPLY',m('CEIL',clamp(m('SUBTRACT',m('ADD',hit[2],depth),.01))),fade))
    else:hit=[*uv[:2],0];sample=uv[:2];wallmask=1
    sampled={}
    for key,image in images.items():
        if image is None:sampled[key]=None;continue
        node=n('ShaderNodeTexImage',key);node.image=image;node.extension='REPEAT';put(combine(*sample,0),node.inputs['Vector']);sampled[key]=node
    normal_sample=sampled['_Normals'];main_sample=sampled['_MainTex']
    rgb=split(normal_sample.outputs['Color']) if normal_sample else [.5,.5,1]
    texture_normal=[m('SUBTRACT',m('MULTIPLY',c,2),1) for c in rgb]
    usenorm=('_USENORMALSMAP_ON' in keywords) if authored else bool(val('_UseNormalsMap',0))
    if fake:
        transformed=[m('SUBTRACT',m('MULTIPLY',x,2),1) for x in hit];side=m('MAXIMUM',m('SIGN',m('SUBTRACT',m('ABSOLUTE',transformed[0]),m('ABSOLUTE',transformed[1]))),0)
        face=[m('MULTIPLY',m('SIGN',transformed[0]),m('MULTIPLY',side,-1)),m('MULTIPLY',m('SIGN',transformed[1]),m('SUBTRACT',side,1)),0]
        target=[m('ADD',0 if i<2 else 1,m('MULTIPLY',m('SUBTRACT',texture_normal[i],0 if i<2 else 1),fade)) if usenorm else (0 if i<2 else 1) for i in range(3)]
        final=[m('ADD',face[i],m('MULTIPLY',m('SUBTRACT',target[i],face[i]),wallmask)) for i in range(3)]
    else:final=texture_normal
    world=None
    for direction,weight in zip((t,bitan,normal),final):
        term=vector('MULTIPLY',direction,combine(weight,weight,weight));world=term if world is None else vector('ADD',world,term)
    norm=n('ShaderNodeVectorMath','Interior normal');norm.operation='NORMALIZE';put(world,norm.inputs[0])
    bs=n('ShaderNodeBsdfPrincipled','Principled approximation');put(norm.outputs[0],bs.inputs['Normal'])
    base=mix(tuple(val('_WallColor',(1,1,1,0))),main_sample.outputs['Color'] if main_sample else (1,1,1,1),wallmask);put(mul(base,tuple(val('_TintColor',(.5,.5,.5,0)))),bs.inputs['Base Color'])
    keywords=set(raw.get('keywords',{}).get('valid',[]));normalalpha='_SMOOTHNESSSOURCE_NORMALSALPHA' in keywords or (not authored and val('_SmoothnessSource',0)==1)
    mainalpha=main_sample.outputs['Alpha'] if main_sample else 1
    normal_source=0 if usenorm else 1
    alpha=normal_source if normalalpha else mainalpha
    put(m('SUBTRACT',1,clamp(m('MULTIPLY',alpha,val('_Smoothness',1)*.95))),bs.inputs['Roughness']);bs.inputs['Metallic'].default_value=0
    nightalpha=normal_source if '_NIGHTEMISSIONSOURCE_ALBEDOALPHA' in keywords else mainalpha
    night_on=1 if val('_NightLighting',0)>=.5 else 0
    nightcolor=tuple(val('_WindowLightTintColor',(1,1,1,0)))
    emission=n('ShaderNodeMixRGB','Night emission');emission.blend_type='MULTIPLY';emission.inputs[0].default_value=1;put(nightcolor,emission.inputs[1]);put(combine(nightalpha,nightalpha,nightalpha),emission.inputs[2]);emission.use_clamp=True;put(emission.outputs[0],bs.inputs['Emission Color']);bs.inputs['Emission Strength'].default_value=night_on
    out=n('ShaderNodeOutputMaterial','Output');put(bs.outputs[0],out.inputs['Surface']);material.use_backface_culling=True
    for k,v in (metadata or {}).items():material[k]=v
    material['umd_shader_adapter']=VERSION;material['umd_unmapped']='Fake depth requires UnityWindow UV4 attributes on mesh' if fake else ''
    material['umd_window_uv4_required']=fake
    material['umd_geometry_offset_enabled']=('_GEOMETRYZOFFSET_ON' in keywords) if authored else bool(val('_GeometryZOffset',0))
    material['umd_geometry_offset_min']=float(val('_MinZOffset',.1));material['umd_geometry_offset_max']=float(val('_MaxZOffset',1));material['umd_geometry_offset_distance']=float(val('_ZOffsetCameraDistance',50))
    material['umd_shader_adapter_limitations']=json.dumps(['Custom per-light box shadows/GGX use Principled approximation; Unity light uniforms unavailable.','Shader distance fade interpolated per-pixel rather than per-vertex.','Unity and Blender tangent generation may differ across triangulation.','Camera offset is preview-only via Unity Camera Offset Preview.', 'Fake depth requires mesh corner attributes UnityWindowUV0ZW, UnityWindowAtlasMin and UnityWindowAtlasMax; standard UV maps do not preserve UV4.', 'Night lighting and direct illumination use Principled approximation.'])
    return True

