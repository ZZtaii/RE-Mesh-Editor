"""Retail integration: changed materials cannot silently discard correctives.
Run Blender with -- --source-file ORIGINAL.mesh.230110883. Fixture not bundled.
"""
import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
import addon_utils
import bpy
import numpy as np


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-file',type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root.parent));addon_utils.enable(root.name,default_set=True)
    io=importlib.import_module(root.name+'.modules.mesh.blender_re_mesh')
    sf6=importlib.import_module(root.name+'.modules.mesh.sf6_source')
    helpers=importlib.import_module(root.name+'.tests.test_sf6_hybrid_export')
    source=sf6.SourceMesh(args.source_file.read_bytes())
    io.importREMeshFile(str(args.source_file),helpers.IMPORT_OPTIONS.copy())
    c=bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
    meshes=[o for o in c.all_objects if o.type=='MESH' and sf6.META in o]
    shaped=min((o for o in meshes if o.data.shape_keys and len(o.data.shape_keys.key_blocks)>1),key=lambda o:len(o.data.vertices))
    plain=next(o for o in meshes if not list(source.part_shapes(json.loads(o[sf6.META]))))
    opts=dict(targetCollection=c.name,rotate90=True,selectedOnly=False,exportAllLODs=False,
              splitLoopVertices=False,useBlenderMaterialName=False,preserveBoneMatrices=True,
              exportBoundingBoxes=False,autoSolveRepeatedUVs=False,preserveSharpEdges=False,
              limitTotal=True,limitTotalCount=6,normalizeWeights=True,exportBlendShapes=True,sf6HybridPreserve=True)
    original=shaped.data.materials[0]
    positions=[np.array([v.co[:] for v in key.data]) for key in shaped.data.shape_keys.key_blocks]
    counts=lambda:tuple(len(x) for x in (bpy.data.objects,bpy.data.meshes,bpy.data.collections,bpy.data.shape_keys))
    before=counts()
    with tempfile.TemporaryDirectory() as temp:
        target=Path(temp)/args.source_file.name
        for mode in ('material_only','material_plus_normalization','missing_slot','object_linked'):
            shaped.data.materials[0]=original
            target.write_bytes(b'existing destination must survive refusal')
            if mode=='missing_slot':shaped.data.materials[0]=None
            elif mode=='object_linked':
                shaped.material_slots[0].link='OBJECT'
                shaped.material_slots[0].material=bpy.data.materials.new('MaterialObjectReplacement')
            else:shaped.data.materials[0]=bpy.data.materials.new('MaterialSafetyReplacement')
            if mode=='material_plus_normalization':plain.shape_key_add(name='Basis');plain.shape_key_add(name='ExtraKey')
            expected='Assign a material' if mode=='missing_slot' else 'Material reassignment on source corrective geometry'
            try:
                io.exportREMeshFile(str(target),dict(opts))
                raise AssertionError('Unsafe material export unexpectedly succeeded')
            except ValueError as exc:assert expected in str(exc),str(exc)
            assert target.read_bytes()==b'existing destination must survive refusal'
            if mode=='material_plus_normalization':plain.shape_key_clear()
            shaped.material_slots[0].link='DATA';shaped.data.materials[0]=original
            assert counts()==before,(before,counts())
            for old,key in zip(positions,shaped.data.shape_keys.key_blocks):assert np.array_equal(old,np.array([v.co[:] for v in key.data]))
            print('PASS '+mode+'_refuses_atomically',flush=True)
        # Duplicate slots assigned to separate faces must remain one shaped
        # source section rather than becoming ambiguous ordinary fragments.
        shaped.data.materials.append(original)
        shaped.data.polygons[0].material_index=len(shaped.data.materials)-1
        assert io.exportREMeshFile(str(target),dict(opts))
        duplicate_output=sf6.SourceMesh(target.read_bytes())
        duplicate_parts=helpers.lod0_parts(duplicate_output)
        for p in source.parts:
            if p['lod']!=0:continue
            expected=list(source.part_shapes(p))
            actual=list(duplicate_output.part_shapes(duplicate_parts[helpers.key(source,p)]))
            assert [x[0] for x in actual]==[x[0] for x in expected]
            for a,b in zip(actual,expected):assert np.array_equal(a[1],b[1])
        shaped.data.polygons[0].material_index=0
        shaped.data.materials.pop(index=len(shaped.data.materials)-1)
        assert counts()==before
        print('PASS equivalent_used_slots_retain_all_source_correctives',flush=True)
        # A material change on an unshaped source part is safe to rebuild, and
        # must keep all unrelated source corrective names and deltas intact.
        plain.data.materials[0]=bpy.data.materials.new('UnshapedReplacement')
        assert io.exportREMeshFile(str(target),dict(opts))
        output=sf6.SourceMesh(target.read_bytes())
        assert 'UnshapedReplacement' in output.materials
        out_parts=helpers.lod0_parts(output)
        for p in source.parts:
            if p['lod']!=0:continue
            expected=list(source.part_shapes(p))
            if not expected:continue
            actual=list(output.part_shapes(out_parts[helpers.key(source,p)]))
            assert [x[0] for x in actual]==[x[0] for x in expected]
            for a,b in zip(actual,expected):assert np.array_equal(a[1],b[1])
        assert counts()==before
        print('PASS unshaped_reassignment_retains_all_source_correctives',flush=True)
    print('ALL_MATERIAL_SAFETY_TESTS_PASSED 6',flush=True)

if __name__=='__main__':main()
