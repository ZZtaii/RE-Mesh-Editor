"""Fixture-neutral Blender checks for weighted and implicit split normals.

Run in background Blender with ``-- --source-file ORIGINAL.mesh.230110883``.
The fixture needs a nonplanar shaped LOD0 part with proven normal encoding.
Private independently evaluated clones supply corner normals, UVs and keys.
"""

import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--report-json', type=Path)
    parser.add_argument('--case-filter', help='Run only cases containing this text')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + '.modules.mesh.blender_re_mesh')
    sf6 = importlib.import_module(repo.name + '.modules.mesh.sf6_source')
    hybrid = importlib.import_module(repo.name + '.modules.mesh.sf6_hybrid')
    spec = importlib.util.spec_from_file_location('modifier_oracles',
        repo / 'tests/test_sf6_hybrid_modifiers.py')
    u = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(u)
    source = sf6.SourceMesh(args.source_file.read_bytes())
    source_parts = u.lod0_parts(source)
    vp, fp = hybrid._normal_pointers(source)
    first = next(part for part in source.parts if part['lod'] == 0)
    supported = []
    for key, part in source_parts.items():
        if not list(source.part_shapes(part)):
            continue
        expected_v, expected_f = hybrid._synthesize_normals(source, part, first['start'])
        original_v = np.frombuffer(source.data, '<u4', part['count'], vp + (part['start'] - first['start']) * 4)
        original_f = np.frombuffer(source.data, '<u4', hybrid._padded_indices(part), fp + (part['face_start'] - first['face_start']) * 4)
        if np.array_equal(expected_v, original_v) and np.array_equal(expected_f, original_f):
            supported.append(key)
    assert supported, 'Fixture needs a shaped part with proven normal encoding'
    chosen = min(supported, key=lambda key: source_parts[key]['count'])
    results, reimports = [], []

    def fresh():
        assert mesh_io.importREMeshFile(str(args.source_file), u.IMPORT_OPTIONS.copy())
        collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        target = next(obj for obj in collection.all_objects if obj.type == 'MESH' and
            obj.get('SF6SourceMeta') and u.part_key(source, json.loads(obj['SF6SourceMeta'])) == chosen)
        u.select([target])
        return collection, target

    def options(collection, selected=True, sharp=False):
        return dict(targetCollection=collection.name, rotate90=bool(collection['SF6SourceRotate']),
            selectedOnly=selected, exportAllLODs=False, splitLoopVertices=False,
            useBlenderMaterialName=False, preserveBoneMatrices=True, exportBoundingBoxes=False,
            autoSolveRepeatedUVs=False, preserveSharpEdges=sharp, limitTotal=True,
            limitTotalCount=6, normalizeWeights=True)

    def counts():
        return tuple(len(items) for items in (bpy.data.objects, bpy.data.meshes,
            bpy.data.armatures, bpy.data.collections, bpy.data.shape_keys))

    def passed(test, **details):
        result = dict(test=test, passed=True, **details)
        results.append(result)
        print('PASS ' + json.dumps(result), flush=True)
        if args.report_json:
            args.report_json.parent.mkdir(parents=True, exist_ok=True)
            args.report_json.write_text(json.dumps(results, indent=2) + '\n', encoding='utf8')
        return result

    with tempfile.TemporaryDirectory(prefix='sf6-weighted-normal-tests-') as temporary:
        root = args.output_dir or Path(temporary)
        root.mkdir(parents=True, exist_ok=True)
        for selected in (False, True):
            collection, target = fresh()
            name = 'unchanged_subset' if selected else 'unchanged_full'
            if args.case_filter and args.case_filter not in name:
                continue
            ordinary_path, hybrid_path = root / (name + '-ordinary.mesh.230110883'), root / (name + '-hybrid.mesh.230110883')
            original_state = u.scene_state(collection)
            opts = options(collection, selected)
            assert mesh_io.exportREMeshFile(str(ordinary_path), dict(opts, exportBlendShapes=False))
            assert u.scene_state(collection) == original_state
            before = u.scene_state(collection), counts()
            assert mesh_io.exportREMeshFile(str(hybrid_path), dict(opts, exportBlendShapes=True, sf6HybridPreserve=True))
            assert (u.scene_state(collection), counts()) == before
            ordinary, result = sf6.SourceMesh(ordinary_path.read_bytes()), sf6.SourceMesh(hybrid_path.read_bytes())
            u.geometry_equal(ordinary, result)
            passed(name + '_normal_and_geometry_streams_exact_to_ordinary', parts=len(result.parts))

        cases = [(f'weighted_keep_{keep}_export_sharp_{split}', keep, split, None, False)
                 for keep in (False, True) for split in (False, True)]
        cases += [('solidify_then_weighted_normal', True, True, 'solidify', False),
                  ('subdivision_then_weighted_normal', True, True, 'subdivision', False)]
        cases += [('weighted_flat_faces_smooth_edges_' + str(split), True, split, None, True)
                  for split in (False, True)]
        cases += [('unmodified_flat_faces_smooth_edges_' + str(split), None, split, None, True)
                  for split in (False, True)]
        for name, keep_sharp, export_sharp, prefix, flat_faces in cases:
            if args.case_filter and args.case_filter not in name:
                continue
            collection, target = fresh()
            mesh = target.data
            for poly in mesh.polygons:
                poly.use_smooth = not flat_faces or poly.index >= 32
            mesh.normals_split_custom_set([(0, 0, 0)] * len(mesh.loops))
            for edge in mesh.edges:
                edge.use_edge_sharp = False
            if not flat_faces:
                adjacent = {}
                for poly in mesh.polygons:
                    rows = list(poly.vertices)
                    for index, row in enumerate(rows):
                        adjacent.setdefault(tuple(sorted((row, rows[(index + 1) % len(rows)]))), []).append(poly)
                edges = {tuple(sorted(edge.vertices)): edge for edge in mesh.edges}
                candidates = sorted([(1 - float(faces[0].normal.dot(faces[1].normal)), key)
                    for key, faces in adjacent.items() if len(faces) == 2], reverse=True)
                assert candidates and candidates[0][0] > 0.001, 'Fixture needs nonplanar shared faces'
                for difference, key in candidates[:12]:
                    if difference > 0:
                        edges[key].use_edge_sharp = True
            mesh.update()
            bpy.context.view_layer.update()
            if prefix == 'solidify':
                modifier = target.modifiers.new('Solidify before Weighted Normal', 'SOLIDIFY')
                modifier.thickness, modifier.offset, modifier.use_rim, modifier.use_even_offset = .005, 0, False, False
            elif prefix == 'subdivision':
                modifier = target.modifiers.new('Subdivision before Weighted Normal', 'SUBSURF')
                modifier.subdivision_type, modifier.levels, modifier.render_levels = 'SIMPLE', 1, 1
            _, _, _, _, _, prior_normals = u.evaluate_oracle(target, mesh_io)
            if keep_sharp is not None:
                weighted = target.modifiers.new('Weighted Normal', 'WEIGHTED_NORMAL')
                weighted.mode, weighted.weight, weighted.thresh, weighted.keep_sharp = 'FACE_AREA', 100, 0, keep_sharp
            samples, signatures, triangles, polygons, uv, normals = u.evaluate_oracle(target, mesh_io)
            basis_name = target.data.shape_keys.key_blocks[0].name
            assert all(value == signatures[basis_name] for value in signatures.values())
            effect = np.abs(np.floor(prior_normals * 127) - np.floor(normals * 127))
            changed_corners = int(np.count_nonzero(np.any(effect > 1, axis=2)))
            if keep_sharp is not None:
                assert changed_corners > 0, 'Weighted Normal must have a meaningful shading effect'
            if flat_faces:
                assert not any(edge.use_edge_sharp for edge in mesh.edges)
            fans = {}
            for row, normal in zip(triangles.ravel(), normals.reshape(-1, 3)):
                fans.setdefault(int(row), []).append(np.floor(normal * 127).astype(int))
            distinct_normal_rows = sum(np.any(np.ptp(values, axis=0) > 1) for values in fans.values())
            if flat_faces:
                assert distinct_normal_rows > 0, 'Expected distinct normal fans on smooth edges'
            before = u.scene_state(collection), counts()
            path = root / (name + '.mesh.230110883')
            opts = dict(options(collection, sharp=export_sharp), exportBlendShapes=True, sf6HybridPreserve=True)
            assert mesh_io.exportREMeshFile(str(path), opts)
            assert (u.scene_state(collection), counts()) == before
            result = sf6.SourceMesh(path.read_bytes())
            part = u.lod0_parts(result)[chosen]
            mapping = u.output_row_mapping(result, part, samples[basis_name], triangles, opts['rotate90'], sf6)
            u.validate_corner_uvs(result, part, uv)
            normal_error = u.validate_evaluated_corner_normals(result, part, normals, opts['rotate90'], sf6)
            actual = {name: delta for name, delta, _ in result.part_shapes(part)}
            assert actual.keys() == set(samples) - {basis_name}
            for shape, coordinates in samples.items():
                if shape != basis_name:
                    expected = sf6._from_blender((coordinates - samples[basis_name])[mapping], opts['rotate90']).astype('<f2').astype('<f4')
                    assert np.array_equal(actual[shape], expected), (shape,
                        int(np.count_nonzero(actual[shape] != expected)),
                        float(np.max(np.abs(actual[shape] - expected))))
            u.validate_target_bounds(result)
            row_report = opts['_sf6HybridReport']['evaluated_geometry']['parts'][0]
            if distinct_normal_rows:
                assert row_report['normal_split_vertices'] > 0, 'Meaningful normal fan must own its export rows'
            detail = passed(name + '_corner_normals_uvs_and_correctives', vertices=part['count'], triangles=part['faces'],
                shape_links=len(actual), changed_normal_corners=changed_corners,
                max_normal_quantization_error=normal_error, normal_split_vertices=row_report['normal_split_vertices'],
                rows_with_distinct_normal_fans=int(distinct_normal_rows))
            reimports.append((path, detail, opts['rotate90']))

        for path, detail, rotate in reimports:
            expected_bytes = path.read_bytes()
            assert mesh_io.importREMeshFile(str(path), u.IMPORT_OPTIONS.copy())
            collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
            meshes = [obj for obj in collection.all_objects if obj.type == 'MESH']
            assert len(meshes) == 1 and len(meshes[0].data.shape_keys.key_blocks) == detail['shape_links'] + 1
            roundtrip = path.with_name(path.name.replace('.mesh.', '-roundtrip.mesh.'))
            assert mesh_io.exportREMeshFile(str(roundtrip), dict(targetCollection=collection.name,
                exportBlendShapes=True, rotate90=rotate))
            assert roundtrip.read_bytes() == expected_bytes
            detail['strict_roundtrip_identical'] = True
        if args.report_json:
            args.report_json.write_text(json.dumps(results, indent=2) + '\n', encoding='utf8')
    print('ALL_HYBRID_WEIGHTED_NORMAL_TESTS_PASSED ' + str(len(results)), flush=True)


if __name__ == '__main__':
    main()
