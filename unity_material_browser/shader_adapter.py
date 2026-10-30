"""Host-side adapters for shader formulas that cannot fit a single PBR map plan.

Consumes the existing material JSON document, so the same adapter works with
compiled and Python pipeline cores, and library rebuilds need no Unity access.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import bpy

SHADER = "Custom/Environment/PBR_blend_materials"
VERSION = "environment-height-blend/1"

def supports(document):
    return bool(document and document.get("shader", {}).get("name") == SHADER)

def build(material, document, prefix="UMD", metadata=None):
    effective=document.get("effective", {})
    raw=document.get("material", {})
    textures=raw.get("textures", {})
    def value(key, default=0):
        for kind in ("floats", "ints", "colors"):
            entry=effective.get(kind, {}).get(key)
            if entry is not None: return entry.get("value",default)
            if key in raw.get(kind, {}): return raw[kind][key]
        return default
    layers=[1,2]+([3] if value("_UseLayer3") or value("_UseLayer4") else [])+([4] if value("_UseLayer4") else [])
    # Validate before replacing an existing tree. Missing maps fail explicitly.
    required=[f"_{kind}{j}" for j in layers for kind in ("BaseColor","Normal")]
    required += [f"_Mask{j}" for j in layers if value(f"_Usetintmap{j}")]
    if any(value("_"+c+"Maskmode") for c in ("Red","Green","Blue")):
        required += ["_SplatMap"]
    for key in required:
        path=textures.get(key,{}).get("absolute_path")
        if not path or not Path(bpy.path.abspath(path)).is_file():
            raise ValueError(f"{SHADER}: unresolved required texture {key}: {path}")
    if material.node_tree is None: material.use_nodes=True
    nt=material.node_tree; nt.nodes.clear(); ns=nt.nodes; links=nt.links
    serial=0
    def node(kind,label):
        nonlocal serial
        n=ns.new(kind);n.name=prefix+" "+label;n.label=label
        n.location=(-1600+(serial%10)*230, -(serial//10)*230);serial+=1
        return n
    def wire(output, socket): links.new(output,socket)
    def mathop(op,a,b=0):
        n=node("ShaderNodeMath",op);n.operation=op
        for i,x in enumerate((a,b)):
            if isinstance(x,(int,float)):n.inputs[i].default_value=x
            else:wire(x,n.inputs[i])
        return n.outputs[0]
    def blend(a,b,t,label):
        n=node("ShaderNodeMixRGB",label)
        wire(t,n.inputs[0]) if not isinstance(t,(int,float)) else setattr(n.inputs[0],"default_value",t)
        for i,x in enumerate((a,b),1):
            if hasattr(x,"node"):wire(x,n.inputs[i])
            else:n.inputs[i].default_value=x
        return n.outputs[0]
    uvnames=document.get("blender",{}).get("uv_names",["UVMap","UVMap_001"])
    def uv(index):
        if document.get("blender",{}).get("uv_attributes"):
            n=node("ShaderNodeAttribute",f"Unity UV{index}");n.attribute_name=uvnames[index];return n.outputs["Vector"]
        n=node("ShaderNodeUVMap",f"Unity UV{index}"); n.uv_map=uvnames[index];return n.outputs[0]
    uv0=uv(0)
    def tex(key,coord,space):
        n=node("ShaderNodeTexImage",key)
        path=bpy.path.abspath(textures[key]["absolute_path"])
        image=bpy.data.images.load(path,check_existing=True)
        if image.colorspace_settings.name!=space:
            image=image.copy();image.colorspace_settings.name=space
        image.alpha_mode="CHANNEL_PACKED"
        n.image=image;wire(coord,n.inputs["Vector"]);return n
    vc=node("ShaderNodeVertexColor","Unity vertex mask")
    vc.layer_name=document.get("blender",{}).get("color_attribute","")
    vcs=node("ShaderNodeSeparateColor","Vertex mask RGB");wire(vc.outputs["Color"],vcs.inputs[0])
    splat=None
    if "_SplatMap" in required:
        # ASE uses UV0/UV1 without the texture scale/offset here.
        splattex=tex("_SplatMap",uv(int(value("_UVset"))),"Non-Color")
        splat=node("ShaderNodeSeparateColor","Splat mask RGB");wire(splattex.outputs["Color"],splat.inputs[0])
    channels={2:("Red",0),3:("Green",1),4:("Blue",2)}
    colors=[];normals=[];heights=[];metals=[];glosses=[];factors=[]
    for j in layers:
        tile=node("ShaderNodeVectorMath",f"Layer {j} tiling");tile.operation="MULTIPLY"
        tile.inputs[1].default_value=(value(f"_Tile{j}x",1),value(f"_Tile{j}y",1),1);wire(uv0,tile.inputs[0])
        rot=node("ShaderNodeVectorRotate",f"Layer {j} Unity rotation");rot.rotation_type="AXIS_ANGLE"
        rot.inputs["Center"].default_value=(.5,.5,0);rot.inputs["Axis"].default_value=(0,0,1)
        rot.inputs["Angle"].default_value=-math.radians(value("_UVRotateAngle"+(str(j-1) if j>1 else "")))
        wire(tile.outputs[0],rot.inputs["Vector"]);coord=rot.outputs[0]
        bc=tex(f"_BaseColor{j}",coord,"sRGB")
        tint=value(f"_Tint{j}",[1,1,1,0]);mul=node("ShaderNodeMixRGB",f"Layer {j} tint");mul.blend_type="MULTIPLY";mul.inputs[0].default_value=1
        mul.inputs[2].default_value=tuple(tint[:3])+ (1,);wire(bc.outputs["Color"],mul.inputs[1]);color=mul.outputs[0]
        if value(f"_Usetintmap{j}"):
            masktex=tex(f"_Mask{j}",coord,"Non-Color");sep=node("ShaderNodeSeparateColor","Tint mask red");wire(masktex.outputs[0],sep.inputs[0])
            color=blend(bc.outputs["Color"],color,sep.outputs[0],"Masked tint")
        gain=node("ShaderNodeVectorMath",f"Layer {j} albedo x2");gain.operation="SCALE";gain.inputs[3].default_value=2;wire(color,gain.inputs[0]);colors.append(gain.outputs[0])
        alpha=bc.outputs["Alpha"]
        inv="_InvertHeight"+{1:"Fill",2:"Red",3:"Green",4:"Blue"}[j]+"Layer"
        # lerp(a, 1-a, inversion), including non-boolean authored values.
        alpha=mathop("ADD",mathop("MULTIPLY",alpha,1-value(inv)),mathop("MULTIPLY",mathop("SUBTRACT",1,alpha),value(inv)))
        h=mathop("MULTIPLY",alpha,value(f"_Scale{j}",{1:.3,2:.6}.get(j,1)))
        if j>1:
            ch,idx=channels[j];mode=value("_"+ch+"Maskmode")
            mask=vcs.outputs[idx] if not mode else mathop("ADD",mathop("MULTIPLY",vcs.outputs[idx],1-mode),mathop("MULTIPLY",splat.outputs[idx],mode))
            h=mathop("MULTIPLY",h,mask)
        h=mathop("ADD",h,value(f"_Offset{j}",{1:.3,2:.6}.get(j,1)))
        if j>1:
            sharp=value(f"_Sharpness{j}",.45)
            f=mathop("DIVIDE",mathop("SUBTRACT",mathop("SUBTRACT",h,heights[-1]),sharp),1-2*sharp)
            clamp=node("ShaderNodeClamp",f"Layer {j} height factor");wire(f,clamp.inputs[0]);factors.append(clamp.outputs[0])
            h=mathop("MAXIMUM",heights[-1],h)
        heights.append(h)
        nmg=tex(f"_Normal{j}",coord,"Non-Color");sep=node("ShaderNodeSeparateColor",f"Layer {j} NMG");wire(nmg.outputs["Color"],sep.inputs[0])
        x=mathop("SUBTRACT",mathop("MULTIPLY",sep.outputs[0],2),1);y=mathop("SUBTRACT",mathop("MULTIPLY",sep.outputs[1],2),1)
        z=mathop("SUBTRACT",1,mathop("ADD",mathop("MULTIPLY",x,x),mathop("MULTIPLY",y,y)))
        z=mathop("DIVIDE",z,mathop("ADD",.4775,mathop("MULTIPLY",.5225,z)))
        cmb=node("ShaderNodeCombineXYZ",f"Layer {j} tangent normal");wire(x,cmb.inputs[0]);wire(y,cmb.inputs[1]);wire(z,cmb.inputs[2]);normals.append(cmb.outputs[0])
        metals.append(mathop("MULTIPLY",sep.outputs[2],value(f"_MetallicMult{j}")))
        glosses.append(mathop("MULTIPLY",nmg.outputs["Alpha"],value(f"_GlossMult{j}",1)))
    color=colors[0];normal=normals[0];metal=metals[0];gloss=glosses[0]
    for k,j in enumerate(layers[1:],1):
        if j==3 and not value("_UseLayer3"):continue
        factor=factors[k-1];color=blend(color,colors[k],factor,f"Blend color layer {j}");normal=blend(normal,normals[k],factor,f"Blend normal layer {j}")
        metal=mathop("ADD",mathop("MULTIPLY",metal,mathop("SUBTRACT",1,factor)),mathop("MULTIPLY",metals[k],factor))
        # Source shader's fourth layer intentionally lerps smoothness from zero.
        gloss=mathop("ADD",mathop("MULTIPLY",0 if j==4 else gloss,mathop("SUBTRACT",1,factor)),mathop("MULTIPLY",glosses[k],factor))
    normalize=node("ShaderNodeVectorMath","Normalize blended tangent normal");normalize.operation="NORMALIZE";wire(normal,normalize.inputs[0])
    enc=node("ShaderNodeVectorMath","Encode blended normal");enc.operation="MULTIPLY_ADD";enc.inputs[1].default_value=(.5,.5,.5);enc.inputs[2].default_value=(.5,.5,.5);wire(normalize.outputs[0],enc.inputs[0])
    nm=node("ShaderNodeNormalMap","Unity tangent normal");nm.uv_map="" if document.get("blender",{}).get("uv_attributes") else uvnames[0];wire(enc.outputs[0],nm.inputs["Color"])
    bs=node("ShaderNodeBsdfPrincipled","Principled");out=node("ShaderNodeOutputMaterial","Output");wire(bs.outputs[0],out.inputs["Surface"])
    wire(blend(color,vc.outputs["Color"],value("_DebugVertexColor"),"Debug vertex color"),bs.inputs["Base Color"])
    wire(nm.outputs[0],bs.inputs["Normal"]);wire(metal,bs.inputs["Metallic"]);wire(mathop("SUBTRACT",1,gloss),bs.inputs["Roughness"])
    material.use_backface_culling=True
    for key,val in (metadata or {}).items():material[key]=val
    material["umd_shader_adapter"]=VERSION
    material["umd_shader_adapter_layers"]=json.dumps(layers)
    material["umd_shader_adapter_limitations"]="Unity Standard lighting/BRDF approximated by Principled; Unity baked lightmap is not transferred"
    material["umd_unmapped"]=""
    return material
