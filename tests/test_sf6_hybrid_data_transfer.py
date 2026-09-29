"""Fixture-neutral Data Transfer custom-normal and SF6 corrective checks.

Run in background Blender with ``-- --source-file ORIGINAL.mesh.230110883``.
The fixture needs a shaped LOD0 part with proven normal-table encoding. The
reference object is private test data outside the exported mesh collection.
Expected normals, UVs and corrective endpoints come from an independent
Blender evaluation, rather than the hybrid snapshot implementation.
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


def enum_set(owner, field, value):
    """Resolve settings against the actual running Blender RNA."""
    choices = {item.identifier for item in owner.bl_rna.properties[field].enum_items}
    assert value in choices, (field, value, sorted(choices))
    setattr(owner, field, value)


def author_reference(target, label):
    """Keep actual shape coordinates but author a measurable normal change."""
    reference = target.copy()
    reference.data = target.data.copy()
    reference.name = 'Normal Transfer Reference ' + label
    reference.parent = None
    reference.matrix_world = target.matrix_world.copy()
    reference.modifiers.clear()
    reference.animation_data_clear()
    reference.data.animation_data_clear()
    reference.data.shape_keys.animation_data_clear()
    for key in reference.data.shape_keys.key_blocks:
        key.value, key.mute = 0, False
    reference.active_shape_key_index = 0
    reference.show_only_shape_key = False
    reference['MeshExportExclude'] = True
    bpy.context.scene.collection.objects.link(reference)
    reference.hide_set(False)
    normals = np.asarray([tuple(item.vector) for item in reference.data.corner_normals], np.float32)
    # Reorient the original smooth normals without changing silhouette, then
    # give a few shared corners genuine discontinuities without sharp flags.
    normals += np.array((0.23, 0.11, 0.07), np.float32)
    for poly in list(reference.data.polygons)[:24]:
        if poly.index % 2:
            normals[list(poly.loop_indices), 1] += 0.35
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    reference.data.normals_split_custom_set(normals)
    reference.data.update()
    return reference


def normal_transfer(target, reference, mapping, mix='REPLACE', factor=1.0):
    allowed = {item.identifier for item in
        bpy.types.ObjectModifiers.bl_rna.functions['new'].parameters['type'].enum_items}
    assert 'DATA_TRANSFER' in allowed
    transfer = target.modifiers.new('Transferred custom normals', 'DATA_TRANSFER')
    transfer.object = reference
    transfer.use_loop_data = True
    loop_types = {item.identifier for item in transfer.bl_rna.properties['data_types_loops'].enum_items}
    assert 'CUSTOM_NORMAL' in loop_types
    transfer.data_types_loops = {'CUSTOM_NORMAL'}
    enum_set(transfer, 'loop_mapping', mapping)
    enum_set(transfer, 'mix_mode', mix)
    transfer.mix_factor = factor
    transfer.use_object_transform = True
    return transfer


def transformed_positions(points, target, mesh_io, rotate):
    """Use Blender's float32 coordinate transform at the file's orientation."""
    mesh = bpy.data.meshes.new('Private oracle position transform')
    try:
        mesh.from_pydata(points.tolist(), [], [])
        matrix = (mesh_io.rotateNeg90Matrix @ target.matrix_world
                  if rotate else target.matrix_world)
        mesh.transform(matrix)
        values = np.empty((len(mesh.vertices), 3), np.float32)
        mesh.vertices.foreach_get('co', values.ravel())
        return values
    finally:
        bpy.data.meshes.remove(mesh)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--report-json', type=Path)
    parser.add_argument('--case-filter')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + '.modules.mesh.blender_re_mesh')
    sf6 = importlib.import_module(repo.name + '.modules.mesh.sf6_source')
    hybrid = importlib.import_module(repo.name + '.modules.mesh.sf6_hybrid')
    snapshots = importlib.import_module(repo.name + '.modules.mesh.sf6_evaluated')
    spec = importlib.util.spec_from_file_location('data_transfer_oracles',
        repo / 'tests' / 'test_sf6_hybrid_modifiers.py')
    u = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(u)
    multires_spec = importlib.util.spec_from_file_location('data_transfer_multires_author',
        repo / 'tests' / 'test_sf6_hybrid_multires.py')
    multires_author = importlib.util.module_from_spec(multires_spec)
    multires_spec.loader.exec_module(multires_author)
    source = sf6.SourceMesh(args.source_file.read_bytes())
    source_parts = u.lod0_parts(source)
    vp, fp = hybrid._normal_pointers(source)
    first = next(part for part in source.parts if part['lod'] == 0)
    supported = []
    for identity, part in source_parts.items():
        if not list(source.part_shapes(part)):
            continue
        expected_v, expected_f = hybrid._synthesize_normals(source, part, first['start'])
        original_v = np.frombuffer(source.data, '<u4', part['count'], vp + (part['start'] - first['start']) * 4)
        original_f = np.frombuffer(source.data, '<u4', hybrid._padded_indices(part), fp + (part['face_start'] - first['face_start']) * 4)
        if np.array_equal(expected_v, original_v) and np.array_equal(expected_f, original_f):
            supported.append(identity)
    assert supported, 'Fixture needs shaped geometry with proven normal encoding'
    chosen = next((identity for identity in supported if 'shirts' in identity[1].lower()),
                  min(supported, key=lambda identity: source_parts[identity]['count']))
    expected_shapes = len(list(source.part_shapes(source_parts[chosen])))
    results, roundtrips = [], []

    def fresh():
        assert mesh_io.importREMeshFile(str(args.source_file), u.IMPORT_OPTIONS.copy())
        collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        target = next(obj for obj in collection.all_objects if obj.type == 'MESH' and
            obj.get('SF6SourceMeta') and u.part_key(source, json.loads(obj['SF6SourceMeta'])) == chosen)
        u.select([target])
        return collection, target

    def options(collection, *, selected=True, sharp=False):
        return dict(targetCollection=collection.name, rotate90=bool(collection['SF6SourceRotate']),
            selectedOnly=selected, exportAllLODs=False, splitLoopVertices=False,
            useBlenderMaterialName=False, preserveBoneMatrices=True, exportBoundingBoxes=False,
            autoSolveRepeatedUVs=False, preserveSharpEdges=sharp, limitTotal=True,
            limitTotalCount=6, normalizeWeights=True,
            exportBlendShapes=True, sf6HybridPreserve=True)

    def state():
        counts = tuple(len(items) for items in (bpy.data.objects, bpy.data.meshes,
            bpy.data.armatures, bpy.data.collections, bpy.data.shape_keys, bpy.data.texts))
        corners = {obj.name: (obj.matrix_world.copy(), obj.hide_get(),
            np.asarray([tuple(normal.vector) for normal in obj.data.corner_normals], np.float32).tobytes())
            for obj in bpy.context.scene.objects if obj.type == 'MESH'}
        return u.scene_state(bpy.context.scene.collection), counts, corners

    with tempfile.TemporaryDirectory(prefix='sf6-data-transfer-tests-') as temporary:
        root = args.output_dir or Path(temporary)
        root.mkdir(parents=True, exist_ok=True)
        report_path = args.report_json or root / 'data-transfer-results.json'

        def passed(test, **details):
            result = dict(test=test, passed=True, **details)
            results.append(result)
            print('PASS ' + json.dumps(result), flush=True)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(results, indent=2) + '\n', encoding='utf8')
            return result

        cases = []
        for mapping in ('TOPOLOGY', 'POLYINTERP_NEAREST'):
            for sharp in (False, True):
                cases.append(dict(name=mapping.lower() + '_replace_export_sharp_' + str(sharp),
                    mapping=mapping, sharp=sharp))
        cases.extend([
            dict(name='nearest_replace_half', mapping='POLYINTERP_NEAREST', factor=0.5),
            dict(name='nearest_replace_zero', mapping='POLYINTERP_NEAREST', factor=0.0),
            dict(name='nearest_mix_half', mapping='POLYINTERP_NEAREST', mix='MIX', factor=0.5),
            dict(name='catmull_subdivision_then_nearest_transfer', mapping='POLYINTERP_NEAREST', subdivision=True),
            dict(name='sculpted_multires_then_nearest_transfer', mapping='POLYINTERP_NEAREST', multires=True),
            dict(name='excluded_reference_matching_simple_subdivision_topology', mapping='TOPOLOGY',
                 subdivision=True, simple=True, excluded_reference=True),
            dict(name='nearest_vertex_group_mask', mapping='POLYINTERP_NEAREST', mask=True),
            dict(name='nearest_reference_current_shape_and_modifier', mapping='POLYINTERP_NEAREST', animated_reference=True),
            dict(name='nearest_hidden_reference', mapping='POLYINTERP_NEAREST', hidden_reference=True),
            dict(name='nearest_computed_reference_normals', mapping='POLYINTERP_NEAREST', computed_reference=True),
            dict(name='nearest_max_distance_without_matches', mapping='POLYINTERP_NEAREST', far_reference=True),
            dict(name='full_collection_nearest_transfer', mapping='POLYINTERP_NEAREST', full=True),
        ])
        for case in cases:
            name = case['name']
            if args.case_filter and args.case_filter not in name:
                continue
            collection, target = fresh()
            reference = author_reference(target, name)
            if case.get('computed_reference'):
                original = reference.data
                points = u.coords(original.vertices)
                points[:, 0] += 0.08 * (points[:, 2] - points[:, 2].min())
                computed = bpy.data.meshes.new('Computed normal donor mesh')
                computed.from_pydata(points.tolist(), [], [tuple(poly.vertices) for poly in original.polygons])
                for poly in computed.polygons:
                    poly.use_smooth = True
                computed.update()
                reference.data = computed
                bpy.data.meshes.remove(original)
                assert not reference.data.has_custom_normals, 'Computed-normal donor must lack authored normals'
            if case.get('subdivision'):
                subdivision = target.modifiers.new('Subdivision before transfer', 'SUBSURF')
                enum_set(subdivision, 'subdivision_type', 'SIMPLE' if case.get('simple') else 'CATMULL_CLARK')
                subdivision.levels, subdivision.render_levels = 1, 2
            if case.get('excluded_reference'):
                donor_subdivision = reference.modifiers.new('Reference subdivision', 'SUBSURF')
                enum_set(donor_subdivision, 'subdivision_type', 'SIMPLE')
                donor_subdivision.levels, donor_subdivision.render_levels = 1, 2
                donor_collection = bpy.data.collections.new('Excluded normal reference')
                bpy.context.scene.collection.children.link(donor_collection)
                for owner in tuple(reference.users_collection):
                    owner.objects.unlink(reference)
                donor_collection.objects.link(reference)
                bpy.context.view_layer.layer_collection.children[donor_collection.name].exclude = True
            if case.get('multires'):
                multires_author.author_multires_sculpt(target, name, mesh_io, u, passed)
            prior_samples, _, prior_triangles, _, prior_uvs, prior_normals = u.evaluate_oracle(target, mesh_io)
            transfer = normal_transfer(target, reference, case['mapping'],
                case.get('mix', 'REPLACE'), case.get('factor', 1.0))
            if case.get('mask'):
                group = target.vertex_groups.new(name='NormalTransferMask')
                count = len(target.data.vertices)
                group.add(list(range(count // 2)), 0.8, 'REPLACE')
                group.add(list(range(count // 2, count)), 0.0, 'REPLACE')
                transfer.vertex_group = group.name
            if case.get('animated_reference'):
                reference.data.shape_keys.key_blocks[1].value = 0.45
                deformation = reference.modifiers.new('Current reference twist', 'SIMPLE_DEFORM')
                enum_set(deformation, 'deform_method', 'TWIST')
                enum_set(deformation, 'deform_axis', 'Z')
                deformation.angle = 0.18
            if case.get('far_reference'):
                reference.location.x += 100
                transfer.use_max_distance, transfer.max_distance = True, 0.001
            if case.get('hidden_reference'):
                reference.hide_set(True)
                reference.hide_viewport = True
            keys = target.data.shape_keys.key_blocks
            keys[1].value, keys[1].mute = 0.3, True
            target.active_shape_key_index, target.show_only_shape_key = 1, True
            if len(keys) > 2:
                keys[2].value = 0.6
            bpy.context.view_layer.update()
            samples, signatures, triangles, polygon_count, uv, normals = u.evaluate_oracle(target, mesh_io)
            basis_name = keys[0].name
            assert all(signature == signatures[basis_name] for signature in signatures.values())
            assert np.array_equal(triangles, prior_triangles), 'Normal-only transfer changed topology'
            for shape in samples:
                assert np.array_equal(samples[shape], prior_samples[shape]), 'Normal-only transfer moved geometry'
            assert all(np.array_equal(before, after) for before, after in zip(prior_uvs, uv))
            effect = np.abs(np.floor(prior_normals * 127) - np.floor(normals * 127))
            changed_corners = int(np.count_nonzero(np.any(effect > 1, axis=2)))
            if case.get('far_reference') or case.get('factor') == 0.0:
                assert changed_corners == 0, 'Unmatched or zero-factor transfer must keep existing shading'
            else:
                assert changed_corners > 0, 'Data Transfer must measurably change shading'
            before = state()
            path = root / (name + '.mesh.230110883')
            opts = options(collection, selected=not case.get('full'), sharp=case.get('sharp', False))
            assert mesh_io.exportREMeshFile(str(path), opts)
            assert state() == before, 'Export changed source/reference or leaked private data'
            result = sf6.SourceMesh(path.read_bytes())
            part = u.lod0_parts(result)[chosen]
            mapping = u.output_row_mapping(result, part, samples[basis_name], triangles, opts['rotate90'], sf6)
            wanted_positions = transformed_positions(samples[basis_name], target, mesh_io, opts['rotate90'])
            assert np.array_equal(result.positions(part), wanted_positions[mapping]), 'Export changed evaluated positions'
            u.validate_corner_uvs(result, part, uv)
            normal_error = u.validate_evaluated_corner_normals(result, part, normals, opts['rotate90'], sf6)
            actual = {shape: delta for shape, delta, _ in result.part_shapes(part)}
            assert len(actual) == expected_shapes and actual.keys() == set(samples) - {basis_name}
            for shape, coordinates in samples.items():
                if shape == basis_name:
                    continue
                expected = sf6._from_blender((coordinates - samples[basis_name])[mapping],
                    opts['rotate90']).astype('<f2').astype('<f4')
                assert np.array_equal(actual[shape], expected), (name, shape,
                    int(np.count_nonzero(actual[shape] != expected)), float(np.max(np.abs(actual[shape] - expected))))
            u.validate_target_bounds(result)
            if case.get('full'):
                assert len(result.parts) == len(source_parts)
                assert len(result.shapes[0]) == len(source.shapes[0])
            row_report = next(item for item in opts['_sf6HybridReport']['evaluated_geometry']['parts']
                if item['source_name'] == target.name)
            detail = passed(name + '_encoded_normals_uvs_and_correctives',
                mapping=case['mapping'], mix=case.get('mix', 'REPLACE'), factor=case.get('factor', 1.0),
                vertices=part['count'], triangles=part['faces'], evaluated_polygons=polygon_count,
                shape_links=len(actual), changed_normal_corners=changed_corners,
                max_normal_quantization_error=normal_error, normal_split_vertices=row_report['normal_split_vertices'],
                total_parts=len(result.parts), total_shape_links=len(result.shapes[0]))
            roundtrips.append((path, detail, opts['rotate90']))

        for invalid in ('missing_source', 'self_source', 'non_mesh_source', 'empty_source',
                        'topology_after_subdivision'):
            if args.case_filter and args.case_filter not in invalid:
                continue
            collection, target = fresh()
            reference = author_reference(target, invalid)
            if invalid == 'topology_after_subdivision':
                subdivision = target.modifiers.new('Subdivision before topology transfer', 'SUBSURF')
                enum_set(subdivision, 'subdivision_type', 'SIMPLE')
                subdivision.levels = 1
            transfer = normal_transfer(target, reference, 'TOPOLOGY')
            if invalid == 'missing_source':
                transfer.object = None
            elif invalid == 'self_source':
                try:
                    transfer.object = target
                except (TypeError, ValueError):
                    passed('self_source_refused_by_blender_rna')
                    continue
            elif invalid == 'non_mesh_source':
                empty = bpy.data.objects.new('Invalid non-mesh normal donor', None)
                bpy.context.scene.collection.objects.link(empty)
                transfer.object = None
                transfer.object = empty
            elif invalid == 'empty_source':
                empty = bpy.data.objects.new('Empty normal donor mesh', bpy.data.meshes.new('No corners'))
                bpy.context.scene.collection.objects.link(empty)
                transfer.object = empty
            expected_error = ('topology mapping has different corner counts' if invalid == 'topology_after_subdivision'
                else 'has no faces' if invalid == 'empty_source'
                else 'separate mesh source')
            destination = root / (invalid + '-rejected.mesh.230110883')
            sentinel = b'Existing mesh must survive invalid Data Transfer normals'
            destination.write_bytes(sentinel)
            bpy.context.view_layer.update()
            before = state()
            markers = {name: bpy.context.scene[name] for name in bpy.context.scene.keys()
                if name.startswith('REMeshLast')}
            try:
                mesh_io.exportREMeshFile(str(destination), options(collection))
            except ValueError as error:
                assert expected_error in str(error), (invalid, str(error))
            else:
                raise AssertionError('Invalid Data Transfer normals must be refused: ' + invalid)
            assert destination.read_bytes() == sentinel
            assert state() == before, 'Rejected export changed user state or leaked private data'
            assert {name: bpy.context.scene[name] for name in bpy.context.scene.keys()
                if name.startswith('REMeshLast')} == markers
            assert not tuple(root.glob('.sf6-hybrid-*'))
            passed(invalid + '_refused_atomically', expected_error=expected_error)

        for ignored in ('viewport_disabled', 'corner_data_disabled'):
            if args.case_filter and args.case_filter not in ignored:
                continue
            collection, target = fresh()
            transfer = target.modifiers.new('Inactive normal transfer', 'DATA_TRANSFER')
            transfer.use_loop_data = True
            transfer.data_types_loops = {'CUSTOM_NORMAL'}
            transfer.object = None
            if ignored == 'viewport_disabled':
                transfer.show_viewport = False
            else:
                transfer.use_loop_data = False
            before = state()
            with snapshots.evaluated_hybrid_collection(collection, (target,)) as (_, _, report):
                assert not any(item.get('custom_normals') for part in report['parts']
                    for item in part['active_modifiers'])
            assert state() == before
            passed(ignored + '_does_not_require_normal_donor')

        for path, detail, rotate in roundtrips:
            expected_bytes = path.read_bytes()
            assert mesh_io.importREMeshFile(str(path), u.IMPORT_OPTIONS.copy())
            collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
            meshes = [obj for obj in collection.all_objects if obj.type == 'MESH']
            assert len(meshes) == detail['total_parts']
            roundtrip = path.with_name(path.name.replace('.mesh.', '-roundtrip.mesh.'))
            assert mesh_io.exportREMeshFile(str(roundtrip), dict(targetCollection=collection.name,
                exportBlendShapes=True, rotate90=rotate))
            assert roundtrip.read_bytes() == expected_bytes
            detail['strict_reimport_roundtrip_byte_identical'] = True
        if roundtrips:
            passed('all_data_transfer_exports_strict_reimport_roundtrip_bytes', files=len(roundtrips))
        report_path.write_text(json.dumps(results, indent=2) + '\n', encoding='utf8')
    print('ALL_HYBRID_DATA_TRANSFER_TESTS_PASSED ' + str(len(results)), flush=True)


if __name__ == '__main__':
    main()
