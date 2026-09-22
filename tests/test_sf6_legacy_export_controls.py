"""Run in isolated Blender; verify legacy callers through the real export operator."""
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
    collection = bpy.data.collections.new('legacy.mesh')
    bpy.context.scene.collection.children.link(collection)
    collection['~TYPE'] = 'RE_MESH_COLLECTION'
    report = []
    calls = []

    def capture(path, options):
        calls.append(options.copy())
        return True

    def passed(name):
        report.append(dict(test=name, passed=True))
        print('PASS ' + name, flush=True)

    with tempfile.TemporaryDirectory(prefix='sf6-legacy-') as temporary:
        root = Path(temporary)
        target = root/'legacy.mesh.230110883'
        kwargs = dict(filepath=str(target), targetCollection=collection.name)
        with patch.object(addon, 'exportREMeshFile', capture):
            collection['BatchExport_exportBlendShapes'] = False
            assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **kwargs) == {'FINISHED'}
            assert calls[-1]['exportBlendShapes'] is False
            assert collection['BatchExport_exportBlendShapes'] == False
            passed('omitted_option_uses_saved_false')

            collection['BatchExport_exportBlendShapes'] = True
            assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **kwargs) == {'FINISHED'}
            assert calls[-1]['exportBlendShapes'] is True
            passed('omitted_option_uses_saved_true')

            collection['BatchExport_exportBlendShapes'] = False
            assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', exportBlendShapes=True, **kwargs) == {'FINISHED'}
            assert calls[-1]['exportBlendShapes'] is True
            assert collection['BatchExport_exportBlendShapes'] == True
            passed('explicit_true_overrides_saved_false')

            collection['BatchExport_exportBlendShapes'] = True
            assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', exportBlendShapes=False, **kwargs) == {'FINISHED'}
            assert calls[-1]['exportBlendShapes'] is False
            assert collection['BatchExport_exportBlendShapes'] == False
            passed('explicit_false_overrides_saved_true')

            del collection['BatchExport_exportBlendShapes']
            assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **kwargs) == {'FINISHED'}
            assert calls[-1]['exportBlendShapes'] is True
            passed('absent_saved_choice_keeps_preservation_default')

            collection['BatchExport_exportBlendShapes'] = ''
            assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **kwargs) == {'FINISHED'}
            assert calls[-1]['exportBlendShapes'] is True
            passed('invalid_saved_choice_does_not_disable_preservation')

            collection['BatchExport_exportBlendShapes'] = False
            other = dict(kwargs, filepath=str(root/'other.mesh.1808312334'))
            assert bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', **other) == {'FINISHED'}
            assert calls[-1]['exportBlendShapes'] is True
            passed('non_sf6_export_keeps_existing_default')

        # An explicit request to preserve must still fail on missing provenance,
        # even when the collection's earlier saved choice is ordinary export.
        collection['BatchExport_exportBlendShapes'] = False
        collection['BatchExport_path'] = 'previous-success'
        target.write_bytes(b'KEEP')
        try:
            result = bpy.ops.re_mesh.exportfile('EXEC_DEFAULT', exportBlendShapes=True, **kwargs)
        except RuntimeError as error:
            assert 'requires an original mesh' in str(error), str(error)
        else:
            assert result == {'CANCELLED'}
        assert target.read_bytes() == b'KEEP'
        assert collection['BatchExport_exportBlendShapes'] == False
        assert collection['BatchExport_path'] == 'previous-success'
        passed('explicit_preservation_failure_keeps_destination_and_saved_choice')

    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('LEGACY_EXPORT_CONTROLS_PASSED ' + str(len(report)), flush=True)


if __name__ == '__main__':
    main()
