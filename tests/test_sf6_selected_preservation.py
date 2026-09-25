"""Exercise the existing export operator with Selected Only + preservation ON."""
import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--report-json',type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(repo.parent))
    addon_utils.enable(repo.name,default_set=True)
    io = importlib.import_module(repo.name+'.modules.mesh.blender_re_mesh')
    sf = importlib.import_module(repo.name+'.modules.mesh.sf6_source')
    def test_module(name):
        spec=importlib.util.spec_from_file_location(name,repo/'tests'/(name+'.py'))
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    source_tests = test_module('test_sf6_source_preservation')
    table_tests = test_module('test_sf6_source_subset')
    original = args.source_dir/'esf030_001_01.mesh.230110883'
    src = sf.SourceMesh(original.read_bytes())
    io.importREMeshFile(str(original),source_tests.IMPORT_OPTIONS.copy())
    collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
    objects = [o for o in collection.all_objects if o.type == 'MESH']
    included = {p['start'] for p in src.parts if p['lod']==0 and not(p['group']==95 and p['sub'] in (0,1))}
    for obj in list(bpy.context.selected_objects):
        obj.select_set(False)
    for obj in objects:
        obj.select_set(json.loads(obj[sf.META])['start'] in included)
    next(o for o in collection.all_objects if o.type=='ARMATURE').hide_set(True)
    options = dict(targetCollection=collection.name,rotate90=True,exportBlendShapes=True,exportAllLODs=True)
    report=[]
    def passed(name,**details):
        report.append(dict(test=name,passed=True,**details))
        print('PASS '+json.dumps(report[-1]),flush=True)
    with tempfile.TemporaryDirectory(prefix='sf6-selected-source-') as directory:
        directory=Path(directory)
        output=directory/original.name
        full=directory/('full-'+original.name)
        assert io.exportREMeshFile(str(full),dict(options,selectedOnly=False))
        assert full.read_bytes() == original.read_bytes()
        passed('full_collection_preservation_is_unchanged')
        assert bpy.ops.re_mesh.exportfile(filepath=str(output),selectedOnly=True,
                                          **options) == {'FINISHED'}
        details=table_tests.validate(src,output.read_bytes(),included)
        passed('existing_selected_only_operator_preserves_filtered_source',**details)
        expected=output.read_bytes()
        body=next(o for o in objects if json.loads(o[sf.META])['start']==0)
        alternate=body.copy()
        alternate.data=body.data.copy()
        for key in list(alternate.keys()):
            del alternate[key]
        collection.objects.link(alternate)
        alternate.hide_set(True)
        assert io.exportREMeshFile(str(output),dict(options,selectedOnly=True))
        assert output.read_bytes() == expected
        bpy.data.objects.remove(alternate,do_unlink=True)
        passed('unselected_alternate_does_not_enter_source_validation')
        for obj in list(bpy.context.selected_objects):
            obj.select_set(False)
        output.write_bytes(b'KEEP DESTINATION')
        try:
            io.exportREMeshFile(str(output),dict(options,selectedOnly=True))
        except ValueError as error:
            assert 'No selected source mesh' in str(error)
        else:
            raise AssertionError('Empty selection was exported')
        assert output.read_bytes() == b'KEEP DESTINATION'
        passed('empty_selection_preserves_destination')
        for obj in objects:
            obj.select_set(json.loads(obj[sf.META])['start'] in included)
        basis=body.data.shape_keys.key_blocks[0].data[0]
        key=body.data.shape_keys.key_blocks[1].data[0]
        basis.co.x+=0.001
        key.co.x+=0.002
        assert io.exportREMeshFile(str(full),dict(options,selectedOnly=False))
        assert io.exportREMeshFile(str(output),dict(options,selectedOnly=True))
        table_tests.validate(sf.SourceMesh(full.read_bytes()),output.read_bytes(),included)
        assert output.read_bytes() != expected
        passed('selected_export_uses_existing_position_and_shape_edit_preservation')
        candidate=output.read_bytes()
        bpy.context.view_layer.update()
        io.importREMeshFile(str(output),source_tests.IMPORT_OPTIONS.copy())
        collection=bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        meshes=[o for o in collection.all_objects if o.type=='MESH']
        assert len(meshes)==13
        for obj in meshes:
            referenced={v for face in obj.data.polygons for v in face.vertices}
            assert referenced==set(range(len(obj.data.vertices))), obj.name
        options['targetCollection']=collection.name
        assert io.exportREMeshFile(str(full),dict(options,selectedOnly=False))
        assert full.read_bytes()==candidate
        passed('reimport_has_only_selected_objects_and_no_loose_vertices')
    if args.report_json:
        args.report_json.write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
