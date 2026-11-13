"""Exact material-specific plan for Custom/VertexColorMix4_v2."""

def build_plan(document, ConversionPlan, TexturePlan):
    effective=document['effective']
    values={k:v['value'] for k,v in effective.get('floats',{}).items()}
    colors={k:v['value'] for k,v in effective.get('colors',{}).items()}
    plan=ConversionPlan(profile='vertex-color-mix4-v2',workflow='specular',shader_name=document['shader']['name'])
    plan.vertex_paint={'values':values,'colors':colors}
    names=['_Diffuse1','_Normal1','_DiffuseR','_NormalR','_DiffuseG','_NormalG','_DiffuseB','_NormalB','_NoiseTexture']
    if values.get('_ReliefONOff',0) or values.get('_ReliefNormalONOff',0): names.append('_Mask_Relief_Diffuse')
    if values.get('_ReliefONOff',0): names.append('_Relief_DIffuse')
    if values.get('_ReliefNormalONOff',0): names.append('_Relief_Normal')
    for name in names:
        entry=document['material']['textures'].get(name,{})
        if not entry.get('absolute_path'): continue
        normal='Normal' in name
        plan.textures.append(TexturePlan(property_name=name,role='VP_'+name,image_path=entry['absolute_path'],colorspace='Non-Color' if normal else entry.get('meta',{}).get('colorspace','sRGB'),is_normal=normal,scale=tuple(entry.get('scale',(1,1))),offset=tuple(entry.get('offset',(0,0)))))
    return plan
