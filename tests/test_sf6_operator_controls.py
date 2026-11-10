"""Run in Blender --background --factory-startup --python-exit-code 1.

Exercises real operators plus controlled helper failures. Game fixtures stay local.
"""
import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

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
    addon = importlib.import_module(repo.name)
    mesh_io = importlib.import_module(repo.name + '.modules.mesh.blender_re_mesh')
    ops = importlib.import_module(repo.name + '.modules.mesh.re_mesh_operators')
    spec = importlib.util.spec_from_file_location('source_test_options', repo/'tests/test_sf6_source_preservation.py')
    source_tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source_tests)
    preferences = bpy.context.preferences.addons[repo.name].preferences
    # This used to crash hidden background processes in wm.console_toggle.
    preferences.showConsole = True
    report = []

    def passed(name, **details):
        report.append(dict(test=name, passed=True, **details))
        print('PASS ' + json.dumps(report[-1]), flush=True)

    def cancelled(call, message=None):
        try:
            result = call()
        except RuntimeError as error:
            assert 'Traceback' not in str(error), str(error)
            if message:
                assert message in str(error), str(error)
        else:
            assert result == {'CANCELLED'}, result

    with tempfile.TemporaryDirectory(prefix='sf6-operator-tests-') as temporary:
        root = Path(temporary)
        preferences.textureCachePath = str(root/'texture-cache')
        original = args.source_dir/'esf033_002_01.mesh.230110883'
        mesh_io.importREMeshFile(str(original), source_tests.IMPORT_OPTIONS.copy())
        collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        assert all('SF6OriginalUV' not in o for o in collection.all_objects if o.type == 'MESH')
        passed('new_import_omits_unused_uv_snapshot')

        def item(path, preserve=True):
            values = {p.identifier:p.default for p in ops.ExporterNodePropertyGroup.bl_rna.properties
                      if p.identifier != 'rna_type' and hasattr(p, 'default')}
            values.update(name=collection.name, path=str(path), exportType='MESH', enabled=True,
                          invalid=False, hasChild=False, exportBlendShapes=preserve,
                          autoSolveRepeatedUVs=False, preserveSharpEdges=False)
            if preserve:
                # Any rebuild option makes an untouched mesh leave the exact-source route.
                values.update(splitLoopVertices=False, preserveBoneMatrices=True)
            return values

        def batch_review(*rows, quick=False):
            # Without a window, batch export only prepares its review. Approving
            # the ready plan is the scripted equivalent of the user's click.
            if quick:
                result = bpy.ops.re_mesh.quick_batch_export('EXEC_DEFAULT')
            else:
                result = bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=list(rows))
            assert result == {'INTERFACE'}, result
            return ops._LAST_BATCH_REVIEW

        def routes(request):
            return [row['result'].get('route') for row in request.review['files']]

        def batch_export(*rows, quick=False):
            request = batch_review(*rows, quick=quick)
            assert request.state == 'ready', request.review
            found = routes(request)
            assert request.approve(request.revision), request.result
            return found

        target = root/original.name
        assert batch_export(item(target)) == ['exact_source']
        assert target.read_bytes() == original.read_bytes()
        assert collection['BatchExport_preserveSource'] is True or collection['BatchExport_preserveSource'] == 1
        passed('batch_preserved_export_is_byte_identical')

        # Without its source metadata the mesh cannot take the exact-source route.
        # The export is still reviewed, and never claims to be an exact copy.
        target.write_bytes(b'KEEP')
        collection['SF6PreserveSource'] = False
        request = batch_review(item(target))
        assert 'exact_source' not in routes(request)
        assert target.read_bytes() == b'KEEP'
        request.cancel()
        assert target.read_bytes() == b'KEEP'
        collection['SF6PreserveSource'] = True
        passed('batch_missing_metadata_never_exports_as_exact_source')

        assert batch_export(item(target, False)) == ['ordinary']
        assert target.read_bytes()[:4] == b'MESH' and target.read_bytes() != original.read_bytes()
        assert collection['BatchExport_preserveSource'] == False
        passed('batch_forwards_explicit_ordinary_mode')

        # Export a real legacy collection through the ordinary exporter too.
        legacy = dict(source_tests.IMPORT_OPTIONS, importBlendShapes=False)
        mesh_io.importREMeshFile(str(original), legacy)
        collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        ordinary_target = root/'ordinary.mesh.230110883'
        assert batch_export(item(ordinary_target, False)) == ['ordinary']
        assert ordinary_target.read_bytes()[:4] == b'MESH'
        passed('real_legacy_mesh_ordinary_batch_export')

        # Quick Export must rebuild the list and use the saved mode, including false.
        ordinary_target.write_bytes(b'STALE')
        assert collection['BatchExport_preserveSource'] == False
        assert batch_export(quick=True) == ['ordinary']
        assert ordinary_target.read_bytes()[:4] == b'MESH'
        passed('quick_export_reloads_saved_false_mode')

        # Exercise the population function with sparse old collection settings,
        # on a real item list like the one the batch window owns.
        bpy.types.Scene.operator_control_items = bpy.props.CollectionProperty(type=ops.ExporterNodePropertyGroup)
        try:
            items = bpy.context.scene.operator_control_items
            for key in list(collection.keys()):
                if key.startswith('BatchExport_'):
                    del collection[key]
            preferences.default_exportBlendShapes = True
            items.clear()
            ops.populateCollectionList(items, collection, 0, '')
            assert items[0].exportBlendShapes is False
            collection['BatchExport_preserveSource'] = True
            items.clear()
            ops.populateCollectionList(items, collection, 0, '')
            assert items[0].exportBlendShapes == True
            items.clear()
        finally:
            del bpy.types.Scene.operator_control_items
        collection['BatchExport_preserveSource'] = False
        passed('batch_mode_ignores_global_preference_and_honors_saved_batch_override')

        # No destination/last-success fields should change on a helper failure.
        collection['BatchExport_path'] = 'previous-success'
        direct = dict(filepath=str(target), targetCollection=collection.name, exportBlendShapes=False)
        target.write_bytes(b'KEEP')
        with patch.object(addon, 'exportREMeshFile', return_value=False):
            cancelled(lambda:bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **direct), 'export failed')
        assert target.read_bytes() == b'KEEP' and collection['BatchExport_path'] == 'previous-success'
        passed('direct_false_result_is_cancelled')
        for error in (ValueError('Unsupported edit'), OSError('Destination unavailable')):
            with patch.object(addon, 'exportREMeshFile', side_effect=error):
                cancelled(lambda:bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **direct), str(error))
        assert target.read_bytes() == b'KEEP' and collection['BatchExport_path'] == 'previous-success'
        passed('direct_validation_and_io_errors_have_no_python_traceback')

        # A first item that cannot be published must not prevent the next file exporting.
        later = root/'later.mesh.230110883'
        occupied = root/'occupied.mesh.230110883'
        occupied.mkdir()
        request = batch_review(item(occupied, False), item(later, False))
        if request.state == 'ready':
            assert not request.approve(request.revision)
            assert request.result['failed_count'] == 1 and request.result['succeeded_count'] == 1, request.result
            assert later.read_bytes()[:4] == b'MESH'
        else:
            assert request.review['report']['failed_count'] == 1 and request.review['report']['ready_count'] == 1
            request.cancel()
        assert occupied.is_dir()
        passed('batch_counts_failure_and_continues')

        collection['BatchExport_path'] = str(occupied)
        collection['BatchExport_preserveSource'] = False
        request = batch_review(quick=True)
        assert request.state == 'blocked' or not request.approve(request.revision)
        assert occupied.is_dir()
        request.cancel()
        passed('quick_export_propagates_failure')

        cancelled(lambda:bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[]))
        passed('empty_batch_is_not_reported_as_success')
        # Real bad-file/valid-file import proves continuation and scene protection.
        bad = root/'invalid.mesh.230110883'
        bad.write_bytes(b'not a mesh')
        valid = root/original.name
        shutil.copyfile(original, valid)
        before = set(bpy.data.objects.keys())
        import_settings = dict(source_tests.IMPORT_OPTIONS)
        import_settings.pop('importAllLODs')
        import_settings.update(directory=str(root), mergeImportedArmatures=False)
        cancelled(lambda:bpy.ops.re_mesh.importfile('EXEC_DEFAULT',
            files=[{'name':bad.name}], **import_settings), 'not an RE mesh')
        assert set(bpy.data.objects.keys()) == before
        passed('bad_single_import_reports_reason_and_preserves_scene')
        assert bpy.ops.re_mesh.importfile('EXEC_DEFAULT', files=[{'name':bad.name},{'name':valid.name}],
                                          **import_settings) == {'FINISHED'}
        collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        assert collection.get('SF6PreserveSource')
        assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', filepath=str(root/'after-import.mesh.230110883'),
                                         targetCollection=collection.name, exportBlendShapes=True) == {'FINISHED'}
        assert (root/'after-import.mesh.230110883').read_bytes() == original.read_bytes()
        passed('multi_import_continues_after_invalid_file')

        # A false helper result must not consume Clear Scene before a later success.
        imports = []
        def partial_import(path, options):
            imports.append(options.copy())
            return len(imports) == 2
        with patch.object(addon, 'importREMeshFile', partial_import):
            assert bpy.ops.re_mesh.importfile('EXEC_DEFAULT',
                files=[{'name':bad.name},{'name':valid.name},{'name':bad.name}], **import_settings) == {'FINISHED'}
        assert [options['clearScene'] for options in imports] == [True, True, False]
        passed('multi_import_false_result_does_not_consume_clear_scene')

        # Merge into the first successful armature even without collections.
        merge_settings = dict(import_settings, mergeImportedArmatures=True,
                              createCollections=False, importArmatureOnly=True,
                              importBlendShapes=False)
        assert bpy.ops.re_mesh.importfile('EXEC_DEFAULT',
            files=[{'name':bad.name},{'name':valid.name},{'name':valid.name}],
            **merge_settings) == {'FINISHED'}
        assert len(bpy.data.armatures) == 1
        assert len([o for o in bpy.data.objects if o.type == 'ARMATURE']) == 1
        assert len(bpy.data.armatures[0].bones) > 0
        passed('multi_import_merges_after_failure_without_collections')

    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('OPERATOR_CONTROLS_TESTS_PASSED ' + str(len(report)), flush=True)


if __name__ == '__main__':
    main()
