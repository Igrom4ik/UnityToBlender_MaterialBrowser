"""Shader graph matching the surface equations in VertexColorMix4_v2."""
import bpy


def build(material, plan, prefix, metadata=None):
    if material.node_tree is None: material.use_nodes=True
    tree=material.node_tree
    tree.nodes.clear()
    values=plan.vertex_paint['values']; colors=plan.vertex_paint['colors']
    counter=[0]
    def node(kind,label):
        n=tree.nodes.new(kind);n.name=f'{prefix} {label}';n.label=label
        counter[0]+=1;n.location=((counter[0]%9)*210-1900,-(counter[0]//9)*220)
        return n
    def set_input(socket,value):
        if hasattr(value,'node'): tree.links.new(value,socket)
        else: socket.default_value=value
    def math(op,a,b=0,label=None,clamp=False):
        n=node('ShaderNodeMath',label or op);n.operation=op;n.use_clamp=clamp
        set_input(n.inputs[0],a);set_input(n.inputs[1],b);return n.outputs[0]
    def mix(a,b,factor,label):
        n=node('ShaderNodeMixRGB',label);n.blend_type='MIX'
        set_input(n.inputs[0],factor);set_input(n.inputs[1],a);set_input(n.inputs[2],b)
        return n.outputs[0]
    def value(name,default=0): return float(values.get(name,default))
    def parameter(name,default=0):
        n=node('ShaderNodeValue',name);n.outputs[0].default_value=value(name,default)
        return n.outputs[0]
    uv=node('ShaderNodeTexCoord','Coordinates')
    geometry=node('ShaderNodeNewGeometry','World Position')
    separate=node('ShaderNodeSeparateXYZ','Blender World XY (Unity XZ)');tree.links.new(geometry.outputs['Position'],separate.inputs[0])
    world=node('ShaderNodeCombineXYZ','World Planar Coordinates')
    tree.links.new(separate.outputs['X'],world.inputs['X']);tree.links.new(separate.outputs['Y'],world.inputs['Y'])
    paint=node('ShaderNodeVertexColor','Vertex Paint (active color attribute)');paint.layer_name=''
    channels=node('ShaderNodeSeparateColor','Vertex RGB');tree.links.new(paint.outputs['Color'],channels.inputs['Color'])
    textures={}; coords={}
    def coordinate(toggle,tile,texture):
        is_world=bool(value(toggle));key=(is_world,value(tile,.1)) if is_world else (False,texture.scale,texture.offset)
        if key not in coords:
            m=node('ShaderNodeMapping',f'UV {texture.property_name}');m.vector_type='POINT'
            m.inputs['Scale'].default_value=(value(tile,.1),)*2+(1,) if is_world else (*texture.scale,1)
            m.inputs['Location'].default_value=(0,0,0) if is_world else (*texture.offset,0)
            tree.links.new(world.outputs[0] if is_world else uv.outputs['UV'],m.inputs['Vector']);coords[key]=m.outputs['Vector']
        return coords[key]
    byname={t.property_name:t for t in plan.textures}
    base=byname.get('_Diffuse1')
    for t in plan.textures:
        name=t.property_name
        if name.endswith('1'): toggle,tile,ref='_WorldPoseDif','_UVTileDif',base or t
        elif name.endswith(('R','G','B')):
            ch=name[-1];toggle,tile,ref='_WorldPose'+ch,'_UVTile'+ch,byname.get('_Diffuse'+ch,t)
        elif name=='_NoiseTexture': toggle,tile,ref='_UVTileNoise_ONOFF','_UVTileNoise',base or t
        elif name=='_Mask_Relief_Diffuse': toggle,tile,ref='_Mask_Relief_UV_ONOFF','_Mask_Relief_UV',base or t
        else: toggle,tile,ref='_WorldPoseRelief','_Relief_UV',byname.get('_DiffuseB',t)
        n=node('ShaderNodeTexImage',f'{t.role} {name}')
        try:
            n.image=bpy.data.images.load(t.image_path,check_existing=True)
            n.image.colorspace_settings.name=t.colorspace
        except (RuntimeError,TypeError): pass
        tree.links.new(coordinate(toggle,tile,ref),n.inputs['Vector']);textures[name]=n
    def color(name,default=(1,1,1,1)): return textures[name].outputs['Color'] if name in textures else default
    def alpha(name): return textures[name].outputs['Alpha'] if name in textures else 1.0
    noise=node('ShaderNodeSeparateColor','Noise RGB');set_input(noise.inputs['Color'],color('_NoiseTexture'))
    luminance=math('ADD',math('ADD',math('MULTIPLY',noise.outputs['Red'],.22),math('MULTIPLY',noise.outputs['Green'],.707)),math('MULTIPLY',noise.outputs['Blue'],.071))
    noise_power=math('POWER',luminance,.47,'Noise Power 0.47')
    masks={}
    for ch,long in [('R','Red'),('G','Green'),('B','Blue')]:
        v=channels.outputs[long];border=value('_'+long+'BorderDistance')
        t=math('DIVIDE',math('SUBTRACT',v,border),max(1-border,1e-6),long+' Border Ramp',True)
        smooth=math('MULTIPLY',math('MULTIPLY',t,t),math('SUBTRACT',3,math('MULTIPLY',2,t)))
        erosion=math('MULTIPLY',math('MULTIPLY',math('POWER',noise_power,value('_'+long+'NoiseContrast')),math('SUBTRACT',1,smooth)),value('_'+long+'NoiseIntensity'))
        mask=math('SUBTRACT',v,math('MULTIPLY',erosion,v),long+' Noise Erosion',True)
        masks[ch]=math('MINIMUM',math('MULTIPLY',math('MULTIPLY',mask,mask),parameter('_'+long+'MaskIntensity')),1,long+' Paint Mask')
    relief=node('ShaderNodeSeparateColor','Relief Mask');set_input(relief.inputs['Color'],color('_Mask_Relief_Diffuse'))
    albedo=mix(color('_Diffuse1'),color('_DiffuseG'),masks['G'],'Albedo Base-Green')
    albedo=mix(albedo,color('_DiffuseB'),masks['B'],'Albedo Blue')
    if value('_ReliefONOff'): albedo=mix(albedo,color('_Relief_DIffuse'),relief.outputs['Red'],'Relief Albedo')
    hsv=node('ShaderNodeHueSaturation','Texture HSV')
    hsv.inputs['Hue'].default_value=(.5+value('_TextureHUE')) % 1.0
    hsv.inputs['Saturation'].default_value=value('_TextureSaturation',1)
    hsv.inputs['Value'].default_value=value('_TextureValue',1)
    set_input(hsv.inputs['Color'],albedo)
    tint=node('ShaderNodeMixRGB','Material Color');tint.blend_type='MULTIPLY';tint.inputs[0].default_value=1
    set_input(tint.inputs[1],hsv.outputs['Color']);tint.inputs[2].default_value=tuple(colors.get('_MaterialColor',(1,1,1,0)))
    albedo=mix(tint.outputs[0],color('_DiffuseR'),masks['R'],'Albedo Red (last)')
    normal=color('_Normal1',(.5,.5,1,1))
    for ch in ['R','G','B']: normal=mix(normal,color('_Normal'+ch,(.5,.5,1,1)),masks[ch],'Normal '+ch)
    if value('_ReliefNormalONOff'): normal=mix(normal,color('_Relief_Normal',(.5,.5,1,1)),relief.outputs['Red'],'Relief Normal')
    normal_map=node('ShaderNodeNormalMap','Blended Tangent Normal');set_input(normal_map.inputs['Color'],normal)
    def gloss(suffix,long):
        # This shader hardcodes the Normal-source value to zero.
        if value('_SmoothnessSourse'+long): return 0.0
        return math('MULTIPLY',alpha('_Diffuse'+suffix),value('_'+long+'Gloss',1),long+' Smoothness')
    smooth=gloss('1','Base')
    # The source uses lerp(base, red, 0.0): red has no effect on gloss.
    for ch,long in [('G','Green'),('B','Blue')]:
        f=channels.outputs[long];g=gloss(ch,long)
        smooth=math('ADD',math('MULTIPLY',smooth,math('SUBTRACT',1,f)),math('MULTIPLY',g,f),long+' Raw Vertex Gloss Blend')
    smooth=math('MINIMUM',math('MULTIPLY',smooth,parameter('_SmoothnessMultiplier',1)),1,'Smoothness Clamp')
    rough=math('SUBTRACT',1,smooth,'Smoothness To Roughness')
    bsdf=node('ShaderNodeBsdfPrincipled','Principled');bsdf.location=(250,300)
    bsdf.inputs['Metallic'].default_value=0;bsdf.inputs['Alpha'].default_value=1
    bsdf.inputs['Specular IOR Level'].default_value=max(0,min(1,value('_Specular')/.08))
    set_input(bsdf.inputs['Base Color'],albedo);set_input(bsdf.inputs['Normal'],normal_map.outputs['Normal']);set_input(bsdf.inputs['Roughness'],rough)
    out=node('ShaderNodeOutputMaterial','Output');out.location=(580,300);tree.links.new(bsdf.outputs['BSDF'],out.inputs['Surface'])
    material.use_backface_culling=True
    if hasattr(material,'surface_render_method'): material.surface_render_method='DITHERED'
    for key,val in (metadata or {}).items(): material[key]=val
    material['umd_vertex_graph_version']=2
    material['vertex_paint_shader']='Custom/VertexColorMix4_v2'
    material['vertex_paint_help']='Use the active render color attribute. Black: base; R/G/B: painted layers. Unity world XZ maps to Blender world XY.'
    from .vertex_paint_layout import arrange
    arrange(tree, prefix)
    return material
