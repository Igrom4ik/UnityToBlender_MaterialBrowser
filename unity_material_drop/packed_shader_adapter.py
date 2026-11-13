"""Exact texture/color/channel formulas for the project's simple Unity shaders.
Unity Standard BRDF, specular workflow and indirect AO remain approximations.
"""
import bpy
from pathlib import Path
SHADERS={"Custom/PBR_Packed_Alpha","Custom/PBR_PackedTextures","Custom/Diffuse Bump Spec","Custom/Diffuse Spec","Custom/Diffuse Surface Material","Custom/Diffuse Blend 2 Textures"}
VERSION = "project-packed-formulas/5"

def supports(document):return bool(document and document.get("shader",{}).get("name") in SHADERS)

def build(mat,document,prefix="UMD",metadata=None):
    from unity_pipeline_core.color_space import shader_document
    document = shader_document(document)
    shader=document["shader"]["name"];raw=document.get("material",{});effective=document.get("effective",{})
    def val(k,d=0):
        for category in ("floats","ints","colors"):
            if k in effective.get(category,{}):return effective[category][k]["value"]
            if k in raw.get(category,{}):return raw[category][k]
        return d
    # A serialized nonempty reference must not silently turn white.
    textures=raw.get("textures",{})
    relevant={"_MainTex","_BumpMap"} if shader.startswith("Custom/Diffuse") else {"_Albedo","_Normals"}
    if shader=="Custom/Diffuse Surface Material":relevant={"_TextureAlbedo","_TextureNormal"}
    if shader=="Custom/Diffuse Blend 2 Textures":relevant={"_MainTex","_BumpMap_1","_BumpMap_2","_DetailAlbedoMap","_BlendMask","_VertexRed","_VertexRedNormal","_VertexBlendMask","_AlbedoBlend_02","_BlendMask_02"}
    if val("_Usetintmap"):relevant.add("_Tintmask")
    if val("_UseEmissionTexture"):relevant.update(("_EmissionAO","_Emission"))
    if val("_EnableLightMap") or shader=="Custom/Diffuse Bump Spec":relevant.add("_Blend_UVW2_Texture")
    missing_normal = None
    for k in relevant.intersection(textures):
        p=textures[k].get("absolute_path")
        if not p or not Path(bpy.path.abspath(p)).is_file():
            if shader == "Custom/PBR_Packed_Alpha" and k == "_Albedo":
                continue  # Unity uses the shader's white default when the reference is missing.
            if shader == "Custom/Diffuse Bump Spec" and k == "_BumpMap":
                missing_normal = textures[k].get("guid", "") or "unresolved"
                continue
            raise ValueError(f"{shader}: unresolved texture {k}: {p}")
    if mat.node_tree is None:mat.use_nodes=True
    nt=mat.node_tree;nt.nodes.clear();i=0
    def node(kind,label):
        nonlocal i
        n=nt.nodes.new(kind);n.name=prefix+" "+label;n.label=label;n.location=(-1300+(i%8)*250,-(i//8)*260);i+=1;return n
    def assign(x,s):
        if hasattr(x,"node"):nt.links.new(x,s)
        else:s.default_value=x
    def op(operation,a,b=0):
        n=node("ShaderNodeMath",operation);n.operation=operation;assign(a,n.inputs[0]);assign(b,n.inputs[1]);return n.outputs[0]
    def mix(a,b,t,label="Mix",operation="MIX",clamp=False):
        if operation=="MIX":
            # HLSL lerp extrapolates; legacy MixRGB clamps linked factors.
            def vectorop(op,a,b):
                n=node("ShaderNodeVectorMath",label+" "+op);n.operation=op
                assign(a[:3] if isinstance(a,(tuple,list)) else a,n.inputs[0]);assign(b[:3] if isinstance(b,(tuple,list)) else b,n.inputs[1]);return n.outputs[0]
            return vectorop("ADD",vectorop("MULTIPLY",a,op("SUBTRACT",1,t)),vectorop("MULTIPLY",b,t))
        n=node("ShaderNodeMixRGB",label);n.blend_type=operation;n.use_clamp=clamp;assign(t,n.inputs[0]);assign(a,n.inputs[1]);assign(b,n.inputs[2]);return n.outputs[0]
    def color(k,d=(1,1,1,1)):
        return tuple(val(k,d)[:3])+(1,)
    def tex(k,space="sRGB",uv_index=0):
        entry=textures.get(k,{})
        if space == "sRGB":
            space = entry.get("meta", {}).get("colorspace", space)
        if k == "_BumpMap" and missing_normal is not None:return None
        if not entry.get("absolute_path") or not Path(bpy.path.abspath(entry["absolute_path"])).is_file():return None
        n=node("ShaderNodeTexImage",k);image=bpy.data.images.load(bpy.path.abspath(entry["absolute_path"]),check_existing=True)
        if image.colorspace_settings.name!=space:image=image.copy();image.colorspace_settings.name=space
        image.alpha_mode="CHANNEL_PACKED";n.image=image
        uvname=document.get("blender",{}).get("uv_names",["","UVMap_001"])[uv_index]
        if document.get("blender",{}).get("uv_attributes"):
            uv=node("ShaderNodeAttribute",f"{k} UV{uv_index}");uv.attribute_name=uvname;uvout=uv.outputs["Vector"]
        else:
            uv=node("ShaderNodeUVMap",f"{k} UV{uv_index}");uv.uv_map=uvname;uvout=uv.outputs[0]
        scale=entry.get("scale",[1,1]);offset=entry.get("offset",[0,0]);mapping=node("ShaderNodeMapping",k+" ST")
        mapping.inputs["Scale"].default_value=(*scale,1);mapping.inputs["Location"].default_value=(*offset,0)
        assign(uvout,mapping.inputs["Vector"]);assign(mapping.outputs[0],n.inputs["Vector"]);return n
    def output(n,name,default=1):return n.outputs[name] if n else default
    def rgb(n,default=(1,1,1,1)):return output(n,"Color",default)
    def alpha(n):return output(n,"Alpha")
    def separate(n):
        sep=node("ShaderNodeSeparateColor","Packed channels");assign(rgb(n),sep.inputs[0]);return sep
    def normal(n,packed=False):
        if not n:return
        source=n.outputs["Color"]
        if packed:
            sep=separate(n);x=op("SUBTRACT",op("MULTIPLY",sep.outputs[0],2),1);y=op("SUBTRACT",op("MULTIPLY",sep.outputs[1],2),1)
            z=op("SUBTRACT",1,op("ADD",op("MULTIPLY",x,x),op("MULTIPLY",y,y)))
            z=op("DIVIDE",z,op("ADD",.4775,op("MULTIPLY",.5225,z)))
            cmb=node("ShaderNodeCombineXYZ","Reconstructed NMG normal");assign(sep.outputs[0],cmb.inputs[0]);assign(sep.outputs[1],cmb.inputs[1]);assign(op("ADD",op("MULTIPLY",z,.5),.5),cmb.inputs[2]);source=cmb.outputs[0]
        nm=node("ShaderNodeNormalMap","Unity tangent normal");nm.uv_map="" if document.get("blender",{}).get("uv_attributes") else document.get("blender",{}).get("uv_names",["","UVMap_001"])[0];assign(source,nm.inputs["Color"]);assign(nm.outputs[0],bs.inputs["Normal"])
    bs=node("ShaderNodeBsdfPrincipled","Principled");out=node("ShaderNodeOutputMaterial","Output");assign(bs.outputs[0],out.inputs["Surface"])
    if shader=="Custom/Diffuse Blend 2 Textures":
        def vectorop(operation,a,b):
            n=node("ShaderNodeVectorMath",operation);n.operation=operation;assign(a,n.inputs[0]);assign(b,n.inputs[1]);return n.outputs[0]
        def vmix(a,b,t,label):
            return vectorop("ADD",vectorop("MULTIPLY",a,vectorop("SUBTRACT",(1,1,1),t)),vectorop("MULTIPLY",b,t))
        def vectorrgb(n):return rgb(n,(1,1,1))
        main=tex("_MainTex");detail=tex("_DetailAlbedoMap");mask=tex("_BlendMask","Non-Color");vertex=tex("_VertexRed")
        vc=node("ShaderNodeVertexColor","Unity vertex color");vc.layer_name=document.get("blender",{}).get("color_attribute","")
        sep=node("ShaderNodeSeparateColor","Vertex red");assign(vc.outputs[0],sep.inputs[0]);red=op("MINIMUM",op("MAXIMUM",op("MULTIPLY",sep.outputs[0],10),0),1)
        vm=tex("_VertexBlendMask","Non-Color");weight=vectorop("MULTIPLY",vectorop("SUBTRACT",(1,1,1),vectorrgb(mask)),vectorrgb(vm))
        scale=node("ShaderNodeVectorMath","Vertex red weight");scale.operation="SCALE";assign(weight,scale.inputs[0]);assign(red,scale.inputs[3]);weight=scale.outputs[0]
        a=vectorop("MULTIPLY",vectorrgb(main),color("_MainTex_color")[:3]);b=vectorop("MULTIPLY",vectorrgb(detail),color("_Detail_color")[:3])
        result=vmix(a,b,vectorrgb(mask),"Layer blend");result=vmix(result,vectorrgb(vertex),weight,"Vertex layer")
        overlay=tex("_AlbedoBlend_02");overlaymask=tex("_BlendMask_02","Non-Color");overlayred=separate(overlaymask).outputs[0]
        result=mix(result,mix(rgb(overlay),color("_Color_02"),1,"Overlay tint","MULTIPLY"),op("MULTIPLY",overlayred,val("_BlendMask_02ONOFF")),"Overlay")
        lm=tex("_Blend_UVW2_Texture",uv_index=1)
        if lm:result=mix(result,rgb(lm),1,"UV1 multiplier","MULTIPLY")
        assign(result,bs.inputs["Base Color"])
        # UnpackNormal RGB inputs first, then blend per channel exactly as HLSL.
        def signed(n):return vectorop("SUBTRACT",vectorop("MULTIPLY",vectorrgb(n),(2,2,2)),(1,1,1))
        n1=tex("_BumpMap_1","Non-Color");n2=tex("_BumpMap_2","Non-Color");nr=tex("_VertexRedNormal","Non-Color")
        nsigned=vmix(signed(n1),signed(n2),vectorrgb(mask),"Normal blend")
        nsigned=vmix(nsigned,vectorrgb(nr),weight,"Raw vertex red normal")
        encoded=vectorop("ADD",vectorop("MULTIPLY",nsigned,(.5,.5,.5)),(.5,.5,.5))
        nm=node("ShaderNodeNormalMap","Blended normal");nm.uv_map="" if document.get("blender",{}).get("uv_attributes") else document.get("blender",{}).get("uv_names",[""])[0];assign(encoded,nm.inputs["Color"]);assign(nm.outputs[0],bs.inputs["Normal"])
        wsep=node("ShaderNodeSeparateXYZ","Vertex weights");assign(weight,wsep.inputs[0]);remaining=op("SUBTRACT",1,wsep.outputs[0])
        assign(op("SUBTRACT",1,op("MULTIPLY",remaining,val("_Smuthnes"))),bs.inputs["Roughness"])
        assign(op("DIVIDE",op("MULTIPLY",remaining,val("_Specular")),.08),bs.inputs["Specular IOR Level"])
    elif shader=="Custom/Diffuse Surface Material":
        base=tex("_TextureAlbedo");normal(tex("_TextureNormal","Non-Color"))
        sep=node("ShaderNodeSeparateColor","Texture HSV");sep.mode="HSV";assign(rgb(base),sep.inputs[0])
        cmb=node("ShaderNodeCombineColor","Modified HSV");cmb.mode="HSV"
        assign(op("ADD",sep.outputs[0],val("_TextureHUE")),cmb.inputs[0]);assign(op("MULTIPLY",sep.outputs[1],val("_TextureSaturation",1)),cmb.inputs[1]);assign(op("MULTIPLY",sep.outputs[2],val("_TextureValue",1)),cmb.inputs[2])
        result=mix(cmb.outputs[0],color("_Color",(0,0,0,0)),1,"Add color","ADD")
        lm=tex("_Blend_UVW2_Texture",uv_index=1)
        if lm:result=mix(result,rgb(lm),1,"UV1 multiplier","MULTIPLY")
        assign(result,bs.inputs["Base Color"]);assign(op("SUBTRACT",1,op("MULTIPLY",alpha(base),val("_TextureSmooth"))),bs.inputs["Roughness"])
        spec=val("_TextureSpec") if val("_SpecularAll") else op("MULTIPLY",alpha(base),val("_TextureSpec"))
        assign(op("DIVIDE",spec,.08),bs.inputs["Specular IOR Level"])
    elif shader in {"Custom/Diffuse Bump Spec","Custom/Diffuse Spec"}:
        base=tex("_MainTex");bump=tex("_BumpMap","Non-Color");normal(bump)
        basecolor=rgb(base)
        if val("_SpecCOlor",1 if shader=="Custom/Diffuse Bump Spec" else 0):basecolor=mix(basecolor,color("_Color",(0,0,0,0)),op("SUBTRACT",1,alpha(base)),"Alpha color replacement")
        basecolor=mix(basecolor,color("_MulColor",(.8676471,)*4),1,"MulColor","MULTIPLY")
        lightmap=tex("_Blend_UVW2_Texture",uv_index=1)
        if lightmap:basecolor=mix(basecolor,rgb(lightmap),1,"UV1 color multiplier","MULTIPLY")
        assign(basecolor,bs.inputs["Base Color"])
        assign(op("SUBTRACT",1,op("MULTIPLY",alpha(base),val("_Gloss")) if shader=="Custom/Diffuse Bump Spec" else val("_Gloss")),bs.inputs["Roughness"])
        spec=val("_Specular") if val("_SpecularAll") else op("MULTIPLY",alpha(base),val("_Specular"))
        assign(op("DIVIDE",spec,.08),bs.inputs["Specular IOR Level"])
    else:
        packed=shader=="Custom/PBR_Packed_Alpha"
        keys=set(raw.get("keywords",{}).get("valid",[]))
        # Compiled keywords control URP variants; absent lists use toggles.
        has_keywords="valid" in raw.get("keywords",{})
        def enabled(keyword, prop):
            return keyword in keys if has_keywords else bool(val(prop))
        urp=shader=="Custom/PBR_PackedTextures"
        rg=packed or (urp and enabled("_NORMALS_PACKED_RG", "_NormalsPackedRG"))
        base=tex("_Albedo");nmg=tex("_Normals","Non-Color");normal(nmg,rg)
        basecolor=mix(rgb(base),color("_Color",(.5,.5,.5,0)),1,"Color tint","MULTIPLY")
        if (packed and val("_Usetintmap")) or (urp and enabled("_USETINTMAP_ON", "_Usetintmap")):
            mask=tex("_Tintmask","Non-Color");sep=separate(mask)
            # Unity's UnpackNormal.r reads red in RGB imported normal maps.
            # DXT5nm/BC5 normal import stores X in alpha or red; honor meta.
            settings=textures.get("_Tintmask",{}).get("meta",{})
            mask_channel=sep.outputs[0]
            basecolor=mix(rgb(base),basecolor,op("SUBTRACT",op("MULTIPLY",mask_channel,2),1),"Normal-red tint mask")
        basecolor=mix(basecolor,(2,2,2,1),1,"Albedo x2 clamp","MULTIPLY",True)
        emission=tex("_EmissionAO" if packed else "_Emission") if (enabled("_USEEMISSIONTEXTURE_ON", "_UseEmissionTexture") if urp else val("_UseEmissionTexture")) else None
        emiss=mix(rgb(emission),color("_EmissionColor",(0,0,0,0)),1,"Emission tint","MULTIPLY")
        emiss=mix(emiss,(2,2,2,1),1,"Emission x2","MULTIPLY");assign(emiss,bs.inputs["Emission Color"]);bs.inputs["Emission Strength"].default_value=1
        if packed:
            metal=separate(nmg).outputs[2] if nmg else 1;gloss=alpha(nmg);ao=alpha(emission)
        else:
            channels=[alpha(base),alpha(nmg),alpha(emission)]
            # Defaults match preprocessor fallback when no keyword is authored.
            keys=set(raw.get("keywords",{}).get("valid",[]))
            def channel(name,fallback):
                for idx,suffix in enumerate(("ALBEDOALPHA","NORMALSALPHA","EMISSIONALPHA")):
                    if "_"+name.upper()+"SOURCE_"+suffix in keys:return channels[idx]
                return channels[fallback]
            metal=separate(nmg).outputs[2] if rg and nmg else (1 if rg else channel("Metallic",1));gloss=channel("Smoothness",0);ao=channel("AmbientOcclusion",2)
        assign(op("MINIMUM",op("MULTIPLY",metal,val("_Metallic",1)),1),bs.inputs["Metallic"])
        assign(op("SUBTRACT",1,op("MINIMUM",op("MULTIPLY",gloss,val("_Smoothness",1)),1)),bs.inputs["Roughness"])
        occ=op("ADD",1-val("_AmbientOcclusion",1),op("MULTIPLY",ao,val("_AmbientOcclusion",1)))
        clamp=node("ShaderNodeClamp","AO saturate");assign(occ,clamp.inputs[0]);basecolor=mix(basecolor,clamp.outputs[0],1,"Indirect AO approximation","MULTIPLY")
        if val("_EnableLightMap"):
            lm=tex("_Blend_UVW2_Texture",uv_index=1)
            if lm:basecolor=mix(basecolor,mix((1,1,1,1),rgb(lm),val("_LightMapIntensity",1),"Lightmap intensity"),1,"Lightmap approximation","MULTIPLY")
        assign(basecolor,bs.inputs["Base Color"])
        if (packed and val("_UseAlphaCutout")) or (urp and enabled("_ALPHATEST_ON", "_AlphaClip")):
            # HLSL clip(alpha-cutoff) keeps equality.
            assign(op("SUBTRACT",1,op("LESS_THAN",alpha(base),val("_Cutoff",.5))),bs.inputs["Alpha"])
            if hasattr(mat,"surface_render_method"):mat.surface_render_method="DITHERED"
            elif hasattr(mat,"blend_method"):
                mat.blend_method="CLIP";mat.alpha_threshold=val("_Cutoff",.5)
    mat.use_backface_culling=True
    for k,v in (metadata or {}).items():mat[k]=v
    mat["umd_shader_adapter"]=VERSION;mat["umd_unmapped"]=""
    if missing_normal is not None:
        mat["umd_missing_normal_guid"] = missing_normal
    elif "umd_missing_normal_guid" in mat:
        del mat["umd_missing_normal_guid"]
    mat["umd_shader_adapter_limitations"]="Unity BRDF/specular/indirect AO approximated; camera Z offset and interlaced clipping excluded"
    return mat
