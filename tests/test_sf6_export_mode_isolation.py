"""Exercise independent batch/folder preservation through real Blender operators."""
import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import addon_utils
import bpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-json', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    addon = importlib.import_module(repo.name)
    ops = importlib.import_module(repo.name+'.modules.mesh.re_mesh_operators')
    mesh_io = importlib.import_module(repo.name+'.modules.mesh.blender_re_mesh')
    folder_ui = importlib.import_module(repo.name+'.modules.mesh.sf6_mod_folder_operator')
    preferences = bpy.context.preferences.addons[repo.name].preferences
    preferences.default_exportBlendShapes = True
    collection = bpy.data.collections.new('esf032_001_02.mesh')
    bpy.context.scene.collection.children.link(collection)
    collection['~TYPE'] = 'RE_MESH_COLLECTION'
    collection['SF6PreserveSource'] = True
    collection['BatchExport_enabled'] = True
    # Older versions shared this value with direct/folder exports. It cannot
    # prove that the user opted into preservation in the independent batch UI.
    collection['BatchExport_exportBlendShapes'] = True
    report, calls = [], []

    def passed(name):
        report.append(dict(test=name, passed=True))
        print('PASS '+name, flush=True)

    with tempfile.TemporaryDirectory(prefix='sf6-modes-') as temporary:
        root = Path(temporary)
        folder_ui.settings_path = lambda: root/'settings.json'
        collection['BatchExport_path'] = str(root/'batch.mesh.230110883')

        def export(path, options):
            assert Path(path).resolve().is_relative_to(root.resolve())
            calls.append(bool(options['exportBlendShapes']))
            Path(path).write_bytes(b'test payload')
            return True

        def quick(expected):
            calls.clear()
            assert bpy.ops.re_mesh.quick_batch_export('EXEC_DEFAULT') == {'FINISHED'}
            assert calls == [expected], calls

        def item(path, mode):
            values = {p.identifier:p.default for p in ops.ExporterNodePropertyGroup.bl_rna.properties
                      if p.identifier != 'rna_type' and hasattr(p, 'default')}
            values.update(name=collection.name, path=str(path), exportType='MESH',
                          enabled=True, invalid=False, hasChild=False, exportBlendShapes=mode)
            return values

        def batch(mode):
            values = item(root/'batch.mesh.230110883', mode)
            calls.clear()
            assert bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[values]) == {'FINISHED'}
            assert calls == [mode], calls

        def folder(mode):
            calls.clear()
            assert bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT',
                targetCollection=collection.name, parent_directory=str(root),
                folder_name='Variant', mod_name='Variant', exportBlendShapes=mode) == {'FINISHED'}
            assert calls == [mode], calls
            assert folder_ui.load_operator_defaults(bpy.context, collection)['exportBlendShapes'] is mode

        with patch.object(addon, 'exportREMeshFile', export), patch.object(mesh_io, 'exportREMeshFile', export):
            quick(False)
            assert ops.ExporterNodePropertyGroup.bl_rna.properties['exportBlendShapes'].default is False
            passed('batch_defaults_off_despite_global_and_old_shared_on')

            folder(True)
            quick(False)
            assert folder_ui.load_operator_defaults(bpy.context, collection)['exportBlendShapes'] is True
            passed('folder_on_does_not_enable_batch_and_batch_keeps_folder_on')

            batch(True)
            folder(False)
            quick(True)
            assert folder_ui.load_operator_defaults(bpy.context, collection)['exportBlendShapes'] is False
            passed('folder_off_does_not_disable_batch_and_batch_keeps_folder_off')

            assert bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT', export_content='MENU',
                parent_directory=str(root), folder_name='Menu', mod_name='Menu') == {'FINISHED'}
            assert folder_ui.load_operator_defaults(bpy.context, collection)['exportBlendShapes'] is False
            quick(True)
            passed('menu_only_export_keeps_both_mesh_export_modes')

            batch(False)
            for mode in (True, False, True):
                folder(mode)
                quick(False)
            passed('repeated_folder_toggles_never_flip_batch_off_back_on')

            # Direct dialog/API exports also do not replace the batch choice.
            calls.clear()
            assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', targetCollection=collection.name,
                filepath=str(root/'direct.mesh.230110883'), exportBlendShapes=True) == {'FINISHED'}
            assert calls == [True]
            quick(False)
            passed('explicit_direct_export_does_not_change_batch')

            # Failure must not commit the batch option selected for that attempt.
            values = item(root/'failed.mesh.230110883', True)
            with patch.object(addon, 'exportREMeshFile', side_effect=ValueError('Rejected test export')):
                try:
                    result = bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[values])
                except RuntimeError as error:
                    assert 'Rejected test export' in str(error), str(error)
                else:
                    assert result == {'CANCELLED'}
            quick(False)
            passed('failed_batch_does_not_replace_saved_mode')

            saved = root/'independent-modes.blend'
            assert bpy.ops.wm.save_as_mainfile(filepath=str(saved)) == {'FINISHED'}
            assert bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False, use_scripts=False) == {'FINISHED'}
            collection = bpy.data.collections['esf032_001_02.mesh']
            quick(False)
            assert folder_ui.load_operator_defaults(bpy.context, collection)['exportBlendShapes'] is True
            passed('separate_modes_survive_save_reopen')

            # Scene defaults remain useful if the config file is unavailable.
            folder_ui.settings_path = lambda: root/'missing-settings.json'
            assert folder_ui.load_operator_defaults(bpy.context, collection)['exportBlendShapes'] is True
            passed('folder_mode_has_independent_scene_fallback')

    if args.report_json:
        args.report_json.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('EXPORT_MODE_ISOLATION_PASSED '+str(len(report)),flush=True)


if __name__ == '__main__':
    main()
