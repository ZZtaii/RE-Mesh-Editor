"""No-shape face integration regression, supplied local retail fixture only.

Run Blender --background --factory-startup --python-exit-code 1 --python this.py
-- --source-file path/to/face.mesh.230110883 --report-json optional.json.
Hair's shaped fixtures use test_sf6_hybrid_export.py and shape-transfer suites.
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
from test_sf6_hybrid_export import IMPORT_OPTIONS, lod0_parts, geometry_equal
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
    assert not source.shapes[0], 'Use a no-shape face fixture for this regression'
    io.importREMeshFile(str(args.source_file), IMPORT_OPTIONS.copy())
    col = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
    print('FACE_SOURCE', dict(parts=len(lod0_parts(source)), materials=source.materials,
                              preserve=bool(col.get('SF6PreserveSource'))), flush=True)
    options = dict(targetCollection=col.name, rotate90=True, selectedOnly=False,
                   exportAllLODs=False, splitLoopVertices=False,
                   useBlenderMaterialName=False, preserveBoneMatrices=True,
                   exportBoundingBoxes=False, autoSolveRepeatedUVs=True,
                   preserveSharpEdges=True, exportBlendShapes=False)
    results = []
    before = scene_state(col)
    with tempfile.TemporaryDirectory(prefix='sf6-face-') as temp:
        path = Path(temp) / args.source_file.name
        assert io.exportREMeshFile(str(path), dict(options, exportBlendShapes=True))
        assert path.read_bytes() == raw
        assert scene_state(col) == before
        results.append(dict(test='face_strict_source_preserve_byte_identical', passed=True))
        assert io.exportREMeshFile(str(path), options.copy())
        ordinary = sf6.SourceMesh(path.read_bytes())
        assert not ordinary.shapes[0]
        assert lod0_parts(ordinary).keys() == lod0_parts(source).keys()
        for key, part in lod0_parts(ordinary).items():
            assert part['faces'] == lod0_parts(source)[key]['faces']
            assert np.isfinite(ordinary.positions(part)).all()
        assert scene_state(col) == before
        results.append(dict(test='face_ordinary_export_all_parts_without_shapes', passed=True,
                            parts=len(lod0_parts(ordinary))))
        hybrid = importlib.import_module(repo.name + '.modules.mesh.sf6_hybrid')
        evaluated = importlib.import_module(repo.name + '.modules.mesh.sf6_evaluated')
        hybrid_options = dict(options, exportBlendShapes=True, sf6HybridPreserve=True)
        counts = lambda: tuple(len(getattr(bpy.data, name)) for name in
                               ('objects', 'meshes', 'shape_keys', 'collections', 'armatures'))
        initial_counts = counts()
        assert io.exportREMeshFile(str(path), hybrid_options)
        full_bytes = path.read_bytes()
        full = sf6.SourceMesh(full_bytes)
        geometry_equal(ordinary, full)
        assert not full.blend_offset and not full.shapes[0]
        assert hybrid_options['_sf6HybridReport']['no_source_shapes']
        assert hybrid_options['_sf6HybridReport']['normal_table_rebuilt']
        assert scene_state(col) == before and counts() == initial_counts
        results.append(dict(test='face_full_hybrid_without_shapes_geometry_and_normals', passed=True))

        # Direct/core builder consumes exactly the evaluated geometry used by
        # the outer atomic export path, including corner and normal handling.
        direct_path = Path(temp) / ('direct' + args.source_file.name)
        with evaluated.evaluated_hybrid_collection(col, None, preserve_sharp_edges=True) as (view, selection, _):
            opts = dict(options, targetCollection=view.name, preserveSharpEdges=False,
                        autoSolveRepeatedUVs=False, preserveMaterialTable=False,
                        _sf6EvaluatedSnapshot=True)
            assert io.exportREMeshFile(str(direct_path), opts)
            plain_bytes = direct_path.read_bytes()
            direct_bytes, report = hybrid.build_hybrid_mesh(raw, plain_bytes, view, selection)
            geometry_equal(sf6.SourceMesh(plain_bytes), sf6.SourceMesh(direct_bytes))
            assert not sf6.SourceMesh(direct_bytes).blend_offset
            assert report['output_shape_links'] == 0
        assert scene_state(col) == before and counts() == initial_counts
        results.append(dict(test='face_direct_builder_without_shapes', passed=True))

        # Sculpt an unshaped facial part and select only it. Both rebuild
        # paths must write the edit rather than restoring original positions.
        selected_part = min((o for o in col.all_objects if o.type == 'MESH' and
                             'SF6SourceMeta' in o), key=lambda o: len(o.data.vertices))
        prior_selection = list(bpy.context.selected_objects)
        prior_co = selected_part.data.vertices[0].co.copy()
        for o in tuple(bpy.context.selected_objects):
            o.select_set(False)
        selected_part.select_set(True)
        selected_part.data.vertices[0].co.x += .001
        selected_part.data.update()
        edited_before = scene_state(col)
        assert io.exportREMeshFile(str(direct_path), dict(options, selectedOnly=True))
        edited_ordinary = sf6.SourceMesh(direct_path.read_bytes())
        assert io.exportREMeshFile(str(path), dict(hybrid_options, selectedOnly=True))
        edited_hybrid = sf6.SourceMesh(path.read_bytes())
        geometry_equal(edited_ordinary, edited_hybrid)
        assert len(edited_hybrid.parts) == 1 and not edited_hybrid.blend_offset
        assert scene_state(col) == edited_before and counts() == initial_counts
        selected_part.data.vertices[0].co = prior_co
        selected_part.data.update()
        selected_part.select_set(False)
        for o in prior_selection:
            o.select_set(True)
        assert scene_state(col) == before
        results.append(dict(test='face_selected_sculpt_without_shapes', passed=True))

        # Retain existing destination even if final verification fails after
        # successful staged geometry output; no staging or datablock leaks.
        sentinel = b'previous destination must survive failed verification'
        path.write_bytes(sentinel)
        with patch.object(hybrid, '_verify_result', side_effect=RuntimeError('injected verification failure')):
            try:
                io.exportREMeshFile(str(path), hybrid_options.copy())
            except RuntimeError as error:
                assert 'injected verification failure' in str(error)
            else:
                raise AssertionError('Verification failure was swallowed')
        assert path.read_bytes() == sentinel
        assert scene_state(col) == before and counts() == initial_counts
        assert not list(Path(temp).glob('.sf6-hybrid-*'))
        results.append(dict(test='face_verification_failure_is_atomic', passed=True))

        # One reassigned unshaped source section plus intact source sections:
        # this routes through automatic mixed geometry without losing material.
        obj = next(o for o in col.all_objects if o.type == 'MESH' and 'SF6SourceMeta' in o)
        old_material = obj.data.materials[0]
        replacement = bpy.data.materials.new('FaceRegressionReplacement')
        obj.data.materials[0] = replacement
        mixed_before = scene_state(col)
        mixed_counts = counts()
        opts = hybrid_options.copy()
        assert io.exportREMeshFile(str(path), opts)
        mixed = sf6.SourceMesh(path.read_bytes())
        assert replacement.name in mixed.materials and not mixed.blend_offset
        assert len(mixed.parts) == len(full.parts)
        assert opts['_sf6HybridReport']['automatic_mixed_geometry']['new_materials'] == [replacement.name]
        assert scene_state(col) == mixed_before and counts() == mixed_counts
        obj.data.materials[0] = old_material
        bpy.data.materials.remove(replacement)
        results.append(dict(test='face_mixed_reassigned_material_no_shapes', passed=True))

        # Fresh no-shape hybrid imports must retain all exported binary tables.
        path.write_bytes(full_bytes)
        io.importREMeshFile(str(path), IMPORT_OPTIONS.copy())
        imported = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        assert io.exportREMeshFile(str(direct_path), dict(options, targetCollection=imported.name,
                                                        exportBlendShapes=True))
        assert direct_path.read_bytes() == full_bytes
        results.append(dict(test='face_no_shape_hybrid_strict_roundtrip', passed=True))
    if args.report_json:
        args.report_json.write_text(json.dumps(results, indent=2) + '\n')
    print('FACE_TESTS_PASSED', json.dumps(results), flush=True)


if __name__ == '__main__':
    main()
