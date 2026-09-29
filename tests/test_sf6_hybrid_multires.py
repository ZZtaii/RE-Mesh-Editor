"""Fixture-neutral Multires sculpt-displacement and SF6 corrective checks.

Run in background Blender with ``-- --source-file ORIGINAL.mesh.230110883``.
The supplied fixture must contain a shaped LOD0 part with proven normal-table
encoding. A high-resolution reshape authors real Multires grid displacement;
empty subdivision alone is not evidence for this sculpting workflow.
"""

import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile

import addon_utils
import bmesh
import bpy
import numpy as np


def author_multires_sculpt(target, label, mesh_io, oracle, passed=None):
    """Author real grids on a disposable, shaped mesh with no other generator."""
    subdivision_modes = {item.identifier for item in
        bpy.ops.object.multires_subdivide.get_rna_type().properties['mode'].enum_items}
    assert 'CATMULL_CLARK' in subdivision_modes
    keys = target.data.shape_keys.key_blocks
    target.active_shape_key_index, target.show_only_shape_key = 0, False
    for key in keys:
        key.value, key.mute = 0, False
    base_key_points = [oracle.coords(key.data).copy() for key in keys]
    base_points = oracle.coords(target.data.vertices).copy()
    multires = target.modifiers.new('Sculpt Multires ' + label, 'MULTIRES')
    oracle.select([target])
    for _ in range(2):
        assert bpy.ops.object.multires_subdivide(modifier=multires.name,
            mode='CATMULL_CLARK') == {'FINISHED'}
    assert multires.total_levels == 2
    unsculpted = {}
    for level in (0, 1, 2):
        multires.levels = level
        unsculpted[level] = oracle.evaluate_oracle(target, mesh_io)[0]['Basis']
    multires.levels = 2
    # The independent oracle supplies ordered high-resolution rows. A
    # selected same-layout mesh is the supported Multires Reshape source,
    # equivalent to authoring its coordinates with a sculpting brush.
    clone = target.copy()
    private = target.data.copy()
    clone.data = private
    bpy.context.scene.collection.objects.link(clone)
    for modifier in clone.modifiers:
        if modifier.type == 'ARMATURE':
            modifier.show_viewport = False
    clone.active_shape_key_index, clone.show_only_shape_key = 0, True
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(clone.evaluated_get(dg),
        preserve_all_data_layers=True, depsgraph=dg)
    bpy.data.objects.remove(clone, do_unlink=True)
    if private.users == 0:
        bpy.data.meshes.remove(private)
    reshape = bpy.data.objects.new('Multires Test Reshape', mesh)
    bpy.context.scene.collection.objects.link(reshape)
    armature_visibility = [(m, m.show_viewport) for m in target.modifiers if m.type == 'ARMATURE']
    try:
        points = oracle.coords(mesh.vertices)
        # Broad shape plus fine detail must survive at level 1 and 2.
        displacement = .003 * (.5 + .5 * np.sin(points[:, 1] * 80))
        displacement += .001 * np.sin(points[:, 2] * 370)
        changed = points.copy()
        changed[:, 0] += displacement
        mesh.vertices.foreach_set('co', changed.ravel())
        mesh.update()
        for modifier, _ in armature_visibility:
            modifier.show_viewport = False
        bpy.context.view_layer.update()
        oracle.select([reshape, target])
        bpy.context.view_layer.objects.active = target
        assert bpy.ops.object.multires_reshape(modifier=multires.name) == {'FINISHED'}
        reshaped = oracle.evaluate_oracle(target, mesh_io)[0]['Basis']
        assert np.allclose(reshaped, changed, atol=1e-6, rtol=0), float(np.max(np.abs(reshaped - changed)))
    finally:
        for modifier, visible in armature_visibility:
            modifier.show_viewport = visible
        bpy.data.objects.remove(reshape, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    sculpted = {}
    sculpt_details = {}
    for level in (0, 1, 2):
        multires.levels = level
        sculpted[level] = oracle.evaluate_oracle(target, mesh_io)[0]['Basis']
        difference = np.linalg.norm(sculpted[level] - unsculpted[level], axis=1)
        sculpt_details[level] = dict(vertices=len(difference),
            moved_vertices=int(np.count_nonzero(difference > 1e-6)),
            maximum_displacement=float(np.max(difference, initial=0)))
    assert sculpt_details[1]['moved_vertices'] > 0
    assert sculpt_details[2]['moved_vertices'] > len(base_points)
    assert sculpt_details[2]['maximum_displacement'] > .001
    assert np.array_equal(oracle.coords(target.data.vertices), base_points)
    assert all(np.array_equal(oracle.coords(key.data), points)
        for key, points in zip(keys, base_key_points)), 'Reshape must author grids, not base keys'
    multires.levels, multires.sculpt_levels, multires.render_levels = 1, 2, 2
    oracle.select([target])
    if passed is not None:
        passed(label + '_authors_nonzero_high_resolution_grid_displacement',
            per_level=sculpt_details, base_mesh_and_corrective_coordinates_unchanged=True)
    return multires, sculpted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--report-json', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + '.modules.mesh.blender_re_mesh')
    sf6 = importlib.import_module(repo.name + '.modules.mesh.sf6_source')
    hybrid = importlib.import_module(repo.name + '.modules.mesh.sf6_hybrid')
    reports = importlib.import_module(repo.name + '.modules.mesh.sf6_hybrid_report')
    object_modes = {item.identifier for item in
        bpy.ops.object.mode_set.get_rna_type().properties['mode'].enum_items}
    assert {'OBJECT', 'EDIT', 'SCULPT'} <= object_modes
    spec = importlib.util.spec_from_file_location('multires_oracles',
        repo / 'tests/test_sf6_hybrid_modifiers.py')
    u = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(u)
    source = sf6.SourceMesh(args.source_file.read_bytes())
    parts = u.lod0_parts(source)
    vp, fp = hybrid._normal_pointers(source)
    first = next(part for part in source.parts if part['lod'] == 0)
    eligible = []
    for key, part in parts.items():
        if not list(source.part_shapes(part)):
            continue
        wanted_v, wanted_f = hybrid._synthesize_normals(source, part, first['start'])
        old_v = np.frombuffer(source.data, '<u4', part['count'],
            vp + (part['start'] - first['start']) * 4)
        old_f = np.frombuffer(source.data, '<u4', hybrid._padded_indices(part),
            fp + (part['face_start'] - first['face_start']) * 4)
        if np.array_equal(wanted_v, old_v) and np.array_equal(wanted_f, old_f):
            eligible.append(key)
    assert eligible, 'Fixture needs a shaped part with proven normal encoding'
    chosen = min(eligible, key=lambda key: parts[key]['count'])
    temporary = tempfile.TemporaryDirectory(prefix='sf6-multires-tests-')
    root = args.output_dir or Path(temporary.name)
    root.mkdir(parents=True, exist_ok=True)
    results, roundtrips = [], []

    def passed(test, **details):
        result = dict(test=test, passed=True, **details)
        results.append(result)
        print('PASS ' + json.dumps(result), flush=True)
        destination = args.report_json or root / 'multires-results.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(results, indent=2) + '\n', encoding='utf8')
        return result

    def fresh():
        assert mesh_io.importREMeshFile(str(args.source_file), u.IMPORT_OPTIONS.copy())
        collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        target = next(obj for obj in collection.all_objects if obj.type == 'MESH' and
            obj.get('SF6SourceMeta') and u.part_key(source, json.loads(obj['SF6SourceMeta'])) == chosen)
        u.select([target])
        return collection, target

    def state(collection):
        modifier_details = []
        for obj in collection.all_objects:
            modifier_details.append((obj.name, obj.mode, obj.data.use_fake_user,
                obj.data.shape_keys.use_fake_user if obj.type == 'MESH' and obj.data.shape_keys else None,
                tuple((m.name, m.levels, m.sculpt_levels, m.render_levels, m.total_levels,
                       m.is_external, m.filepath) for m in obj.modifiers if m.type == 'MULTIRES')))
        return u.scene_state(collection), modifier_details

    def ids():
        return {name: tuple(sorted((item.name_full, item.as_pointer())
            for item in getattr(bpy.data, name)))
            for name in ('objects', 'meshes', 'shape_keys', 'armatures', 'collections')}

    def options(collection):
        return dict(targetCollection=collection.name, selectedOnly=True,
            rotate90=bool(collection['SF6SourceRotate']), exportAllLODs=False,
            exportBlendShapes=True, sf6HybridPreserve=True, splitLoopVertices=False,
            useBlenderMaterialName=False, preserveBoneMatrices=True, exportBoundingBoxes=False,
            autoSolveRepeatedUVs=False, preserveSharpEdges=False,
            limitTotal=True, limitTotalCount=6, normalizeWeights=True)

    def validate(collection, target, name):
        samples, signatures, triangles, polygons, uv, normals = u.evaluate_oracle(target, mesh_io)
        basis_name = target.data.shape_keys.key_blocks[0].name
        basis = samples[basis_name]
        assert all(value == signatures[basis_name] for value in signatures.values())
        before = state(collection), ids()
        path = root / (name + '.mesh.230110883')
        opts = options(collection)
        assert mesh_io.exportREMeshFile(str(path), opts)
        assert (state(collection), ids()) == before, 'Export changed editable state or leaked private IDs'
        result = sf6.SourceMesh(path.read_bytes())
        part = u.lod0_parts(result)[chosen]
        mapping = u.output_row_mapping(result, part, basis, triangles, opts['rotate90'], sf6)
        u.validate_corner_uvs(result, part, uv)
        normal_error = u.validate_evaluated_corner_normals(result, part, normals, opts['rotate90'], sf6)
        actual = {name: delta for name, delta, _ in result.part_shapes(part)}
        assert actual.keys() == set(samples) - {basis_name}
        for shape, coordinates in samples.items():
            if shape == basis_name:
                continue
            expected = sf6._from_blender((coordinates - basis)[mapping], opts['rotate90']).astype('<f2').astype('<f4')
            assert np.array_equal(actual[shape], expected), (shape,
                int(np.count_nonzero(actual[shape] != expected)),
                float(np.max(np.abs(actual[shape] - expected))))
        assert any(np.any(delta) for delta in actual.values()), 'Fixture must contain genuine corrective movement'
        u.validate_target_bounds(result)
        # Resampling the original also detects changed or detached grid data,
        # which is not accessible through ordinary shape-key RNA properties.
        after_samples, after_signatures, *_ = u.evaluate_oracle(target, mesh_io)
        assert after_signatures == signatures
        assert all(np.array_equal(after_samples[key], points) for key, points in samples.items())
        multires = next(m for m in target.modifiers if m.type == 'MULTIRES')
        evaluation = opts['_sf6HybridReport']['evaluated_geometry']['parts'][0]
        modifier_report = next(m for m in evaluation['active_modifiers'] if m['type'] == 'MULTIRES')
        assert modifier_report['viewport_level'] == multires.levels
        assert modifier_report['sculpt_level'] == multires.sculpt_levels
        assert modifier_report['render_level'] == multires.render_levels
        assert modifier_report['stored_levels'] == multires.total_levels
        assert modifier_report['external_displacements'] == multires.is_external
        formatted_report = '\n'.join(reports.format_hybrid_report(opts['_sf6HybridReport']))
        assert ('Multires viewport level ' + str(multires.levels)) in formatted_report
        assert (f'(sculpt {multires.sculpt_levels}, render {multires.render_levels})') in formatted_report
        detail = passed(name, editable_vertices=len(target.data.vertices),
            evaluated_vertices=len(basis), export_vertices=part['count'],
            triangles=part['faces'], evaluated_polygons=polygons, shape_links=len(actual),
            viewport_level=multires.levels, sculpt_level=multires.sculpt_levels,
            render_level=multires.render_levels, total_levels=multires.total_levels,
            external_displacement=multires.is_external,
            max_normal_snorm8_error=normal_error,
            moving_vertices_per_key={name:int(np.count_nonzero(np.any(delta, axis=1)))
                for name,delta in actual.items()},
            original_grid_and_keys_unchanged=True, temporary_datablocks_removed=True,
            viewport_sculpt_render_report_verified=True,
            report=opts['_sf6HybridReport'])
        roundtrips.append((path, detail, opts['rotate90']))
        return path, samples

    def sculpt(target, label):
        return author_multires_sculpt(target, label, mesh_io, u, passed)

    def expect_refusal(collection, target, name, words):
        destination = root / (name + '.mesh.230110883')
        sentinel = ('existing destination survives ' + name).encode()
        destination.write_bytes(sentinel)
        before = state(collection), ids()
        try:
            exported = mesh_io.exportREMeshFile(str(destination), options(collection))
        except ValueError as error:
            reason = str(error)
            assert any(word in reason.lower() for word in words), reason
        else:
            assert exported is False, 'Expected export refusal: ' + name
            reason = 'ordinary export validation refused the evaluated geometry'
        assert destination.read_bytes() == sentinel
        assert (state(collection), ids()) == before
        assert not tuple(root.glob('.sf6-hybrid-*'))
        passed(name, reason=reason, destination_unchanged=True,
            original_scene_unchanged=True, temporary_datablocks_removed=True)

    collection, target = fresh()
    multires, sculpted = sculpt(target, 'full_part')
    target_name, collection_name, multires_name = target.name, collection.name, multires.name
    # Deliberately preserve an active and muted corrective preview, fake-user
    # state, and unusual limits while exporting a clean Basis privately.
    keys = target.data.shape_keys.key_blocks
    keys[1].value, keys[1].mute = .35, True
    keys[1].slider_min, keys[1].slider_max = -.5, .75
    target.active_shape_key_index, target.show_only_shape_key = 1, True
    target.data.use_fake_user = target.data.shape_keys.use_fake_user = True
    embedded_file = root / 'multires-sculpted-embedded.blend'
    before_save = u.evaluate_oracle(target, mesh_io)[0]
    assert bpy.ops.wm.save_as_mainfile(filepath=str(embedded_file)) == {'FINISHED'}
    assert bpy.ops.wm.open_mainfile(filepath=str(embedded_file)) == {'FINISHED'}
    collection, target = bpy.data.collections[collection_name], bpy.data.objects[target_name]
    multires = target.modifiers[multires_name]
    after_save = u.evaluate_oracle(target, mesh_io)[0]
    assert before_save.keys() == after_save.keys()
    assert all(np.array_equal(before_save[key], points) for key, points in after_save.items())
    passed('embedded_sculpt_grids_and_corrective_samples_survive_save_reopen',
        sampled_keys=list(after_save), viewport_vertices=len(after_save['Basis']))
    baseline, _ = validate(collection, target, 'full_part_sculpted_viewport_1_sculpt_2_render_2')
    baseline_bytes = baseline.read_bytes()
    # SF6 stores a Basis and linear additive key deltas. Multires recomputes
    # displacement frames on a deformed surface, so validate key endpoints
    # exactly but measure intermediate and combined samples independently.
    endpoint_samples = u.evaluate_oracle(target, mesh_io)[0]
    keys = target.data.shape_keys.key_blocks
    combinations = [('half_first_key', {keys[1].name: .5})]
    if len(keys) > 2:
        combinations.append(('two_keys_combined', {keys[1].name: .35, keys[2].name: .6}))
    mixed_details = []
    for name, values in combinations:
        clone = target.copy()
        private_mesh = target.data.copy()
        clone.data = private_mesh
        private_mesh.use_fake_user = False
        private_mesh.shape_keys.use_fake_user = False
        bpy.context.scene.collection.objects.link(clone)
        evaluated_mesh = None
        try:
            clone.data.shape_keys.animation_data_clear()
            clone.active_shape_key_index, clone.show_only_shape_key = 0, False
            for key in clone.data.shape_keys.key_blocks:
                key.mute, key.value = False, values.get(key.name, 0)
            for modifier in clone.modifiers:
                if modifier.type == 'ARMATURE':
                    modifier.show_viewport = False
            bpy.context.view_layer.update()
            dg = bpy.context.evaluated_depsgraph_get()
            evaluated_mesh = bpy.data.meshes.new_from_object(clone.evaluated_get(dg),
                preserve_all_data_layers=True, depsgraph=dg)
            actual = u.coords(evaluated_mesh.vertices)
            linear = endpoint_samples['Basis'].copy()
            for key, value in values.items():
                linear += (endpoint_samples[key] - endpoint_samples['Basis']) * value
            difference = np.linalg.norm(actual - linear, axis=1)
            mixed_details.append(dict(sample=name, values=values,
                maximum_position_deviation=float(np.max(difference, initial=0)),
                mean_position_deviation=float(np.mean(difference)),
                vertices_differing_over_one_micrometer=int(np.count_nonzero(difference > 1e-6))))
        finally:
            bpy.data.objects.remove(clone, do_unlink=True)
            for mesh in (evaluated_mesh, private_mesh):
                if mesh is not None and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
    passed('intermediate_and_combined_corrective_deviation_measured', samples=mixed_details,
        exact_blender_equivalence_for_mixed_keys_claimed=False)
    multires.sculpt_levels, multires.render_levels = 0, 0
    lower_levels, _ = validate(collection, target, 'full_part_viewport_1_sculpt_0_render_0')
    assert lower_levels.read_bytes() == baseline_bytes
    passed('export_uses_viewport_level_independent_of_sculpt_and_render_levels', byte_identical=True)
    multires.levels = 0
    level_zero, zero_samples = validate(collection, target, 'full_part_sculpted_viewport_0')
    assert len(zero_samples['Basis']) == len(target.data.vertices)
    multires.levels, multires.sculpt_levels, multires.render_levels = 1, 2, 2

    # External files are a supported Blender storage option. Open the saved
    # project again so the test exercises loading .btx rather than only cached
    # grids retained immediately after writing it.
    external_file = root / 'multires-sculpted-external.blend'
    external_grids = root / 'multires-sculpted.btx'
    u.select([target])
    assert bpy.ops.object.multires_external_save(filepath=str(external_grids),
        modifier=multires.name, relative_path=True) == {'FINISHED'}
    assert multires.is_external and external_grids.is_file()
    assert bpy.ops.wm.save_as_mainfile(filepath=str(external_file)) == {'FINISHED'}
    assert bpy.ops.wm.open_mainfile(filepath=str(external_file)) == {'FINISHED'}
    collection, target = bpy.data.collections[collection_name], bpy.data.objects[target_name]
    multires = target.modifiers[multires_name]
    assert multires.is_external
    external, external_samples = validate(collection, target, 'external_grids_saved_and_reopened')
    assert external.read_bytes() == baseline_bytes
    passed('external_sculpt_displacement_matches_embedded_export', byte_identical=True,
        grid_bytes=external_grids.stat().st_size)
    moved_grids = root / 'multires-sculpted.btx.unavailable'
    external_grids.rename(moved_grids)
    try:
        assert bpy.ops.wm.open_mainfile(filepath=str(external_file)) == {'FINISHED'}
        collection, target = bpy.data.collections[collection_name], bpy.data.objects[target_name]
        expect_refusal(collection, target, 'missing_external_grid_refused_atomically',
            ('external', 'displacement', 'multires'))
    finally:
        moved_grids.rename(external_grids)
    assert bpy.ops.wm.open_mainfile(filepath=str(external_file)) == {'FINISHED'}
    collection, target = bpy.data.collections[collection_name], bpy.data.objects[target_name]
    multires = target.modifiers[multires_name]
    valid_external_bytes = external_grids.read_bytes()
    damaged_header = bytearray(valid_external_bytes)
    damaged_header[:4] = b'FAIL'
    wrong_grid_length = bytearray(valid_external_bytes)
    endian = '<' if valid_external_bytes[4] == 0 else '>'
    header_size, _, layer_count = struct.unpack_from(endian + 'iii', valid_external_bytes, 8)
    mesh_header_size = struct.unpack_from(endian + 'i', valid_external_bytes, header_size)[0]
    position = header_size + mesh_header_size
    modified = False
    for _ in range(layer_count):
        descriptor_size, _, data_size, layer_type = struct.unpack_from(endian + 'iiQi', valid_external_bytes, position)
        if layer_type == 19 and valid_external_bytes[position + 20:position + 84].split(b'\0', 1)[0] == b'':
            assert data_size > 12
            struct.pack_into(endian + 'Q', wrong_grid_length, position + 8, data_size - 12)
            modified = True
            break
        position += descriptor_size
    assert modified
    for name, damaged in (
        ('invalid_external_grid_header_refused_atomically', damaged_header),
        ('truncated_external_grid_payload_refused_atomically', valid_external_bytes[:-12]),
        ('mismatched_external_grid_length_refused_atomically', wrong_grid_length),
    ):
        try:
            external_grids.write_bytes(damaged)
            expect_refusal(collection, target, name, ('external', 'displacement', 'multires'))
        finally:
            external_grids.write_bytes(valid_external_bytes)
    u.select([target])
    assert bpy.ops.object.multires_external_pack() == {'FINISHED'}
    assert not multires.is_external
    packed_file = root / 'multires-sculpted-packed.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(packed_file)) == {'FINISHED'}
    external_grids.rename(moved_grids)
    try:
        assert bpy.ops.wm.open_mainfile(filepath=str(packed_file)) == {'FINISHED'}
        collection, target = bpy.data.collections[collection_name], bpy.data.objects[target_name]
        multires = target.modifiers[multires_name]
        assert not multires.is_external
        packed, _ = validate(collection, target, 'packed_grids_export_with_old_external_path_missing')
        assert packed.read_bytes() == baseline_bytes
    finally:
        moved_grids.rename(external_grids)
    # Export deliberately requires Object Mode; the grid data and existing
    # preview are preserved when a user attempts export while sculpting.
    u.select([target])
    target.active_shape_key_index, target.show_only_shape_key = 0, False
    for key in target.data.shape_keys.key_blocks:
        key.value, key.mute = 0, False
    assert bpy.ops.object.mode_set(mode='SCULPT') == {'FINISHED'}
    try:
        expect_refusal(collection, target, 'sculpt_mode_refused_without_switching_or_writing', ('object mode', 'edit mode', 'sculpt'))
        assert target.mode == 'SCULPT'
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')

    # A compact source-derived region makes level-2 geometry practical while
    # retaining the original target names, UVs, weights and real corrective.
    collection, target = fresh()
    keys = target.data.shape_keys.key_blocks
    focus = max(keys[1:], key=lambda key:float(np.max(np.linalg.norm(
        u.coords(key.data) - u.coords(keys[0].data), axis=1))))
    delta = u.coords(focus.data) - u.coords(keys[0].data)
    center = int(np.argmax(np.linalg.norm(delta, axis=1)))
    points = u.coords(target.data.vertices)
    keep = set(np.argsort(np.linalg.norm(points - points[center], axis=1))[:120].tolist())
    u.select([target])
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(target.data)
    bm.verts.ensure_lookup_table()
    for vertex in bm.verts:
        vertex.select = vertex.index not in keep
    bpy.ops.mesh.delete(type='VERT')
    bpy.ops.object.mode_set(mode='OBJECT')
    used = {index for face in target.data.polygons for index in face.vertices}
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(target.data)
    bm.verts.ensure_lookup_table()
    for vertex in bm.verts:
        vertex.select = vertex.index not in used
    bpy.ops.mesh.delete(type='VERT')
    bpy.ops.object.mode_set(mode='OBJECT')
    assert 3 <= len(target.data.vertices) < 200
    assert np.any(u.coords(focus.data) - u.coords(keys[0].data))
    multires, _ = sculpt(target, 'source_patch')
    multires.levels, multires.sculpt_levels, multires.render_levels = 2, 1, 0
    validate(collection, target, 'source_patch_sculpted_viewport_2_sculpt_1_render_0')
    smooth = target.modifiers.new('Smooth after sculpted Multires', 'SMOOTH')
    smooth.factor, smooth.iterations = .15, 2
    try:
        validate(collection, target, 'source_patch_sculpted_multires_then_smooth')
    finally:
        target.modifiers.remove(smooth)

    for path, detail, rotate in roundtrips:
        expected_bytes = path.read_bytes()
        assert mesh_io.importREMeshFile(str(path), u.IMPORT_OPTIONS.copy())
        collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        meshes = [obj for obj in collection.all_objects if obj.type == 'MESH']
        assert len(meshes) == 1
        assert len(meshes[0].data.shape_keys.key_blocks) == detail['shape_links'] + 1
        destination = path.with_name(path.name.replace('.mesh.', '-roundtrip.mesh.'))
        assert mesh_io.exportREMeshFile(str(destination), dict(targetCollection=collection.name,
            rotate90=rotate, exportBlendShapes=True))
        assert destination.read_bytes() == expected_bytes, path.name
        detail['strict_reimport_roundtrip_byte_identical'] = True
    passed('all_sculpted_exports_reimport_keys_and_strict_roundtrip_bytes', files=len(roundtrips))
    print('ALL_HYBRID_MULTIRES_TESTS_PASSED ' + str(len(results)), flush=True)


if __name__ == '__main__':
    main()
