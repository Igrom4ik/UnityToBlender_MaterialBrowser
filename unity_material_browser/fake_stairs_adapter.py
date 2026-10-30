"""View-dependent node translation of Custom/Environment/FakeStairs surf()."""
from pathlib import Path
import json
import bpy

SHADER = 'Custom/Environment/FakeStairs'


def build_fake_stairs(material, plan, document, metadata=None):
    if document.get('shader', {}).get('name') != SHADER:
        return False
    raw=document.get('material',{});effective=document.get('effective',{})
    def val(k,d):
        for category in ('floats','ints','colors'):
            if k in effective.get(category,{}):return effective[category][k]['value']
            if k in raw.get(category,{}):return raw[category][k]
        return d
    entry=raw.get('textures',{}).get('_MainTex',{})
    path=entry.get('absolute_path')
    if not path or not Path(bpy.path.abspath(path)).is_file():
        raise ValueError('FakeStairs requires resolved _MainTex: '+str(path))
    count=float(val('_StepsCount',0))
    if abs(count)<1e-8:raise ValueError('FakeStairs _StepsCount=0 is undefined in Unity shader')
    scale=entry.get('scale',[1,1]);offset=entry.get('offset',[0,0])
    if abs(scale[0])<1e-8 or abs(scale[1])<1e-8:raise ValueError('FakeStairs zero texture scale is undefined')
    image=bpy.data.images.load(bpy.path.abspath(path),check_existing=True)
    if image.colorspace_settings.name!='sRGB':image=image.copy();image.colorspace_settings.name='sRGB'
    if material.node_tree is None:material.use_nodes=True
    nt=material.node_tree;nt.nodes.clear();counter=0
    def node(kind,label):
        nonlocal counter
        n=nt.nodes.new(kind);n.name='Unity FakeStairs '+label;n.label=label;n.location=(-1600+(counter%10)*240,-(counter//10)*230);counter+=1;return n
    def assign(v,s):
        if hasattr(v,'node'):nt.links.new(v,s)
        else:s.default_value=v
    def op(kind,a,b=0):
        n=node('ShaderNodeMath',kind);n.operation=kind;assign(a,n.inputs[0]);assign(b,n.inputs[1]);return n.outputs[0]
    def vec(x,y,z):
        n=node('ShaderNodeCombineXYZ','Vector');assign(x,n.inputs[0]);assign(y,n.inputs[1]);assign(z,n.inputs[2]);return n.outputs[0]
    def vop(kind,a,b):
        n=node('ShaderNodeVectorMath',kind);n.operation=kind;assign(a,n.inputs[0]);assign(b,n.inputs[1]);return n.outputs['Value'] if kind=='DOT_PRODUCT' else n.outputs[0]
    def lerp(a,b,t):
        n=node('ShaderNodeMixRGB','lerp');assign(t,n.inputs[0]);assign(a,n.inputs[1]);assign(b,n.inputs[2]);return n.outputs[0]
    def mulcolor(a,b):
        n=node('ShaderNodeMixRGB','Multiply');n.blend_type='MULTIPLY';n.inputs[0].default_value=1;assign(a,n.inputs[1]);assign(b,n.inputs[2]);return n.outputs[0]
    uvname=document.get('blender',{}).get('uv_names',[''])[0]
    # Native UVs remain live through edits; uv_attributes never selects cached copies.
    uv=node('ShaderNodeUVMap','UV0');uv.uv_map=uvname
    sep=node('ShaderNodeSeparateXYZ','UV0 components');assign(uv.outputs[0],sep.inputs[0]);u,v=sep.outputs[:2]
    tx=op('ADD',op('MULTIPLY',u,scale[0]),offset[0]);ty=op('ADD',op('MULTIPLY',v,scale[1]),offset[1])
    geom=node('ShaderNodeNewGeometry','World surface/view')
    # Native tangent normal mapping supplies the bitangent handedness that
    # cross(N,T) loses on mirrored UVs and negative object scales.
    def tangent_axis(color,label):
        axis=node('ShaderNodeNormalMap',label);axis.space='TANGENT';axis.uv_map=uvname
        axis.inputs['Strength'].default_value=1;axis.inputs['Color'].default_value=(*color,1)
        return axis.outputs['Normal']
    t=tangent_axis((1,.5,.5),'UV tangent');binormal=tangent_axis((.5,1,.5),'UV bitangent')
    # Verified in Blender 5.2: Incoming points toward the observer.
    normal=geom.outputs['Normal']
    vx=vop('DOT_PRODUCT',geom.outputs['Incoming'],t);vy=vop('DOT_PRODUCT',geom.outputs['Incoming'],binormal)
    step=op('FRACT',op('ADD',op('MULTIPLY',ty,count),val('_StepsOffset',0)))
    view=op('ADD',op('MULTIPLY',vy,.5),.5)
    # Blender safe DIVIDE defines the measure-zero grazing singularities as 0.
    par=op('ADD',op('MINIMUM',op('DIVIDE',op('MULTIPLY',step,.5),view),.5),op('MAXIMUM',op('DIVIDE',op('MULTIPLY',op('SUBTRACT',step,view),.5),op('SUBTRACT',1,view)),0))
    triangle=op('SUBTRACT',1,op('ABSOLUTE',op('SUBTRACT',op('MULTIPLY',op('FRACT',op('MULTIPLY',par,2)),2),1)))
    ny=op('MULTIPLY',op('MULTIPLY',op('MINIMUM',op('MULTIPLY',triangle,val('_StepCornerSharpness',10)),1),op('SIGN',op('SUBTRACT',par,.5))),-.7071)
    dx=op('MULTIPLY',op('MULTIPLY',op('SUBTRACT',1,op('ABSOLUTE',op('SUBTRACT',op('MULTIPLY',par,2),1))),vx),-val('_UScaleCorrection',1)/count)
    dy=op('DIVIDE',op('SUBTRACT',par,step),count)
    borderpos=op('SUBTRACT',op('MULTIPLY',op('ADD',u,op('DIVIDE',dx,scale[0])),2),1)
    edge=op('SUBTRACT',op('ABSOLUTE',borderpos),1)
    clamp=node('ShaderNodeClamp','Border mask');assign(op('ADD',op('MULTIPLY',edge,50),.5),clamp.inputs[0]);mask=clamp.outputs[0]
    # Mix tangent vectors with vector arithmetic, avoiding RGB clamping negatives.
    nx=op('MULTIPLY',op('MULTIPLY',op('SIGN',borderpos),-1),mask)
    ny=op('MULTIPLY',ny,op('SUBTRACT',1,mask));nz=op('MULTIPLY',.7071,op('SUBTRACT',1,mask))
    stepnormal=node('ShaderNodeNormalMap','Tangent step normal');stepnormal.space='TANGENT';stepnormal.uv_map=uvname
    stepnormal.inputs['Strength'].default_value=1
    assign(vec(op('ADD',op('MULTIPLY',nx,.5),.5),op('ADD',op('MULTIPLY',ny,.5),.5),op('ADD',op('MULTIPLY',nz,.5),.5)),stepnormal.inputs['Color'])
    texture=node('ShaderNodeTexImage','_MainTex parallax');texture.image=image;texture.extension='REPEAT';assign(vec(op('ADD',tx,dx),op('ADD',ty,dy),0),texture.inputs['Vector'])
    border=tuple(val('_BorderColor',(.5,.5,.5,0)));base=lerp(texture.outputs['Color'],border,mask)
    ao=op('MAXIMUM',op('SUBTRACT',op('MULTIPLY',op('SUBTRACT',1,op('ABSOLUTE',edge)),11),10),0)
    ao=op('SUBTRACT',1,op('MULTIPLY',op('POWER',ao,4),val('_BorderOccludionStreigth',.5)))
    bs=node('ShaderNodeBsdfPrincipled','Unity Standard');assign(mulcolor(base,ao),bs.inputs['Base Color']);assign(stepnormal.outputs['Normal'],bs.inputs['Normal']);bs.inputs['Metallic'].default_value=0;bs.inputs['Roughness'].default_value=1
    out=node('ShaderNodeOutputMaterial','Output');assign(bs.outputs[0],out.inputs['Surface']);material.use_backface_culling=True
    for k,v in (metadata or {}).items():material[k]=v
    material['umd_shader_adapter']='fake-stairs-view-parallax/2'
    material['umd_shader_adapter_limitations']=json.dumps(['Unity Standard BRDF is approximated by Principled.','Raster depth Offset/AlphaToMask unsupported.','Native tangent normal mapping preserves mirrored-UV handedness.','Exactly grazing divide-by-zero uses Blender safe divide.'])
    material['umd_unmapped']=''
    return True
