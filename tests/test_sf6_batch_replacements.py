"""Run in isolated Blender with local original SF6 fixtures.

Reproduce a replacement object in a source-preserved collection through the real
batch operator, including mixed modes and saved settings. No assets are shipped.
"""
import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import addon_utils
import bpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--report-json', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + '.modules.mesh.blender_re_mesh')
    ops = importlib.import_module(repo.name + '.modules.mesh.re_mesh_operators')
    spec = importlib.util.spec_from_file_location('source_options', repo/'tests/test_sf6_source_preservation.py')
    source_tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source_tests)
    preferences = bpy.context.preferences.addons[repo.name].preferences
    preferences.showConsole = True
    report = []

    def passed(name):
        report.append(dict(test=name, passed=True))
        print('PASS ' + name, flush=True)

    def item(collection, path, preserve):
        values = {p.identifier:p.default for p in ops.ExporterNodePropertyGroup.bl_rna.properties
                  if p.identifier != 'rna_type' and hasattr(p, 'default')}
        values.update(name=collection.name, path=str(path), exportType='MESH', enabled=True,
                      invalid=False, hasChild=False, exportBlendShapes=preserve,
                      autoSolveRepeatedUVs=False, preserveSharpEdges=False)
        return values

    with tempfile.TemporaryDirectory(prefix='sf6-batch-') as temporary:
        root = Path(temporary)
        preferences.textureCachePath = str(root/'textures')
        original = args.source_dir/'esf033_002_01.mesh.230110883'
        mesh_io.importREMeshFile(str(original), source_tests.IMPORT_OPTIONS.copy())
        collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        old = next(o for o in collection.all_objects if o.type == 'MESH')
        replacement = old.copy()
        replacement.data = old.data.copy()
        old_name = old.name
        for key in list(replacement.keys()):
            if key.startswith('SF6'):
                del replacement[key]
        for attribute in list(replacement.data.attributes):
            if attribute.name.startswith('sf6_source_'):
                replacement.data.attributes.remove(attribute)
        for owner in old.users_collection:
            owner.objects.link(replacement)
        bpy.data.objects.remove(old, do_unlink=True)
        replacement.name = old_name
        target = root/'replacement.mesh.230110883'
        target.write_bytes(b'KEEP')
        collection['BatchExport_path'] = 'previous-success'
        collection['BatchExport_exportBlendShapes'] = True

        try:
            mesh_io.exportREMeshFile(str(target), dict(targetCollection=collection.name,
                                                     exportBlendShapes=True, rotate90=True))
        except ValueError as error:
            assert replacement.name in str(error), str(error)
            assert 'Preserve Source Data' in str(error), str(error)
            assert 'ordinary' in str(error), str(error)
            assert 'deformation' in str(error), str(error)
        else:
            raise AssertionError('Replacement was silently exported in source mode')
        assert target.read_bytes() == b'KEEP'
        passed('replacement_error_explains_explicit_mode_choice')

        # Call the batch implementation with real Blender children so reports
        # can be checked without mocking the export or source validation.
        messages = []
        batch = SimpleNamespace(skipPrompt=False,
            itemList_items=[SimpleNamespace(**item(collection, target, True))],
            report=lambda kinds,message:messages.append(message))
        assert ops.WM_OT_REBatchExporter.execute(batch, bpy.context) == {'CANCELLED'}
        assert target.read_bytes() == b'KEEP'
        assert collection['BatchExport_path'] == 'previous-success'
        assert collection['BatchExport_exportBlendShapes'] == True
        assert any('1/1 files failed' in m and replacement.name in m
                   and 'Preserve Source Data' in m for m in messages), messages
        assert not any('finished successfully' in m for m in messages)
        passed('batch_reports_replacement_reason_and_preserves_destination_and_settings')

        mesh_io.importREMeshFile(str(original), dict(source_tests.IMPORT_OPTIONS, clearScene=False))
        untouched = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        second = root/'untouched.mesh.230110883'
        assert bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[
            item(collection, target, True), item(untouched, second, True)]) == {'CANCELLED'}
        assert target.read_bytes() == b'KEEP'
        assert second.read_bytes() == original.read_bytes()
        passed('failed_replacement_does_not_block_later_preserved_export')

        assert bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[
            item(collection, target, False), item(untouched, second, True)]) == {'FINISHED'}
        rebuilt = target.read_bytes()
        assert rebuilt[:4] == b'MESH' and rebuilt != original.read_bytes()
        assert second.read_bytes() == original.read_bytes()
        assert collection['BatchExport_exportBlendShapes'] == False
        assert untouched['BatchExport_exportBlendShapes'] == True
        passed('mixed_batch_rebuilds_replacement_only_when_explicitly_selected')

        saved = root/'batch-settings.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(saved))
        bpy.ops.wm.open_mainfile(filepath=str(saved))
        target.write_bytes(b'REEXPORT')
        second.write_bytes(b'REEXPORT')
        assert bpy.ops.re_mesh.quick_batch_export('EXEC_DEFAULT') == {'FINISHED'}
        assert target.read_bytes() == rebuilt
        assert second.read_bytes() == original.read_bytes()
        passed('quick_batch_keeps_each_mode_after_save_reopen')

    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('BATCH_REPLACEMENT_TESTS_PASSED ' + str(len(report)), flush=True)


if __name__ == '__main__':
    main()
