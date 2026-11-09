"""Retail regression for moved copies: safe supported exports and atomic refusals.
Run Blender --background --factory-startup --python-exit-code 1 --python this.py
-- --source-file shaped.mesh.230110883 [--report-json result.json].
The proprietary fixture is supplied locally, not distributed with the addon.
"""
import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
import addon_utils
import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sf6_hybrid_export import IMPORT_OPTIONS, lod0_parts, key
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
    source = sf6.SourceMesh(args.source_file.read_bytes())
    io.importREMeshFile(str(args.source_file), IMPORT_OPTIONS.copy())
    col = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
    meshes = [o for o in col.all_objects if o.type == 'MESH' and sf6.META in o]
    shaped = min((o for o in meshes if list(source.part_shapes(json.loads(o[sf6.META])))),
                 key=lambda o: len(o.data.vertices))
    plain = next((o for o in meshes if not list(source.part_shapes(json.loads(o[sf6.META])))), None)
    options = dict(targetCollection=col.name, rotate90=True, selectedOnly=False,
                   exportAllLODs=False, splitLoopVertices=False,
                   useBlenderMaterialName=False, preserveBoneMatrices=True,
                   exportBoundingBoxes=False, autoSolveRepeatedUVs=False,
                   preserveSharpEdges=False, exportBlendShapes=True, sf6HybridPreserve=True)
    counts = lambda: tuple(len(getattr(bpy.data, n)) for n in
                           ('objects', 'meshes', 'collections', 'shape_keys', 'armatures'))
    results = []
    def passed(name):
        results.append(dict(test=name, passed=True)); print('PASS', name, flush=True)
    def duplicate(obj, mode, linked=False):
        copy = obj.copy(); copy.data = obj.data if linked else obj.data.copy()
        obj.users_collection[0].objects.link(copy)
        if mode == 'object': copy.location += Vector((.11, .07, .03))
        else:
            arrays = [k.data for k in copy.data.shape_keys.key_blocks] if copy.data.shape_keys else [copy.data.vertices]
            for array in arrays:
                for v in array: v.co += Vector((.11, .07, .03))
            copy.data.update()
        bpy.context.view_layer.update()
        return copy
    def remove(copy):
        data = copy.data
        bpy.data.objects.remove(copy, do_unlink=True)
        if data.users == 0: bpy.data.meshes.remove(data)
    def assert_shapes(output, old_part, new_part, indices=None):
        expected = list(source.part_shapes(old_part)); actual = list(output.part_shapes(new_part))
        assert [x[0] for x in actual] == [x[0] for x in expected]
        for a, b in zip(actual, expected):
            target = b[1] if indices is None else b[1][indices]
            # Transformed or unreferenced rows rebuild from Blender float32 keys;
            # half-float re-encoding can differ by sub-micro-unit rounding.
            equal = np.array_equal(a[1], target) if indices is None else np.allclose(a[1], target, atol=1e-6, rtol=0)
            assert equal, (a[0], float(np.max(np.abs(a[1] - target))))
    with tempfile.TemporaryDirectory(prefix='sf6-moved-copy-') as temp:
        path = Path(temp) / args.source_file.name
        for mode, linked in [('object', False), ('mesh', False), ('object', True)]:
            original_state = scene_state(col)
            copy = duplicate(shaped, mode, linked)
            baseline = scene_state(col); initial_counts = counts()
            sentinel = b'existing destination must survive duplicate refusal'
            path.write_bytes(sentinel)
            try: io.exportREMeshFile(str(path), options.copy())
            except ValueError as exc: assert 'Duplicated or split source corrective geometry' in str(exc), str(exc)
            else: raise AssertionError('Shaped duplicate silently lost correctives')
            assert path.read_bytes() == sentinel
            assert scene_state(col) == baseline and counts() == initial_counts
            assert not list(Path(temp).glob('.sf6-hybrid-*'))
            passed(f'{mode}_{linked}_shaped_duplicate_refuses_atomically')
            # Exporting just the translated copy is unambiguous and retains
            # the source correctives; source geometry itself must not move.
            selected = list(bpy.context.selected_objects)
            for obj in selected: obj.select_set(False)
            copy.select_set(True)
            before = scene_state(col)
            opts = dict(options, selectedOnly=True)
            assert io.exportREMeshFile(str(path), opts)
            out = sf6.SourceMesh(path.read_bytes()); assert len(out.parts) == 1
            old = json.loads(copy[sf6.META]); new = out.parts[0]
            coords = np.array([v.co[:] for v in (copy.data.shape_keys.key_blocks[0].data if copy.data.shape_keys else copy.data.vertices)], np.float32)
            matrix = np.asarray(copy.matrix_world, np.float32)
            expected = sf6._from_blender(coords @ matrix[:3,:3].T + matrix[:3,3], True)
            assert np.allclose(out.positions(new), expected, atol=1e-6, rtol=0), (mode, float(np.max(np.abs(out.positions(new)-expected))))
            assert_shapes(out, old, new, [v.value for v in copy.data.attributes[sf6.VERTEX_ID].data])
            assert scene_state(col) == before and counts() == initial_counts
            copy.select_set(False)
            for obj in selected: obj.select_set(True)
            passed(f'{mode}_{linked}_selected_copy_preserves_positions_and_correctives')
            remove(copy)
            assert scene_state(col) == original_state
        for mode in (('object', 'mesh') if plain is not None else ()):
            original_state = scene_state(col)
            copy = duplicate(plain, mode)
            before = scene_state(col); initial_counts = counts()
            opts = options.copy()
            assert io.exportREMeshFile(str(path), opts)
            out = sf6.SourceMesh(path.read_bytes()); parts = lod0_parts(out)
            old = json.loads(plain[sf6.META])
            matches = [p for p in out.parts if p['lod'] == 0 and out.materials[p['material']] == source.materials[old['material']]]
            assert len(matches) == 1 + sum(p['lod'] == 0 and source.materials[p['material']] == source.materials[old['material']] for p in source.parts)
            expected_positions = []
            for obj in (plain, copy):
                matrix = np.asarray(obj.matrix_world, np.float32)
                coords = np.array([v.co[:] for v in obj.data.vertices], np.float32)
                expected_positions.append(sf6._from_blender(coords @ matrix[:3,:3].T + matrix[:3,3], True))
            for expected in expected_positions:
                assert any(p['count'] == len(expected) and np.allclose(out.positions(p), expected, atol=1e-6, rtol=0) for p in matches)
            for old_part in source.parts:
                if old_part['lod'] == 0 and list(source.part_shapes(old_part)):
                    assert_shapes(out, old_part, parts[key(source, old_part)])
            assert scene_state(col) == before and counts() == initial_counts
            remove(copy)
            assert scene_state(col) == original_state
            passed(f'{mode}_unshaped_copy_retains_both_positions_and_unrelated_correctives')
    if args.report_json: args.report_json.write_text(json.dumps(results, indent=2) + '\n')
    print('MOVED_DUPLICATE_TESTS_PASSED', len(results), flush=True)


if __name__ == '__main__': main()
