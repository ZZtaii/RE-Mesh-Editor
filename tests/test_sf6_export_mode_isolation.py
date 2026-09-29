"""Exercise independent batch/folder preservation through real Blender operators."""
import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
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
    report, calls, selection_calls = [], [], []

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
            selection_calls.append(bool(options.get('selectedOnly')))
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

        def folder(mode, selected=False):
            calls.clear()
            assert bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT',
                targetCollection=collection.name, parent_directory=str(root),
                folder_name='Variant', mod_name='Variant', exportBlendShapes=mode,
                selectedOnly=selected) == {'FINISHED'}
            assert calls == [mode], calls
            assert selection_calls[-1:] == [selected], selection_calls
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

            folder(True, selected=True)
            assert folder_ui.load_operator_defaults(bpy.context, collection)['selectedOnly'] is True
            passed('folder_forwards_and_remembers_selected_objects_only')

            assert bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT', export_content='MENU',
                parent_directory=str(root), folder_name='Selection Menu',
                mod_name='Selection Menu') == {'FINISHED'}
            assert folder_ui.load_operator_defaults(bpy.context, collection)['selectedOnly'] is True
            passed('menu_only_export_keeps_selected_objects_preference')

            variant = root/'Variant'
            mesh = next(variant.rglob('*.mesh.230110883'))
            ini = variant/'modinfo.ini'
            before = (mesh.read_bytes(), ini.read_bytes())
            last_success = dict(collection=bpy.context.scene.get('REMeshLastExportedCollection'),
                                version=bpy.context.scene.get('REMeshLastExportedMeshVersion'),
                                batch_path=collection.get('BatchExport_path'),
                                mod_directory=bpy.context.scene.re_mdf_toolpanel.modDirectory)

            def assert_last_success_unchanged():
                assert bpy.context.scene.get('REMeshLastExportedCollection') == last_success['collection']
                assert bpy.context.scene.get('REMeshLastExportedMeshVersion') == last_success['version']
                assert collection.get('BatchExport_path') == last_success['batch_path']
                assert bpy.context.scene.re_mdf_toolpanel.modDirectory == last_success['mod_directory']

            def reject_selection(path, options):
                assert options['selectedOnly'] is True
                raise ValueError('No selected source mesh objects in the target collection')

            with patch.object(mesh_io, 'exportREMeshFile', reject_selection):
                try:
                    result = bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT',
                        targetCollection=collection.name, parent_directory=str(root),
                        folder_name='Variant', mod_name='Variant', mod_version='rejected',
                        exportBlendShapes=True, selectedOnly=True)
                except RuntimeError as error:
                    assert 'No selected source mesh objects' in str(error)
                else:
                    assert result == {'CANCELLED'}
            assert (mesh.read_bytes(), ini.read_bytes()) == before
            assert_last_success_unchanged()
            failed_defaults = folder_ui.load_operator_defaults(bpy.context, collection)
            assert failed_defaults['mod_version'] == 'rejected'
            assert failed_defaults['selectedOnly'] is True
            assert json.loads(bpy.context.scene['SF6ModFolderDefaults'])['mod_version'] == 'rejected'
            passed('failed_selected_folder_export_keeps_variant_and_remembers_fields')

            try:
                result = bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT', export_content='MENU',
                    parent_directory=str(root), folder_name='invalid/menu',
                    mod_name='Rejected Menu', mod_version='v47', mod_author='Menu Author')
            except RuntimeError as error:
                assert 'folder' in str(error).lower(), str(error)
            else:
                assert result == {'CANCELLED'}
            assert not (root/'invalid').exists()
            assert (mesh.read_bytes(), ini.read_bytes()) == before
            assert_last_success_unchanged()
            failed_menu = folder_ui.load_operator_defaults(bpy.context, collection)
            for key, value in dict(export_content='MENU', folder_name='invalid/menu',
                                   mod_name='Rejected Menu', mod_version='v47',
                                   mod_author='Menu Author').items():
                assert failed_menu[key] == value, key
            assert failed_menu['selectedOnly'] is True
            assert failed_menu['exportBlendShapes'] is True
            passed('failed_menu_export_remembers_fields_and_mesh_preferences')

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

            # A manually chosen collection and rotation toggle must survive a
            # rejected export even if a different valid collection owns the
            # active object when the dialog opens again.
            other_collection = bpy.data.collections.new('esf033_002_01.mesh')
            bpy.context.scene.collection.children.link(other_collection)
            other_collection['SF6SourceFilename'] = 'esf033_002_01.mesh.230110883'
            other_mesh = bpy.data.meshes.new('other active mesh')
            other_object = bpy.data.objects.new('other active object', other_mesh)
            other_collection.objects.link(other_object)
            collection['SF6SourceRotate'] = True
            dialog_context = SimpleNamespace(
                scene=bpy.context.scene, active_object=other_object,
                window_manager=SimpleNamespace(
                    invoke_props_dialog=lambda *args, **kwargs: {'RUNNING_MODAL'}))
            assert folder_ui.choose_collection(dialog_context) == other_collection

            def reject_hybrid(**extra):
                settings = dict(targetCollection=collection.name, parent_directory=str(root),
                                folder_name='Variant', mod_name='Variant', mod_version='failed controls',
                                exportBlendShapes=False, sf6HybridPreserve=True, rotate90=False)
                settings.update(extra)
                try:
                    result = bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT', **settings)
                except RuntimeError as error:
                    assert 'requires Preserve Source Data' in str(error), str(error)
                else:
                    assert result == {'CANCELLED'}

            def reopened_dialog():
                operator = SimpleNamespace(**{name: '' for name in folder_ui.package.DEFAULT_FIELDS})
                assert folder_ui.ExportSF6ModFolder.invoke(operator, dialog_context, None) == {'RUNNING_MODAL'}
                return operator

            folder_ui.settings_path = lambda: root/'failed-controls.json'
            reject_hybrid()
            remembered = folder_ui.package.load_defaults(folder_ui.settings_path())
            assert remembered['targetCollection'] == collection.name
            assert remembered['rotate90'] is False
            assert remembered['mod_version'] == 'failed controls'
            reopened = reopened_dialog()
            assert reopened.targetCollection == collection.name
            assert reopened.rotate90 is False
            assert (mesh.read_bytes(), ini.read_bytes()) == before
            passed('failed_export_reopens_with_chosen_collection_and_rotation')

            # When the config file cannot be written, the scene copy still
            # fills the next dialog with the rejected attempt's fields.
            old_config = folder_ui.settings_path().read_bytes()
            with patch.object(folder_ui.package, 'save_defaults', side_effect=OSError('simulated config lock')):
                reject_hybrid(mod_version='scene fallback', mod_author='Failed Author')
            assert folder_ui.settings_path().read_bytes() == old_config
            scene_defaults = folder_ui.load_operator_defaults(bpy.context, collection)
            assert scene_defaults['targetCollection'] == collection.name
            assert scene_defaults['rotate90'] is False
            assert scene_defaults['mod_version'] == 'scene fallback'
            assert scene_defaults['mod_author'] == 'Failed Author'
            reopened = reopened_dialog()
            assert reopened.targetCollection == collection.name
            assert reopened.rotate90 is False
            assert reopened.mod_version == 'scene fallback'
            assert (mesh.read_bytes(), ini.read_bytes()) == before
            passed('failed_export_uses_scene_defaults_when_config_write_fails')

    if args.report_json:
        args.report_json.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('EXPORT_MODE_ISOLATION_PASSED '+str(len(report)),flush=True)


if __name__ == '__main__':
    main()
