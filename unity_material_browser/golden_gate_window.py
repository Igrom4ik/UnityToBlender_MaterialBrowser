"""Оконный шейдер Unity: точные атрибуты либо запасной вариант для обычного UV."""
import json
from . import window_shader_adapter

MATERIAL_GUID='8427edd0c3070f14a902cd77d3cfa3ff'
TEXTURE_GUID='86b92be5dcdf2ab4288e637fbfa8de51'
SHADER='Custom/Environment/Window'


def supports(document):
    if not document:return False
    src=document.get('source',{});raw=document.get('material',{});eff=document.get('effective',{})
    toggle=eff.get('floats',{}).get('_UseFakeDepth',{}).get('value',raw.get('floats',{}).get('_UseFakeDepth',0))
    return (document.get('shader',{}).get('name')==SHADER and src.get('guid','').casefold()==MATERIAL_GUID
            and raw.get('textures',{}).get('_MainTex',{}).get('guid','').casefold()==TEXTURE_GUID and bool(toggle))


def build(material,document,metadata=None):
    if not window_shader_adapter.build_window(material,None,document,metadata):
        raise ValueError('Не удалось построить исходный оконный шейдер')
    tree=material.node_tree;nodes=tree.nodes;links=tree.links
    names=('UnityWindowUV0ZW','UnityWindowAtlasMin','UnityWindowAtlasMax')
    attrs={n.attribute_name:n for n in nodes if n.type=='ATTRIBUTE' and n.attribute_name in names}
    if set(attrs)!=set(names):raise ValueError('Нет ожидаемых узлов атрибутов Unity')
    targets={name:[link.to_socket for link in list(attrs[name].outputs['Vector'].links)] for name in names}
    def node(kind,label):
        n=nodes.new(kind);n.name='Unity Window fallback '+label;n.label=label;return n
    def put(v,s):
        if hasattr(v,'node'):links.new(v,s)
        else:s.default_value=v
    def math(op,a,b=0):
        n=node('ShaderNodeMath',op);n.operation=op;put(a,n.inputs[0]);put(b,n.inputs[1]);return n.outputs[0]
    def combine(x,y,z):
        n=node('ShaderNodeCombineXYZ','Vector');put(x,n.inputs[0]);put(y,n.inputs[1]);put(z,n.inputs[2]);return n.outputs[0]
    uv=nodes.get('Unity Window Live primary UV')
    if not uv:raise ValueError('Не найден вход UV шейдера')
    sep=node('ShaderNodeSeparateXYZ','UV');put(uv.outputs['UV'],sep.inputs[0])
    minimum=[math('FLOOR',sep.outputs[i]) for i in range(2)]
    maximum=[math('ADD',minimum[i],1) for i in range(2)]
    default_scale=node('ShaderNodeValue','Fallback UV per metre');default_scale.outputs[0].default_value=.2
    fallback={names[0]:combine(default_scale.outputs[0],default_scale.outputs[0],1),
              names[1]:combine(minimum[0],minimum[1],0),names[2]:combine(maximum[0],maximum[1],0)}
    scale=node('ShaderNodeSeparateXYZ','Available scale');put(attrs[names[0]].outputs['Vector'],scale.inputs[0])
    valid=math('GREATER_THAN',math('ADD',math('ABSOLUTE',scale.outputs[0]),math('ABSOLUTE',scale.outputs[1])),.000001)
    for name in names:
        mix=node('ShaderNodeMixRGB',name);put(valid,mix.inputs[0]);put(fallback[name],mix.inputs[1]);put(attrs[name].outputs['Vector'],mix.inputs[2])
        for target in targets[name]:links.new(mix.outputs[0],target)
    material['umd_shader_adapter']='golden-gate-reference-with-fallback/1'
    material['umd_shader_adapter_limitations']=json.dumps(['For accurate opening boundaries, prepare UnityWindow attributes from mesh UV; fallback assumes one opening per UV tile.',
        'Unity lighting and per-light shadows are approximated in Blender.'])
    return material
