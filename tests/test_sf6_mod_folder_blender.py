"""Run in background Blender with --source-dir containing original SF6 meshes."""
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
    parser.add_argument('--source-dir', required=True, type=Path)
    parser.add_argument('--report-json', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + '.modules.mesh.blender_re_mesh')
    ui = importlib.import_module(repo.name + '.modules.mesh.sf6_mod_folder_operator')
    package = ui.package
    spec = importlib.util.spec_from_file_location('source_tests', repo / 'tests/test_sf6_source_preservation.py')
    source_tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source_tests)
    report = []

    with tempfile.TemporaryDirectory(prefix='sf6-mod-folder-tests-') as temporary:
        root = Path(temporary)
        ui.settings_path = lambda: root / 'config/defaults.json'
        preview = root / 'preview.png'
        preview.write_bytes(b'preview fixture')
        for costume in (1, 2):
            filename = f'esf033_00{costume}_01.mesh.230110883'
            original = args.source_dir / filename
            mesh_io.importREMeshFile(str(original), source_tests.IMPORT_OPTIONS.copy())
            collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
            assert collection['SF6SourceFilename'] == filename
            if costume == 1:
                collection.name = 'Renamed SF6 collection'
            else:
                del collection['SF6SourceFilename']
                collection.name = 'esf033_002_01.mesh.003'
            asset = package.asset_from_collection(collection)
            folder = f'Variant C{costume}'
            settings = dict(targetCollection=collection.name, parent_directory=str(root),
                            folder_name=folder, mod_name=f'Display C{costume}', mod_version='v2',
                            mod_author='Test Author', mod_category=asset.category,
                            mod_description='Folder export integration', preview_path=str(preview),
                            exportBlendShapes=True, rotate90=True)
            result = bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT', **settings)
            assert result == {'FINISHED'}
            output = root / folder / asset.relative_path
            assert output.read_bytes() == original.read_bytes()
            info = package.read_modinfo(root / folder / 'modinfo.ini')
            assert info['name'] == settings['mod_name']
            assert info['category'] == '!Characters > Yasmine'
            assert info['author'] == 'Test Author'
            assert (root / folder / info['screenshot']).read_bytes() == preview.read_bytes()
            assert collection['BatchExport_path'] == str(output)
            report.append(dict(test='real_folder_export', costume=costume, byte_identical=True,
                               renamed_collection=costume == 1, legacy_metadata=costume == 2))

            defaults = ui.load_operator_defaults(bpy.context, collection)
            for name in package.DEFAULT_FIELDS:
                if name in settings:
                    assert defaults[name] == settings[name]
            # Exercise the actual dialog initialization without opening a window
            # in a background test process.
            properties = {name: '' for name in package.DEFAULT_FIELDS}
            properties.update(targetCollection=collection.name, rotate90=True)
            next_export = SimpleNamespace(**properties)
            context = SimpleNamespace(scene=bpy.context.scene, active_object=None,
                                      window_manager=SimpleNamespace(invoke_props_dialog=lambda *a, **k: {'RUNNING_MODAL'}))
            assert ui.ExportSF6ModFolder.invoke(next_export, context, None) == {'RUNNING_MODAL'}
            assert next_export.mod_author == 'Test Author'
            assert next_export.folder_name == folder
            assert next_export.preview_path == str(preview)
            assert next_export.mod_version == 'v2'
            report.append(dict(test='next_dialog_prefilled', costume=costume, passed=True))

        active_mesh = next(o for o in collection.all_objects if o.type == 'MESH')
        source_flag = collection.get('SF6PreserveSource')
        collection['SF6PreserveSource'] = False
        assert ui.choose_collection(SimpleNamespace(active_object=active_mesh, scene=bpy.context.scene)) == collection
        collection['SF6PreserveSource'] = source_flag
        report.append(dict(test='active_legacy_collection_selected', passed=True))

        ingrid = bpy.data.collections.new('esf032_001_02.mesh')
        next_export.targetCollection = ingrid.name
        ui.update_collection(next_export, context)
        assert next_export.mod_category == '!Characters > Ingrid'
        next_export.mod_category = '!My custom category'
        next_export.targetCollection = collection.name
        ui.update_collection(next_export, context)
        assert next_export.mod_category == '!My custom category'
        bpy.data.collections.remove(ingrid)
        report.append(dict(test='category_tracks_character_and_keeps_custom_value', passed=True))

        # A failed export must leave the previously exported mod and defaults intact.
        before_defaults = ui.settings_path().read_bytes()
        before_mesh = output.read_bytes()
        before_info = (root / folder / 'modinfo.ini').read_bytes()
        shaped = next(o for o in collection.all_objects if o.type == 'MESH' and o.data.shape_keys)
        keys = shaped.data.shape_keys.key_blocks
        old_name = keys[1].name
        keys[1].name = 'Unsupported renamed shape'
        try:
            bpy.ops.re_mesh.export_sf6_mod_folder('EXEC_DEFAULT', **dict(settings, mod_version='bad'))
        except RuntimeError as error:
            assert 'Shape keys' in str(error)
        else:
            raise AssertionError('Invalid export was accepted')
        keys[1].name = old_name
        assert output.read_bytes() == before_mesh
        assert (root / folder / 'modinfo.ini').read_bytes() == before_info
        assert ui.settings_path().read_bytes() == before_defaults
        report.append(dict(test='failed_export_keeps_mod_and_defaults', passed=True))

        # Direct export retains the previous behavior and produces no mod metadata.
        direct = root / filename
        mesh_io.exportREMeshFile(str(direct), dict(targetCollection=collection.name,
                                                  exportBlendShapes=True, rotate90=True))
        assert direct.read_bytes() == original.read_bytes()
        assert not (root / 'modinfo.ini').exists()
        report.append(dict(test='direct_export_unchanged', byte_identical=True))
    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('FOLDER_EXPORT_TESTS_PASSED ' + json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
