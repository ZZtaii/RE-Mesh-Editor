"""Preserve-source mode must accept no-shape retail meshes without relaxing safety.

Blender --background --factory-startup --python-exit-code 1 --python this.py
-- --source-file local_face.mesh.230110883 --report-json results.json
"""
import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import addon_utils
import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sf6_hybrid_export import IMPORT_OPTIONS
from test_sf6_hybrid_modifiers import scene_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', type=Path, required=True)
    parser.add_argument('--report-json', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    io = importlib.import_module(repo.name + '.modules.mesh.blender_re_mesh')
    sf6 = importlib.import_module(repo.name + '.modules.mesh.sf6_source')
    raw = args.source_file.read_bytes()
    source = sf6.SourceMesh(raw)
    assert not source.shapes[0], 'Supply a no-shape face source'
    io.importREMeshFile(str(args.source_file), IMPORT_OPTIONS.copy())
    col = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
    opts = dict(targetCollection=col.name, exportBlendShapes=True, sf6HybridPreserve=False,
                selectedOnly=False, rotate90=True)
    before = scene_state(col)
    counts = lambda: tuple(len(getattr(bpy.data, k)) for k in
                           ('objects', 'meshes', 'collections', 'armatures', 'shape_keys'))
    count_before = counts()
    results = []
    def passed(name):
        results.append(dict(test=name, passed=True))
        print('PASS', name, flush=True)
    with tempfile.TemporaryDirectory(prefix='sf6-no-shapes-source-') as temp:
        path = Path(temp) / args.source_file.name
        assert io.exportREMeshFile(str(path), opts.copy())
        assert path.read_bytes() == raw and scene_state(col) == before
        passed('unmodified_no_shape_source_byte_identical')
        obj = min((o for o in col.all_objects if o.type == 'MESH' and sf6.META in o),
                  key=lambda o: len(o.data.vertices))
        assert obj.data.shape_keys is None
        part = json.loads(obj[sf6.META])
        vertex_id = obj.data.attributes[sf6.VERTEX_ID].data[0].value
        original = obj.data.vertices[0].co.copy()
        obj.data.vertices[0].co.x += .001
        obj.data.update()
        edited_state = scene_state(col)
        expected = bytearray(raw)
        offset = source.elements[0][1] + (part['start'] + vertex_id) * 12
        coordinate = np.asarray([obj.data.vertices[0].co[:]], np.float32)
        expected[offset:offset+12] = sf6._from_blender(coordinate, True).astype('<f4').tobytes()
        assert io.exportREMeshFile(str(path), opts.copy())
        assert path.read_bytes() == bytes(expected), 'Source edit changed non-position bytes'
        assert scene_state(col) == edited_state and counts() == count_before
        passed('no_shape_sculpt_changes_only_expected_position_bytes')
        selected_before = list(bpy.context.selected_objects)
        for selected in tuple(bpy.context.selected_objects):
            selected.select_set(False)
        obj.select_set(True)
        assert io.exportREMeshFile(str(path), dict(opts, selectedOnly=True, exportAllLODs=False))
        subset = sf6.SourceMesh(path.read_bytes())
        assert len(subset.parts) == 1 and not subset.shapes[0]
        assert subset.materials[subset.parts[0]['material']] == source.materials[part['material']]
        assert np.array_equal(subset.positions(subset.parts[0])[vertex_id],
                              sf6._from_blender(coordinate, True)[0])
        obj.select_set(False)
        for selected in selected_before:
            selected.select_set(True)
        assert scene_state(col) == edited_state and counts() == count_before
        passed('no_shape_selected_source_preserves_sculpt')
        sentinel = b'keep existing file'
        path.write_bytes(sentinel)
        obj.data.vertices[0].co.x = float('nan')
        try:
            io.exportREMeshFile(str(path), opts.copy())
        except ValueError as error:
            assert 'Non-finite vertex positions' in str(error)
        else:
            raise AssertionError('Invalid position accepted')
        assert path.read_bytes() == sentinel
        obj.data.vertices[0].co = original
        obj.data.update()
        assert scene_state(col) == before and counts() == count_before
        passed('no_shape_invalid_coordinates_preserve_destination')
        with patch.object(sf6.os, 'replace', side_effect=OSError('injected atomic replace failure')):
            try:
                io.exportREMeshFile(str(path), opts.copy())
            except OSError as error:
                assert 'injected atomic replace failure' in str(error)
            else:
                raise AssertionError('Replace failure swallowed')
        assert path.read_bytes() == sentinel
        assert not list(Path(temp).glob('.sf6-*'))
        assert scene_state(col) == before and counts() == count_before
        passed('no_shape_write_failure_is_atomic_and_cleans_up')
        path.write_bytes(bytes(expected))
        io.importREMeshFile(str(path), IMPORT_OPTIONS.copy())
        imported = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        assert io.exportREMeshFile(str(path), dict(opts, targetCollection=imported.name))
        assert path.read_bytes() == bytes(expected)
        passed('edited_no_shape_source_reimport_is_byte_identical')
    if args.report_json:
        args.report_json.write_text(json.dumps(results, indent=2)+'\n')
    print('NO_SHAPE_SOURCE_TESTS_PASSED', len(results), flush=True)


if __name__ == '__main__':
    main()
