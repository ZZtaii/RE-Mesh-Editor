"""Run in isolated Blender with --source-dir pointing to original SF6 meshes."""
import argparse
import importlib
import importlib.util
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
    parser.add_argument('--source-dir', required=True, type=Path)
    parser.add_argument('--report-json', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    ui = importlib.import_module(repo.name + '.modules.mesh.sf6_mod_folder_operator')
    mesh_io = importlib.import_module(repo.name + '.modules.mesh.blender_re_mesh')
    spec = importlib.util.spec_from_file_location('source_test_options', repo/'tests/test_sf6_source_preservation.py')
    source_tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source_tests)
    report = []

    def passed(name):
        report.append(dict(test=name, passed=True))
        print('PASS ' + name, flush=True)

    assert repo.name + '.addon_updater_ops' not in sys.modules
    assert repo.name + '.addon_updater' not in sys.modules
    assert not any(name.startswith('updater_') for name in dir(bpy.ops.re_mesh_editor))
    passed('upstream_updater_not_loaded_or_registered')

    with tempfile.TemporaryDirectory(prefix='sf6-notice-') as temporary:
        root = Path(temporary)
        ui.settings_path = lambda: root/'config/defaults.json'
        preferences = bpy.context.preferences.addons[repo.name].preferences
        preferences.textureCachePath = str(root/'textures')
        preferences.showConsole = True
        # A stored legacy preference cannot activate an updater that isn't loaded.
        preferences.auto_check_update = True
        old_asset = ui.package.SF6Asset('032', '001', '02')
        existing = root/'Combined'/old_asset.relative_path
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b'EXISTING INGRID HAIR')
        original = args.source_dir/'esf033_002_01.mesh.230110883'
        mesh_io.importREMeshFile(str(original), source_tests.IMPORT_OPTIONS.copy())
        collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        defaults = dict(parent_directory=str(root), folder_name='Combined', mod_name='Chosen display name',
                        mod_author='Test Author', mod_version='v2', mod_category='!Characters > Multiple',
                        bundle_name='Chosen bundle', parent_mod_name='', export_content='MESH')
        ui.package.save_defaults(ui.settings_path(), defaults)
        values = {key: '' for key in ui.package.DEFAULT_FIELDS}
        values.update(defaults, targetCollection=collection.name, destination_notice='')
        operator = SimpleNamespace(**values)
        context = SimpleNamespace(scene=bpy.context.scene, active_object=None,
            window_manager=SimpleNamespace(invoke_props_dialog=lambda *a, **k: {'RUNNING_MODAL'}))
        assert ui.ExportSF6ModFolder.invoke(operator, context, None) == {'RUNNING_MODAL'}
        assert 'Ingrid C1' in operator.destination_notice and 'Yasmine C2' in operator.destination_notice
        for key, value in defaults.items():
            assert getattr(operator, key) == value, key
        passed('conflict_notice_preserves_remembered_fields')

        same = bpy.data.collections.new('esf032_001_01.mesh')
        operator.targetCollection = same.name
        ui.update_collection(operator, context)
        assert operator.destination_notice == ''
        operator.targetCollection = collection.name
        ui.update_collection(operator, context)
        assert 'Ingrid C1' in operator.destination_notice
        operator.folder_name = 'New variant'
        ui.update_destination_notice(operator, context)
        assert operator.destination_notice == ''
        operator.folder_name = 'Combined'
        ui.update_destination_notice(operator, context)
        assert 'Ingrid C1' in operator.destination_notice
        passed('notice_updates_for_collection_and_folder_changes')

        operator.export_content = 'MENU'
        with patch.object(ui.package, 'destination_conflicts', side_effect=AssertionError('menu scanned')):
            ui.update_destination_notice(operator, context)
        assert operator.destination_notice == ''
        operator.export_content = 'MESH'
        with patch.object(ui.package, 'destination_conflicts', side_effect=PermissionError('locked')):
            ui.update_destination_notice(operator, context)
        assert 'could not be checked' in operator.destination_notice
        assert operator.folder_name == 'Combined'
        passed('menu_and_unreadable_folder_notice_handling')

        # The notice is informational: a real preserved export still succeeds.
        settings = dict(defaults, targetCollection=collection.name, exportBlendShapes=True, rotate90=True)
        assert bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT', **settings) == {'FINISHED'}
        asset = ui.package.asset_from_collection(collection)
        assert (root/'Combined'/asset.relative_path).read_bytes() == original.read_bytes()
        assert existing.read_bytes() == b'EXISTING INGRID HAIR'
        assert ui.package.read_modinfo(root/'Combined/modinfo.ini')['name'] == defaults['mod_name']
        saved = ui.package.load_defaults(ui.settings_path())
        assert 'destination_notice' not in saved
        for key, value in defaults.items():
            assert saved[key] == value, key
        passed('mixed_asset_export_allowed_and_existing_payload_preserved')

    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('DESTINATION_NOTICE_TESTS_PASSED ' + str(len(report)), flush=True)


if __name__ == '__main__':
    main()
