# opens the saved pedestal.blend in background blender and checks it against D-07 (and that nothing from D-03 slipped in)
import json

import bpy
from mathutils import Vector
res = {'checks': {}}
def ok(name, cond, got):
    res['checks'][name] = {'ok': bool(cond), 'got': got}
o = bpy.data.objects.get('Pedestal')
ok('object Pedestal exists', o is not None, [x.name for x in bpy.data.objects])
if o is not None:
    dims = [round(v, 4) for v in o.dimensions]
    ok('diameter 0.60 m', abs(dims[0] - 0.6) < 2e-3 and abs(dims[1] - 0.6) < 2e-3, dims)
    ok('height 0.90 m', abs(dims[2] - 0.9) < 2e-3, dims)
    ws = [o.matrix_world @ Vector(c) for c in o.bound_box]
    zmin = round(min(v.z for v in ws), 4)
    cx = round(sum(v.x for v in ws) / 8, 4)
    cy = round(sum(v.y for v in ws) / 8, 4)
    ok('base at z = 0', abs(zmin) < 2e-3, zmin)
    ok('centred on the origin', abs(cx) < 2e-3 and abs(cy) < 2e-3, [cx, cy])
    if o.type == 'MESH':
        ring = sorted({round(v.co.z, 4) for v in o.data.vertices})
        n_ring = sum(1 for v in o.data.vertices if abs(v.co.z - o.data.vertices[0].co.z) < 1e-4)
        ok('64 vertices around', n_ring == 64 or len(o.data.vertices) in (128, 130), [len(o.data.vertices), n_ring])
    mats = [m.name for m in o.data.materials if m] if hasattr(o.data, 'materials') else []
    ok('material PedestalMatte', mats[:1] == ['PedestalMatte'], mats)
    m = bpy.data.materials.get('PedestalMatte')
    if m is not None and m.use_nodes:
        b = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if b is not None:
            col = [round(x, 3) for x in b.inputs['Base Color'].default_value[:3]]
            rough = round(b.inputs['Roughness'].default_value, 3)
            ok('base colour 0.20 0.45 0.70', all(abs(a - e) < 1e-2 for a, e in zip(col, (0.2, 0.45, 0.7), strict=True)), col)
            ok('roughness 0.8', abs(rough - 0.8) < 1e-2, rough)
        else:
            ok('principled bsdf present', False, [n.type for n in m.node_tree.nodes])
ok('no D-03 material', 'PedestalGloss' not in bpy.data.materials, [x.name for x in bpy.data.materials])
res['all_ok'] = all(c['ok'] for c in res['checks'].values())
print('VERIFY ' + json.dumps(res))
