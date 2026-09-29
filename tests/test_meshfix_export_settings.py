"""Asset-free operator checks for mesh-fix controls and per-game shape export.

Run in Blender --background --factory-startup --python-exit-code 1. The actual
registered export operator is exercised with a controlled export helper; no
game assets, installed preferences, or mesh destination files are required.
"""
import importlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import addon_utils
import bpy


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    addon = importlib.import_module(repo.name)
    preferences = bpy.context.preferences.addons[repo.name].preferences
    preferences.showConsole = False
    collection = bpy.data.collections.new('ExportSettings.mesh')
    collection['~TYPE'] = 'RE_MESH_COLLECTION'
    bpy.context.scene.collection.children.link(collection)
    controls = dict(selectedOnly=True, exportAllLODs=False, rotate90=False,
                    autoSolveRepeatedUVs=False, preserveSharpEdges=False,
                    splitLoopVertices=False, limitTotal=True, limitTotalCount=4,
                    normalizeWeights=False, useBlenderMaterialName=True,
                    preserveBoneMatrices=True, exportBoundingBoxes=True)
    capture = []
    hybrid_reports = []

    def exported(path, options):
        capture.append((path, options.copy()))
        return True

    def hybrid_report(operator, report):
        hybrid_reports.append(report)

    def cancelled(call, message):
        try:
            result = call()
        except RuntimeError as error:
            assert message in str(error), str(error)
        else:
            assert result == {'CANCELLED'}, result

    def saved_fields():
        return {name: collection[name] for name in collection.keys()
                if name.startswith('BatchExport_')}

    with tempfile.TemporaryDirectory(prefix='meshfix-export-settings-') as temporary:
        root = Path(temporary)

        def settings(version, **updates):
            return dict(controls, filepath=str(root / ('output.mesh.' + str(version))),
                        filename_ext='.' + str(version), targetCollection=collection.name,
                        **updates)

        with patch.object(addon, 'exportREMeshFile', exported), \
                patch.object(addon, 'report_hybrid_export', hybrid_report), \
                patch.object(addon, 'setModDirectoryFromFilePath'):
            assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **settings(
                230110883, exportBlendShapes=False, sf6HybridPreserve=False)) == {'FINISHED'}
            options = capture[-1][1]
            assert all(options[name] == value for name, value in controls.items()), options
            assert options['exportBlendShapes'] is False and options['sf6HybridPreserve'] is False
            assert not hybrid_reports
            assert all(collection['BatchExport_' + name] == controls[name]
                       for name in ('splitLoopVertices', 'limitTotal', 'limitTotalCount', 'normalizeWeights'))
            print('PASS direct_operator_forwards_and_saves_meshfix_controls', flush=True)

            assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **settings(
                230110883, exportBlendShapes=True, sf6HybridPreserve=True)) == {'FINISHED'}
            assert capture[-1][1]['exportBlendShapes'] is True
            assert capture[-1][1]['sf6HybridPreserve'] is True
            assert len(hybrid_reports) == 1
            print('PASS sf6_hybrid_options_and_reporting_remain_enabled', flush=True)

            for mode in ('NO', 'MODE0', 'MODE1'):
                for stale_preservation in (False, True):
                    assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **settings(
                        241111606, exportBlendShapes=stale_preservation,
                        sf6HybridPreserve=True, shapeKeyExportMode=mode)) == {'FINISHED'}
                    options = capture[-1][1]
                    assert options['sf6HybridPreserve'] is False
                    assert options['exportBlendShapes'] is (mode != 'NO')
                    if mode != 'NO':
                        assert options['blendShapeExportMode'] == (0 if mode == 'MODE0' else 1)
                    assert all(options[name] == value for name, value in controls.items())
                    assert len(hybrid_reports) == 1, 'Wilds used SF6 hybrid success reporting'
            print('PASS wilds_modes_override_sf6_bool_and_ignore_stale_hybrid_state', flush=True)

            for version in (221108797, 260421070, 260209350):
                assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **settings(
                    version, exportBlendShapes=True, sf6HybridPreserve=True,
                    shapeKeyExportMode='MODE1')) == {'FINISHED'}
                options = capture[-1][1]
                assert options['exportBlendShapes'] is True
                assert options['sf6HybridPreserve'] is False
                assert 'blendShapeExportMode' not in options
                assert len(hybrid_reports) == 1, 'Other game used SF6 hybrid success reporting'
            print('PASS other_games_preserve_legacy_blend_bool_and_ignore_stale_hybrid', flush=True)

            for version, expected in ((230110883, 6), (241111606, 12), (260421070, 16)):
                values = settings(version, exportBlendShapes=False, sf6HybridPreserve=False)
                del values['filename_ext']
                del values['limitTotalCount']
                assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **values) == {'FINISHED'}
                assert capture[-1][1]['limitTotalCount'] == expected
            print('PASS programmatic_default_weight_limit_uses_actual_destination_format', flush=True)

            calls_before = len(capture)
            cancelled(lambda: bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **settings(
                230110883, exportBlendShapes=False, sf6HybridPreserve=True)),
                'requires Preserve Source Data')
            assert len(capture) == calls_before
            print('PASS sf6_hybrid_without_preservation_fails_before_export', flush=True)

        successful_fields = saved_fields()
        failed_settings = settings(241111606, exportBlendShapes=True,
                                   sf6HybridPreserve=True, shapeKeyExportMode='NO')
        failed_settings.update(splitLoopVertices=True, normalizeWeights=True,
                               limitTotal=False, limitTotalCount=12)
        with patch.object(addon, 'exportREMeshFile', return_value=False), \
                patch.object(addon, 'report_hybrid_export', hybrid_report):
            cancelled(lambda: bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **failed_settings),
                      'RE Mesh export failed')
        assert saved_fields() == successful_fields
        assert len(hybrid_reports) == 1
        with patch.object(addon, 'exportREMeshFile', side_effect=ValueError('Controlled export failure')):
            cancelled(lambda: bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **failed_settings),
                      'Controlled export failure')
        assert saved_fields() == successful_fields
        print('PASS false_and_exception_failures_keep_last_success_settings', flush=True)

    for extension, expected in (('.230110883', 6), ('.241111606', 12),
                                ('.260421070', 16), ('.260209350', 12),
                                ('.1808282334', 8), ('.250925211', 8)):
        values = SimpleNamespace(filename_ext=extension, limitTotalCount=1)
        addon.updateMeshExportWeightLimit(values, bpy.context)
        assert values.limitTotalCount == expected, (extension, values.limitTotalCount)
    print('PASS version_weight_limit_callback_uses_correct_game_and_banks', flush=True)
    assert addon.getMeshExportDefaultWeightLimit('invalid') == 8
    print('MESH_EXPORT_SETTINGS_TESTS_PASSED 8', flush=True)


if __name__ == '__main__':
    main()
