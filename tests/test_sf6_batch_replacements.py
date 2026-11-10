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
                      autoSolveRepeatedUVs=False, preserveSharpEdges=False,
                      # Rebuild options would take an untouched mesh off the exact-source route.
                      splitLoopVertices=False, preserveBoneMatrices=True)
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
        collection['BatchExport_preserveSource'] = True

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

        def review(result):
            # Without a window, batch export prepares a review and writes nothing.
            assert result == {'INTERFACE'}, result
            request = ops._LAST_BATCH_REVIEW
            assert request.state == 'ready', request.review
            return request

        def routes(request):
            return {Path(row['path']).name: row['result']['route'] for row in request.review['files']}

        # A replacement under Preserve Source is no longer a hard failure. It is
        # reviewed as a named loss, and nothing is written until that is approved.
        messages = []
        batch = SimpleNamespace(skipPrompt=False,
            itemList_items=[SimpleNamespace(**item(collection, target, True))],
            report=lambda kinds,message:messages.append(message))
        request = review(ops.WM_OT_REBatchExporter.execute(batch, bpy.context))
        assert request.review['requires_loss_approval'] is True
        assert any(loss.get('object') == replacement.name for loss in request.review['losses']), request.review['losses']
        assert target.read_bytes() == b'KEEP'
        assert collection['BatchExport_path'] == 'previous-success'
        assert collection['BatchExport_preserveSource'] == True
        assert request.cancel()
        assert target.read_bytes() == b'KEEP'
        assert collection['BatchExport_path'] == 'previous-success'
        assert not any('finished successfully' in m for m in messages)
        passed('batch_reviews_replacement_as_a_loss_and_cancel_preserves_destination_and_settings')

        mesh_io.importREMeshFile(str(original), dict(source_tests.IMPORT_OPTIONS, clearScene=False))
        untouched = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        second = root/'untouched.mesh.230110883'
        request = review(bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[
            item(collection, target, True), item(untouched, second, True)]))
        assert request.review['requires_loss_approval'] is True
        assert {loss.get('object') for loss in request.review['losses']} == {replacement.name}
        assert routes(request) == {target.name: 'hybrid', second.name: 'exact_source'}, routes(request)
        assert target.read_bytes() == b'KEEP' and not second.exists()
        assert request.approve(request.revision), request.result
        assert second.read_bytes() == original.read_bytes()
        approved_replacement = target.read_bytes()
        assert approved_replacement[:4] == b'MESH' and approved_replacement != original.read_bytes()
        passed('approved_replacement_loss_exports_beside_an_exact_untouched_mesh')

        request = review(bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[
            item(collection, target, False), item(untouched, second, True)]))
        assert request.review['requires_loss_approval'] is False
        assert routes(request) == {target.name: 'ordinary', second.name: 'exact_source'}, routes(request)
        assert request.approve(request.revision), request.result
        rebuilt = target.read_bytes()
        assert rebuilt[:4] == b'MESH' and rebuilt != original.read_bytes()
        assert second.read_bytes() == original.read_bytes()
        assert collection['BatchExport_preserveSource'] == False
        assert untouched['BatchExport_preserveSource'] == True
        passed('mixed_batch_rebuilds_replacement_only_when_explicitly_selected')

        saved = root/'batch-settings.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(saved))
        bpy.ops.wm.open_mainfile(filepath=str(saved))
        target.write_bytes(b'REEXPORT')
        second.write_bytes(b'REEXPORT')
        request = review(bpy.ops.re_mesh.quick_batch_export('EXEC_DEFAULT'))
        assert routes(request) == {target.name: 'ordinary', second.name: 'exact_source'}, routes(request)
        assert request.approve(request.revision), request.result
        assert target.read_bytes()[:4] == b'MESH' and len(target.read_bytes()) == len(rebuilt)
        assert second.read_bytes() == original.read_bytes()
        passed('quick_batch_keeps_each_mode_after_save_reopen')
    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('BATCH_REPLACEMENT_TESTS_PASSED ' + str(len(report)), flush=True)


if __name__ == '__main__':
    main()
