"""Batch, mod-folder and direct exports each keep their own Preserve Source choice.

Every export here is a real one on a retail mesh. The route a file took tells
which mode was used: 'ordinary' is Preserve Source off, anything else is on.
"""
import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sf6_source_preservation import IMPORT_OPTIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', type=Path, required=True,
                        help='A small retail SF6 mesh, for example esf032_001_02.mesh.230110883')
    parser.add_argument('--report-json', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    ops = importlib.import_module(repo.name+'.modules.mesh.re_mesh_operators')
    mesh_io = importlib.import_module(repo.name+'.modules.mesh.blender_re_mesh')
    source_mesh = importlib.import_module(repo.name+'.modules.mesh.sf6_source')
    folder_ui = importlib.import_module(repo.name+'.modules.mesh.sf6_mod_folder_operator')
    dialogs = importlib.import_module(repo.name+'.modules.mesh.sf6_unified_dialogs')
    preferences = bpy.context.preferences.addons[repo.name].preferences
    preferences.default_exportBlendShapes = True
    original = args.source_file
    report = []

    def passed(name):
        report.append(dict(test=name, passed=True))
        print('PASS '+name, flush=True)

    with tempfile.TemporaryDirectory(prefix='sf6-modes-') as temporary:
        root = Path(temporary)
        folder_ui.settings_path = lambda: root/'settings.json'
        mesh_io.importREMeshFile(str(original), IMPORT_OPTIONS.copy())
        collection_name = bpy.context.scene['REMeshLastImportedCollection']

        def collection():
            return bpy.data.collections[collection_name]

        batch_target = root/'batch.mesh.230110883'
        collection()['BatchExport_enabled'] = True
        collection()['BatchExport_path'] = str(batch_target)
        # Older versions shared this value with direct/folder exports. It cannot
        # prove that the user opted into preservation in the independent batch UI.
        collection()['BatchExport_exportBlendShapes'] = True

        def approve_batch(result):
            # Without a window, batch export only prepares its review. Approving
            # the ready plan is the scripted equivalent of the user's click.
            assert result == {'INTERFACE'}, result
            request = ops._LAST_BATCH_REVIEW
            assert request.state == 'ready', request.review
            route = request.review['files'][0]['result']['route']
            assert request.approve(request.revision), request.result
            assert batch_target.read_bytes()[:4] == b'MESH'
            return route != 'ordinary'

        def quick(expected):
            preserved = approve_batch(bpy.ops.re_mesh.quick_batch_export('EXEC_DEFAULT'))
            assert preserved is expected, preserved

        def item(path, mode):
            values = {p.identifier:p.default for p in ops.ExporterNodePropertyGroup.bl_rna.properties
                      if p.identifier != 'rna_type' and hasattr(p, 'default')}
            values.update(name=collection_name, path=str(path), exportType='MESH',
                          enabled=True, invalid=False, hasChild=False, exportBlendShapes=mode)
            return values

        def batch(mode):
            preserved = approve_batch(bpy.ops.re_mesh.batch_exporter(
                'EXEC_DEFAULT', itemList_items=[item(batch_target, mode)]))
            assert preserved is mode, preserved

        def folder(mode, selected=False):
            result = bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT',
                targetCollection=collection_name, parent_directory=str(root),
                folder_name='Variant', mod_name='Variant', exportBlendShapes=mode,
                selectedOnly=selected)
            outcome = json.loads(bpy.context.scene[folder_ui.SCENE_UNIFIED_RESULT])
            if result == {'INTERFACE'}:
                # A partial or rebuilt export may list losses to approve first.
                request = dialogs._REQUESTS[outcome['request_id']]
                assert request.state == 'ready', request.review
                assert request.approve(request.revision), request.result
                outcome = request.result
            else:
                assert result == {'FINISHED'}, result
            assert outcome['status'] == 'committed', outcome
            assert (outcome['route'] != 'ordinary') is mode, outcome['route']
            defaults = folder_ui.load_operator_defaults(bpy.context, collection())
            assert defaults['exportBlendShapes'] is mode
            assert defaults['selectedOnly'] is selected

        def menu(name):
            assert bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT', export_content='MENU',
                parent_directory=str(root), folder_name=name, mod_name=name) == {'FINISHED'}

        def folder_default(key):
            return folder_ui.load_operator_defaults(bpy.context, collection())[key]

        # A collection with no batch history starts with Preserve Source on, and an
        # untouched mesh then leaves exactly as it came in.
        quick(True)
        assert batch_target.read_bytes() == original.read_bytes()
        passed('new_batch_profile_preserves_and_exports_the_source_exactly')

        folder(False)
        quick(True)
        assert folder_default('exportBlendShapes') is False
        passed('folder_off_does_not_disable_batch_and_batch_keeps_folder_off')

        batch(False)
        folder(True)
        quick(False)
        assert folder_default('exportBlendShapes') is True
        passed('folder_on_does_not_enable_batch_and_batch_keeps_folder_on')

        menu('Menu')
        assert folder_default('exportBlendShapes') is True
        quick(False)
        passed('menu_only_export_keeps_both_mesh_export_modes')

        for mode in (False, True, False):
            folder(mode)
            quick(False)
        passed('repeated_folder_toggles_never_flip_batch_off_back_on')

        for obj in tuple(bpy.context.selected_objects):
            obj.select_set(False)
        chosen = next(o for o in collection().all_objects if o.type == 'MESH' and source_mesh.META in o
                      and json.loads(o[source_mesh.META])['lod'] == 0)
        chosen.select_set(True)
        folder(True, selected=True)
        variant_mesh = next((root/'Variant').rglob('*.mesh.230110883'))
        assert len(source_mesh.SourceMesh(variant_mesh.read_bytes()).parts) == 1
        passed('folder_forwards_and_remembers_selected_objects_only')

        menu('Selection Menu')
        assert folder_default('selectedOnly') is True
        assert folder_default('exportBlendShapes') is True
        passed('menu_only_export_keeps_selected_objects_preference')

        # Direct exports also do not replace the batch choice.
        direct = root/'direct.mesh.230110883'
        assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', targetCollection=collection_name,
            filepath=str(direct), exportBlendShapes=True) == {'FINISHED'}
        assert direct.read_bytes() == original.read_bytes()
        quick(False)
        passed('explicit_direct_export_does_not_change_batch')

        # A batch attempt that cannot be published must not commit the mode chosen for it.
        occupied = root/'occupied.mesh.230110883'
        occupied.mkdir()
        result = bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[item(occupied, True)])
        assert result == {'INTERFACE'}, result
        request = ops._LAST_BATCH_REVIEW
        # A folder sits where the file should go. Whether that is caught at review
        # or at publication, approval cannot publish.
        assert request.state in ('blocked', 'ready'), request.state
        assert not request.approve(request.revision)
        assert occupied.is_dir()
        request.cancel()
        quick(False)
        passed('failed_batch_does_not_replace_saved_mode')

        saved = root/'independent-modes.blend'
        assert bpy.ops.wm.save_as_mainfile(filepath=str(saved)) == {'FINISHED'}
        assert bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False, use_scripts=False) == {'FINISHED'}
        quick(False)
        assert folder_default('exportBlendShapes') is True
        assert folder_default('selectedOnly') is True
        passed('separate_modes_survive_save_reopen')

        # Scene defaults remain useful if the config file is unavailable.
        folder_ui.settings_path = lambda: root/'missing-settings.json'
        assert folder_default('exportBlendShapes') is True
        passed('folder_mode_has_independent_scene_fallback')

    if args.report_json:
        args.report_json.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('EXPORT_MODE_ISOLATION_PASSED '+str(len(report)),flush=True)


if __name__ == '__main__':
    main()
