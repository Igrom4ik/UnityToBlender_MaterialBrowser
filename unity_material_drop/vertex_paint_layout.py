"""Readable left-to-right layout for the vertex-paint shader, without rewiring."""

def arrange(tree, prefix='UMD'):
    nodes=[n for n in tree.nodes if n.type not in {'FRAME','REROUTE'}]
    incoming={n:set() for n in nodes}
    for link in tree.links:
        if link.to_node in incoming and link.from_node in incoming: incoming[link.to_node].add(link.from_node)
    byname={n.name:n for n in nodes}
    groups={};assigned={}
    def collect(end,stop):
        found=set()
        def visit(n):
            if n is None or n in stop or n in found:return
            found.add(n)
            for p in incoming.get(n,()):visit(p)
        visit(end);return found
    def add(key,items):
        groups[key]=[n for n in nodes if n in items and n not in assigned]
        for n in groups[key]:assigned[n]=key
    source={n for n in nodes if n.type=='TEX_IMAGE'}
    add('coordinates',{n for n in nodes if n.type in {'TEX_COORD','NEW_GEOMETRY','SEPXYZ','COMBXYZ','MAPPING','UVMAP'}})
    add('textures',source)
    paint=byname.get(prefix+' Vertex Paint (active color attribute)')
    rgb=byname.get(prefix+' Vertex RGB')
    power=byname.get(prefix+' Noise Power 0.47')
    add('paint_noise',collect(power,set(assigned))|{n for n in (paint,rgb) if n})
    for ch in ['Red','Green','Blue']:
        add(ch,collect(byname.get(prefix+' '+ch+' Paint Mask'),set(assigned)))
    add('albedo',collect(byname.get(prefix+' Albedo Red (last)'),set(assigned)))
    add('normal',collect(byname.get(prefix+' Blended Tangent Normal'),set(assigned)))
    add('roughness',collect(byname.get(prefix+' Smoothness To Roughness'),set(assigned)))
    add('output',set(nodes)-set(assigned))
    # Remove only frames created by this layout. Retain all actual shader nodes.
    for n in list(tree.nodes):
        if n.type=='FRAME' and n.get('umd_layout_frame'):
            for child in list(tree.nodes):
                if child.parent==n:child.parent=None
            tree.nodes.remove(n)
    labels={
        'coordinates':'01 | UV / мировые координаты',
        'textures':'02 | Цвет / нормали слоёв',
        'paint_noise':'03 | Vertex RGB / шум',
        'Red':'04 R | Красная маска',
        'Green':'04 G | Зелёная маска',
        'Blue':'04 B | Синяя маска',
        'albedo':'05 | Цвет: G → B → HSV → R',
        'normal':'06 | Нормали: R → G → B',
        'roughness':'07 | Alpha → Smoothness → Roughness',
        'output':'08 | Итоговый материал',
    }
    colors={'Red':(.30,.09,.09),'Green':(.08,.25,.10),'Blue':(.08,.13,.32),'textures':(.23,.15,.08),'albedo':(.25,.16,.07),'normal':(.09,.14,.28),'roughness':(.15,.15,.15)}
    widths={'TEX_IMAGE':260,'MAPPING':200,'BSDF_PRINCIPLED':270}
    heights={'TEX_IMAGE':390,'MAPPING':480,'BSDF_PRINCIPLED':780,'TEX_COORD':350,'NEW_GEOMETRY':350}
    def make_frame(key,x,y):
        items=groups[key]
        frame=tree.nodes.new('NodeFrame');frame.name=prefix+' Layout '+key;frame.label=labels[key]
        frame['umd_layout_frame']=True;frame.use_custom_color=True;frame.color=colors.get(key,(.10,.19,.20));frame.label_size=24
        frame.location=(x,y)
        levels={}
        def depth(n):
            if n in levels:return levels[n]
            deps=[p for p in incoming[n] if p in items]
            levels[n]=1+max((depth(p) for p in deps),default=-1)
            return levels[n]
        for n in items:depth(n)
        if key=='textures':
            for idx,n in enumerate(items):
                if n.name.endswith(('_Diffuse1','_Normal1')):row=0
                elif n.name.endswith(('_DiffuseR','_NormalR')):row=1
                elif n.name.endswith(('_DiffuseG','_NormalG')):row=2
                elif n.name.endswith(('_DiffuseB','_NormalB')):row=3
                elif n.name.endswith('_NoiseTexture'):row=4
                else:row=5
                col=1 if 'Normal' in n.name else 0
                n.parent=frame;n.width=260;n.location=(60+col*360,-100-row*490)
            return 790,3200
        buckets={}
        for n in items:buckets.setdefault(levels[n],[]).append(n)
        height=200
        for col,bucket in sorted(buckets.items()):
            yy=-100
            for n in bucket:
                n.parent=frame;n.width=widths.get(n.type,190);n.location=(60+col*310,yy)
                yy-=max(heights.get(n.type,280),float(n.dimensions.y))+100
            height=max(height,-yy+60)
        return max(600,310*(max(levels.values(),default=0)+1)+120),height
    coords_w,_=make_frame('coordinates',-2600,900)
    textures_x=-2600+coords_w+240
    tex_w,_=make_frame('textures',textures_x,900)
    controls_x=textures_x+tex_w+300
    controls_w,_=make_frame('paint_noise',controls_x,900)
    masks_x=controls_x+controls_w+300
    mask_y=900;mask_w=0
    for ch in ['Red','Green','Blue']:
        w,h=make_frame(ch,masks_x,mask_y);mask_w=max(mask_w,w);mask_y-=h+200
    blend_x=masks_x+mask_w+350
    yy=900;blend_w=0
    for key in ['albedo','normal','roughness']:
        w,h=make_frame(key,blend_x,yy);blend_w=max(blend_w,w);yy-=h+240
    make_frame('output',blend_x+blend_w+400,900)
    return len(nodes)
