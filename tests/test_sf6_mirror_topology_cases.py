"""Synthetic mirror, transform, and per-shape topology guards for Blender."""
import importlib, json, sys, types
from pathlib import Path
import bpy
import numpy as np
package=types.ModuleType('_sf6_mirror_audit');package.__path__=[str(Path(__file__).resolve().parents[1]/'modules/mesh')];sys.modules[package.__name__]=package
ev=importlib.import_module(package.__name__+'.sf6_evaluated')
RESULTS=[]
def make():
    for o in list(bpy.data.objects):bpy.data.objects.remove(o,do_unlink=True)
    for c in list(bpy.data.collections):bpy.data.collections.remove(c)
    c=bpy.data.collections.new('fixture');bpy.context.scene.collection.children.link(c)
    mesh=bpy.data.meshes.new('triangle');mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    uv=mesh.uv_layers.new(name='UVMap')
    for i,co in enumerate([(0,0),(1,0),(0,1)]):uv.data[i].uv=co
    o=bpy.data.objects.new('Group_0_Sub_0__fixture',mesh);c.objects.link(o)
    o['SF6SourceMeta']=json.dumps({'lod':0});o['SF6SourceSHA256']='synthetic'
    for name,dom,vals in [('sf6_source_vertex','POINT',[0,1,2]),('sf6_source_face','FACE',[0])]:
        a=mesh.attributes.new(name,'INT',dom);a.data.foreach_set('value',vals)
    o.vertex_groups.new(name='Bone').add([0,1,2],1,'REPLACE')
    o.shape_key_add(name='Basis');key=o.shape_key_add(name='Lift');key.data[1].co.z=.25
    o.select_set(True);bpy.context.view_layer.objects.active=o
    return c,o

def counts():return tuple(len(x) for x in (bpy.data.objects,bpy.data.meshes,bpy.data.collections,bpy.data.shape_keys))
def run(name,setup,verts=None,faces=None,error=None):
    c,o=make();setup(o);bpy.context.view_layer.update();before=counts();matrix=o.matrix_world.copy();original=[[tuple(v.co) for v in k.data] for k in o.data.shape_keys.key_blocks]
    try:
        with ev.evaluated_hybrid_collection(c) as (snapshot,selected,report):
            if error:raise AssertionError('Expected topology rejection')
            copy=next(x for x in snapshot.all_objects if x.type=='MESH')
            assert len(copy.data.vertices)==verts,(name,len(copy.data.vertices))
            assert len(copy.data.polygons)==faces
            assert copy.data.shape_keys.key_blocks.get('Lift')
            keys=copy.data.shape_keys.key_blocks
            delta=np.array([tuple(v.co) for v in keys['Lift'].data])-np.array([tuple(v.co) for v in keys[0].data])
            assert np.isfinite(delta).all() and np.any(delta)
            if name.startswith('mirror_'):
                assert np.count_nonzero(np.linalg.norm(delta,axis=1)>.001)==2
                assert np.allclose(delta[delta[:,2]>.001,2],.25)
            assert all(len(p.vertices)==3 for p in copy.data.polygons)
    except ev.HybridExportError as e:assert error and error in str(e),(error,str(e))
    assert counts()==before,('leaked datablocks',before,counts())
    assert o.matrix_world==matrix
    assert original==[[tuple(v.co) for v in k.data] for k in o.data.shape_keys.key_blocks]
    RESULTS.append(dict(test=name,passed=True));print('PASS '+name,flush=True)
def mirror(o):
    m=o.modifiers.new('Mirror','MIRROR');m.use_mirror_merge=True;m.use_clip=True
run('mirror_stable_seam_retains_correctives',mirror,4,2)
def unsafe(o):
    mirror(o);o.data.shape_keys.key_blocks[1].data[0].co.x=.2
run('mirror_shape_dependent_merge_refuses',unsafe,error='topology')
def unmerged(o):
    mirror(o);o.modifiers[-1].use_mirror_merge=False
run('mirror_without_merge_retains_correctives',unmerged,6,2)
run('negative_object_scale_keeps_valid_snapshot',lambda o:setattr(o,'scale',(-1,1,1)),3,1)
def solidify(o):
    m=o.modifiers.new('Solidify','SOLIDIFY');m.thickness=.05
run('solidify_retains_correctives',solidify,24,8)
print(json.dumps(dict(blender=bpy.app.version_string,results=RESULTS)),flush=True)
