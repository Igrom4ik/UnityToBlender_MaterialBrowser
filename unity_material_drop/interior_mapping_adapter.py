"""Custom/Environment/InteriorMapping: Unity view-ray room atlas."""
from pathlib import Path
import bpy, json
VERSION='interior-mapping-ray-box/3'
SHADER='Custom/Environment/InteriorMapping'
def supports(document):
    return bool(document and document.get('shader',{}).get('name')==SHADER)
def build(material,document,prefix='UMD',metadata=None):
    raw=document.get('material',{}); eff=document.get('effective',{}); textures=raw.get('textures',{})
    def val(k,d):
        for cat in ('floats','ints','colors'):
            if k in eff.get(cat,{}): return eff[cat][k]['value']
            if k in raw.get(cat,{}): return raw[cat][k]
        return d
    keywords=set(raw.get('keywords',{}).get('valid',[]))
    uvindex=next((i for i in range(3) if '_INTERRIORUVSET_UV'+str(i) in keywords),int(round(val('_InterriorUVSet',0))))
    if uvindex not in (0,1,2): raise ValueError('Invalid InteriorMapping UV channel: '+str(uvindex))
    flags={'_MainTex':'_USEBASEMAP_ON' in keywords,'_NormalsSmoothnessTex':'_USENORMALSMAP_ON' in keywords,'_InteriorAtlas':True}
    images={}
    for key in flags:
        entry=textures.get(key,{})
        if not flags[key] or not entry.get('guid'): continue
        path=entry.get('absolute_path')
        if not path or not Path(bpy.path.abspath(path)).is_file():raise ValueError('InteriorMapping texture unresolved '+key+': '+str(path))
        im=bpy.data.images.load(bpy.path.abspath(path),check_existing=True);space='Non-Color' if key=='_NormalsSmoothnessTex' else 'sRGB'
        if im.colorspace_settings.name!=space:im=im.copy();im.colorspace_settings.name=space
        im.alpha_mode='CHANNEL_PACKED';images[key]=im
    docuv=document.get('blender',{}).get('uv_names',[]) if document.get('blender') else []
    obj=bpy.context.active_object
    if not docuv and obj and obj.type=='MESH':docuv=[u.name for u in obj.data.uv_layers]
    uvnames=list(docuv);fallback=('ch1_UVMap','ch2_UVMap','ch3_UVMap') if uvnames and uvnames[0].startswith('ch1_') else ('UVMap','UVMap.001','UVMap.002')
    uvnames=(uvnames+list(fallback[len(uvnames):]))[:3]
    if material.node_tree is None:material.use_nodes=True
    nt=material.node_tree;nt.nodes.clear();counter=0
    def node(kind,label):
        nonlocal counter
        v=nt.nodes.new(kind);v.name=prefix+' '+label;v.label=label;v.location=(-1800+(counter%10)*240,-(counter//10)*210);counter+=1;return v
    def put(v,s):
        if hasattr(v,'node'):nt.links.new(v,s)
        else:s.default_value=v
    def op(kind,a,b=0):
        n=node('ShaderNodeMath',kind);n.operation=kind;put(a,n.inputs[0]);put(b,n.inputs[1]);return n.outputs[0]
    def sep(x):
        n=node('ShaderNodeSeparateXYZ','XYZ');put(x,n.inputs[0]);return tuple(n.outputs)
    def vec(x,y,z=0):
        n=node('ShaderNodeCombineXYZ','Vector');put(x,n.inputs[0]);put(y,n.inputs[1]);put(z,n.inputs[2]);return n.outputs[0]
    def dot(a,b):
        n=node('ShaderNodeVectorMath','Dot');n.operation='DOT_PRODUCT';put(a,n.inputs[0]);put(b,n.inputs[1]);return n.outputs['Value']
    def color_mul(a,b,label):
        n=node('ShaderNodeMixRGB',label);n.blend_type='MULTIPLY';n.inputs[0].default_value=1;put(a,n.inputs[1]);put(b,n.inputs[2]);return n.outputs[0]
    def uvmap(i):
        n=node('ShaderNodeUVMap','UV'+str(i));n.uv_map=uvnames[i];return n.outputs['UV']
    uv0=uvmap(0);uvin=uv0 if uvindex==0 else uvmap(uvindex)
    def tex(key,vector,role):
        im=images.get(key)
        if im is None:return None
        x=node('ShaderNodeTexImage',role+' '+key);x.image=im;x.extension='REPEAT';put(vector,x.inputs['Vector']);return x
    def stcoord(uv,key):
        x,y,_=sep(uv);entry=textures.get(key,{});scale=entry.get('scale',[1,1]);off=entry.get('offset',[0,0]);return vec(op('ADD',op('MULTIPLY',x,scale[0]),off[0]),op('ADD',op('MULTIPLY',y,scale[1]),off[1]))
    main=tex('_MainTex',stcoord(uv0,'_MainTex'),'BASE_COLOR');normal=tex('_NormalsSmoothnessTex',stcoord(uv0,'_NormalsSmoothnessTex'),'NORMAL')
    atlasuv=stcoord(uvin,'_InteriorAtlas');cx,cy,_=sep(atlasuv);w=max(float(val('_AtlasWidth',1)),1);h=max(float(val('_AtlasHeight',1)),1);depth=float(val('_Depth',.15))
    im=images.get('_InteriorAtlas');width,height=(float(im.size[0]),float(im.size[1])) if im else (1.,1.);ratio=(h/w)*(width/max(height,1.));ratio=max(ratio,1e-8)
    sx=op('FRACT',op('MULTIPLY',cx,w));sy=op('FRACT',op('MULTIPLY',cy,h))
    geo=node('ShaderNodeNewGeometry','View ray')
    def axis(name,color):
        n=node('ShaderNodeNormalMap',name);n.space='TANGENT';n.uv_map=uvnames[0];n.inputs['Color'].default_value=color;return n.outputs['Normal']
    t=axis('Tangent',(1,.5,.5,1));b=axis('Bitangent',(.5,1,.5,1));normalworld=geo.outputs['Normal']
    rays=[op('MULTIPLY',dot(geo.outputs['Incoming'],x),-1) for x in (t,b,normalworld)]
    def safe(x):return op('MULTIPLY',op('SUBTRACT',1,op('MULTIPLY',2,op('LESS_THAN',x,0))),op('MAXIMUM',op('ABSOLUTE',x),1e-7))
    inv=[op('DIVIDE',v,safe(r)) for v,r in zip((ratio,1,1),rays)]
    fx=op('MAXIMUM',op('MULTIPLY',op('SUBTRACT',1,sx),inv[0]),op('MULTIPLY',op('MULTIPLY',-1,sx),inv[0]))
    fy=op('MAXIMUM',op('MULTIPLY',op('SUBTRACT',1,sy),inv[1]),op('MULTIPLY',op('MULTIPLY',-1,sy),inv[1]))
    fz=op('MAXIMUM',op('MULTIPLY',-depth,inv[2]),0)
    dist=op('MINIMUM',op('MINIMUM',fx,fy),fz)
    hx=op('ADD',sx,op('DIVIDE',op('MULTIPLY',dist,rays[0]),ratio));hy=op('ADD',sy,op('MULTIPLY',dist,rays[1]));hz=op('MULTIPLY',dist,rays[2])
    den=op('SUBTRACT',1,hz);projx=op('ADD',.5,op('DIVIDE',op('SUBTRACT',hx,.5),safe(den)));projy=op('ADD',.5,op('DIVIDE',op('SUBTRACT',hy,.5),safe(den)))
    ax=op('ADD',cx,op('DIVIDE',op('SUBTRACT',projx,sx),w));ay=op('ADD',cy,op('DIVIDE',op('SUBTRACT',projy,sy),h))
    atlas=tex('_InteriorAtlas',vec(ax,ay),'EMISSION');bs=node('ShaderNodeBsdfPrincipled','Principled');bs.inputs['Metallic'].default_value=0;bs.inputs['Alpha'].default_value=1
    tint=tuple(val('_TintColor',[1,1,1,1]));itint=tuple(val('_InteriorTintColor',[1,1,1,1]));base=main.outputs['Color'] if main else (0,0,0,1);alpha=op('MULTIPLY',main.outputs['Alpha'],tint[3]) if main else 0
    tinted=color_mul(base,tint,'Base tint');put(color_mul(tinted,vec(alpha,alpha,alpha),'Base alpha'),bs.inputs['Base Color'])
    interior=atlas.outputs['Color'] if atlas else (1,1,1,1);em=color_mul(interior,itint,'Interior tint');mask=op('SUBTRACT',1,alpha);put(color_mul(em,vec(mask,mask,mask),'Interior visibility'),bs.inputs['Emission Color']);bs.inputs['Emission Strength'].default_value=1
    smooth=op('MULTIPLY',normal.outputs['Alpha'] if normal else 1,float(val('_Smoothness',1)));put(op('SUBTRACT',1,smooth),bs.inputs['Roughness'])
    if normal:
        n=node('ShaderNodeNormalMap','Surface Normal');n.uv_map=uvnames[0];put(normal.outputs['Color'],n.inputs['Color']);put(n.outputs['Normal'],bs.inputs['Normal'])
    out=node('ShaderNodeOutputMaterial','Output');put(bs.outputs[0],out.inputs['Surface']);material.use_backface_culling=True
    for k,v in (metadata or {}).items():material[k]=v
    material['umd_shader_adapter']=VERSION;material['umd_interior_uv_index']=uvindex;material['umd_interior_uv_name']=uvnames[uvindex];material['umd_primary_uv_name']=uvnames[0]
    material['umd_shader_adapter_limitations']=json.dumps(['Unity Standard BRDF approximated with Blender Principled; lighting and tangent generation may differ.','UV layers are chosen by name; objects sharing this material need matching UV names.','Missing image slots use Unity white or zero shader defaults.'])
    return material
