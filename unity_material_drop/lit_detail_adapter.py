"""Project URP Lit Detail: material-side detail albedo and tangent normal."""
from pathlib import Path
import bpy

VERSION = 'urp-lit-detail/1'

def apply_detail(material, document, color, bs, base_normal, val):
    raw=document.get('material',{}); names=document.get('blender',{}).get('uv_names',['','UVMap_001']); textures=raw.get('textures',{}); keys=set(raw.get('keywords',{}).get('valid',[]))
    if not ({'_DETAIL_MULX2','_DETAIL_SCALED'} & keys):return color,[]
    tree=material.node_tree;warnings=[]
    def n(kind,label):
        a=tree.nodes.new(kind);a.name='Unity Detail '+label;a.label='Detail '+label;return a
    def put(v,s):
        if hasattr(v,'node'):tree.links.new(v,s)
        else:s.default_value=v
    def op(kind,a,b):
        v=n('ShaderNodeVectorMath',kind);v.operation=kind;put(a,v.inputs[0]);put(b,v.inputs[1]);return v.outputs[0]
    def math(kind,a,b):
        v=n('ShaderNodeMath',kind);v.operation=kind;put(a,v.inputs[0]);put(b,v.inputs[1]);return v.outputs[0]
    def scale(a,b):
        v=n('ShaderNodeVectorMath','Scale');v.operation='SCALE';put(a,v.inputs[0]);put(b,v.inputs['Scale']);return v.outputs[0]
    def uv():
        if document.get('blender',{}).get('uv_attributes'):
            a=n('ShaderNodeAttribute','UV');a.attribute_name=names[0];return a.outputs['Vector']
        a=n('ShaderNodeUVMap','UV');a.uv_map=names[0];return a.outputs['UV']
    def mapping(vec,entry,label):
        a=n('ShaderNodeMapping',label);a.inputs['Scale'].default_value=(*entry.get('scale',[1,1]),1);a.inputs['Location'].default_value=(*entry.get('offset',[0,0]),0);put(vec,a.inputs['Vector']);return a.outputs['Vector']
    def image(key, coords, default_space):
        e=textures.get(key,{}) or {};p=e.get('absolute_path')
        if not p or not Path(bpy.path.abspath(p)).is_file():
            if e.get('guid'):warnings.append('unresolved '+key)
            return None
        a=n('ShaderNodeTexImage',key);im=bpy.data.images.load(bpy.path.abspath(p),check_existing=True);space=e.get('meta',{}).get('colorspace',default_space)
        if im.colorspace_settings.name!=space or im.alpha_mode!='CHANNEL_PACKED':im=im.copy();im.colorspace_settings.name=space;im.alpha_mode='CHANNEL_PACKED'
        a.image=im;a.extension={'repeat':'REPEAT','clamp':'EXTEND','mirror':'MIRROR'}.get(e.get('meta',{}).get('wrap_mode','repeat').lower(),'REPEAT');put(coords,a.inputs['Vector']);return a
    # URP gets both maps through input.uv, which already includes BaseMap ST.
    base_uv=mapping(uv(),textures.get('_BaseMap',{}) or {},'BaseMap ST');detail_uv=mapping(base_uv,textures.get('_DetailAlbedoMap',{}) or {},'Albedo ST')
    mask=image('_DetailMask',base_uv,'Non-Color');alpha=mask.outputs['Alpha'] if mask else 1
    albedo=image('_DetailAlbedoMap',detail_uv,'sRGB');detail=albedo.outputs['Color'] if albedo else (.5,.5,.5)
    state=document.get('source',{}).get('color_space',{}).get('space','Unknown')
    if state=='Unknown':
        from unity_pipeline_core.color_space import project_settings
        state=project_settings(document.get('source',{}).get('absolute_path','')).get('space','Unknown')
    if state=='Unknown':warnings.append('Detail color space unknown; assuming linear')
    factor=2.0 if state=='Gamma' else 4.59479
    if albedo is None:
        warnings.append('Detail albedo uses approximate Unity builtin grey default')
    detail=scale(detail,factor)
    if '_DETAIL_SCALED' in keys:
        k=val('_DetailAlbedoMapScale',1);detail=op('ADD',scale(detail,k),(1-k,1-k,1-k))
    blended=op('ADD',(1,1,1),scale(op('SUBTRACT',detail,(1,1,1)),alpha))
    color=op('MULTIPLY',color,blended)
    detail_normal=image('_DetailNormalMap',detail_uv,'Non-Color')
    if detail_normal:
        # Source RGB normal images are unpacked in tangent space before RNM.
        def unpack(socket,strength):
            xyz=op('SUBTRACT',scale(socket,2),(1,1,1));split=n('ShaderNodeSeparateXYZ','Normal XYZ');put(xyz,split.inputs[0]);join=n('ShaderNodeCombineXYZ','Scaled normal');put(math('MULTIPLY',split.outputs['X'],strength),join.inputs['X']);put(math('MULTIPLY',split.outputs['Y'],strength),join.inputs['Y']);put(split.outputs['Z'],join.inputs['Z']);return join.outputs[0]
        bn=unpack(base_normal.outputs['Color'],val('_BumpScale',1)) if base_normal and ('_NORMALMAP' in keys or 'valid' not in raw.get('keywords',{})) else (0,0,1)
        dn=unpack(detail_normal.outputs['Color'],val('_DetailNormalMapScale',1));norm=n('ShaderNodeVectorMath','Normalize detail');norm.operation='NORMALIZE';put(dn,norm.inputs[0]);dn=norm.outputs[0]
        # Unity BlendNormalRNM: t=n1+(0,0,1); u=n2*(-1,-1,1); r=t*dot(t,u)/t.z-u.
        t=op('ADD',bn,(0,0,1));u=op('MULTIPLY',dn,(-1,-1,1));dot=n('ShaderNodeVectorMath','RNM dot');dot.operation='DOT_PRODUCT';put(t,dot.inputs[0]);put(u,dot.inputs[1]);sp=n('ShaderNodeSeparateXYZ','RNM denominator');put(t,sp.inputs[0]);rn=op('SUBTRACT',scale(t,math('DIVIDE',dot.outputs['Value'],sp.outputs['Z'])),u)
        blendedn=op('ADD',bn,scale(op('SUBTRACT',rn,bn),alpha));encoded=op('ADD',scale(blendedn,.5),(.5,.5,.5));normalnode=n('ShaderNodeNormalMap','RNM tangent normal');normalnode.uv_map='' if document.get('blender',{}).get('uv_attributes') else names[0];put(encoded,normalnode.inputs['Color']);put(normalnode.outputs['Normal'],bs.inputs['Normal'])
        warnings.append('Detail normals assume RGB source texture; platform-specific compression can differ')
    return color,warnings
