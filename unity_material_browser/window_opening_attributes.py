"""Подготовка UV-атрибутов Unity Window на копии меша.

Проём = связный набор граней с одним материалом, непрерывным UV и совпадающей
плоскостью. Размер UV на метр вычисляется по треугольникам в мировых координатах.
"""
from collections import defaultdict, deque
import math


NAMES = ('UnityWindowUV0ZW', 'UnityWindowAtlasMin', 'UnityWindowAtlasMax')


def opening_groups(obj, slot_index):
    mesh=obj.data
    if obj.type!='MESH' or not mesh.uv_layers.active:
        raise ValueError('Для окон нужны mesh и активный UV-канал')
    if any(mesh.attributes.get(name) for name in NAMES):
        raise ValueError('На меше уже есть оконные атрибуты: автоматическая замена запрещена')
    uv=mesh.uv_layers.active.data
    faces={p.index:p for p in mesh.polygons if p.material_index==slot_index}
    if not faces:raise ValueError('У материала нет граней')
    matrix=obj.matrix_world
    normal_matrix=matrix.to_3x3().inverted().transposed()
    normals={i:(normal_matrix @ p.normal).normalized() for i,p in faces.items()}
    edges=defaultdict(list)
    for index,p in faces.items():
        loops=list(p.loop_indices)
        for k,ia in enumerate(loops):
            ib=loops[(k+1)%len(loops)]
            va=mesh.loops[ia].vertex_index;vb=mesh.loops[ib].vertex_index
            if va==vb:continue
            if va<vb:key=(va,vb);coords=(uv[ia].uv.copy(),uv[ib].uv.copy())
            else:key=(vb,va);coords=(uv[ib].uv.copy(),uv[ia].uv.copy())
            edges[key].append((index,coords))
    adjacency=defaultdict(set)
    for linked in edges.values():
        if len(linked)!=2:continue
        (a,ua),(b,ub)=linked
        if normals[a].dot(normals[b])<0.999:continue
        if any((ua[i]-ub[i]).length>0.0001 for i in range(2)):continue
        adjacency[a].add(b);adjacency[b].add(a)
    groups=[];remaining=set(faces)
    while remaining:
        start=min(remaining);remaining.remove(start);group=[];q=deque([start])
        while q:
            face=q.popleft();group.append(face)
            for nxt in adjacency[face]:
                if nxt in remaining:remaining.remove(nxt);q.append(nxt)
        groups.append(group)
    mesh.calc_loop_triangles()
    triangles=defaultdict(list)
    for tri in mesh.loop_triangles:
        if tri.polygon_index in faces:triangles[tri.polygon_index].append(tri)
    result=[]
    for group in groups:
        corners=[i for f in group for i in faces[f].loop_indices]
        minuv=(min(uv[i].uv.x for i in corners),min(uv[i].uv.y for i in corners))
        maxuv=(max(uv[i].uv.x for i in corners),max(uv[i].uv.y for i in corners))
        if any(maxuv[i]-minuv[i]<1e-6 for i in range(2)):
            raise ValueError('Вырожденная UV-развёртка в проёме')
        sum_u=sum_v=area_total=0.0
        for f in group:
            for tri in triangles[f]:
                ls=list(tri.loops);points=[matrix @ mesh.vertices[mesh.loops[l].vertex_index].co for l in ls]
                uvv=[uv[l].uv for l in ls];e1=points[1]-points[0];e2=points[2]-points[0]
                g11=e1.dot(e1);g22=e2.dot(e2);g12=e1.dot(e2);det=g11*g22-g12*g12
                area=e1.cross(e2).length*.5
                if det<1e-14 or area<1e-8:continue
                def gradient(component):
                    a=uvv[1][component]-uvv[0][component];b=uvv[2][component]-uvv[0][component]
                    return (e1*((a*g22-b*g12)/det)+e2*((b*g11-a*g12)/det)).length
                sum_u+=gradient(0)*area;sum_v+=gradient(1)*area;area_total+=area
        if area_total<=0:raise ValueError('Вырожденная геометрия оконного проёма')
        su,sv=sum_u/area_total,sum_v/area_total
        if not all(math.isfinite(x) and x>1e-6 for x in (su,sv)):raise ValueError('Не удалось вычислить масштаб оконного UV')
        result.append({'faces':group,'loops':corners,'min':minuv,'max':maxuv,'scale':(su,sv)})
    return result


def apply_groups(obj,groups):
    mesh=obj.data
    if any(mesh.attributes.get(name) for name in NAMES):raise ValueError('Оконные атрибуты уже существуют')
    arrays={name:[0.0]*(len(mesh.loops)*3) for name in NAMES}
    for group in groups:
        values={NAMES[0]:(*group['scale'],1.0),NAMES[1]:(*group['min'],0.0),NAMES[2]:(*group['max'],0.0)}
        for loop in group['loops']:
            for name,value in values.items():arrays[name][loop*3:loop*3+3]=value
    created=[]
    try:
        for name in NAMES:
            attr=mesh.attributes.new(name,'FLOAT_VECTOR','CORNER');created.append(attr)
            attr.data.foreach_set('vector',arrays[name])
    except Exception:
        for attr in created:mesh.attributes.remove(attr)
        raise
    return len(groups)
