"""Fixture-neutral focused Mirror/Solidify hybrid export checks.

Run in background Blender with -- --source-file ORIGINAL.mesh.230110883.
A shaped source part with proven normal encoding and paired L_/R_ deform groups
is required. The fixture and retained exports are supplied by the tester.
"""
import argparse
import importlib
import json
from pathlib import Path
import runpy
import sys
import tempfile
import addon_utils
import bmesh
import bpy
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--report-json', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name+'.modules.mesh.blender_re_mesh')
    sf6 = importlib.import_module(repo.name+'.modules.mesh.sf6_source')
    helpers = runpy.run_path(str(repo/'tests/test_sf6_hybrid_modifiers.py'), run_name='probe_helpers')
    assert mesh_io.importREMeshFile(str(args.source_file), helpers['IMPORT_OPTIONS'])
    collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
    rotate = bool(collection['SF6SourceRotate'])
    hybrid = importlib.import_module(repo.name+'.modules.mesh.sf6_hybrid')
    source_mesh = sf6.SourceMesh(args.source_file.read_bytes())
    vp, fp = hybrid._normal_pointers(source_mesh)
    first = next(part for part in source_mesh.parts if part['lod'] == 0)
    supported = []
    for obj in collection.all_objects:
        if obj.type != 'MESH' or not obj.data.shape_keys or not obj.get('SF6SourceMeta'):
            continue
        if not any(name.startswith('L_') for name in obj.vertex_groups.keys()) or not any(name.startswith('R_') for name in obj.vertex_groups.keys()):
            continue
        part = json.loads(obj['SF6SourceMeta'])
        if part['lod'] != 0:
            continue
        expected_v, expected_f = hybrid._synthesize_normals(source_mesh, part, first['start'])
        original_v = np.frombuffer(source_mesh.data, '<u4', part['count'], vp+(part['start']-first['start'])*4)
        original_f = np.frombuffer(source_mesh.data, '<u4', hybrid._padded_indices(part), fp+(part['face_start']-first['face_start'])*4)
        if np.array_equal(expected_v, original_v) and np.array_equal(expected_f, original_f):
            supported.append(obj)
    assert supported, 'Fixture requires a shaped part with paired side groups and proven normal encoding'
    target = min(supported, key=lambda obj:len(obj.data.vertices))
    helpers['select']([target])
    if args.output_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix='sf6-hybrid-generate-modifier-tests-')
        args.output_dir = Path(temporary.name)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
    results = []
    options = dict(targetCollection=collection.name, rotate90=rotate, selectedOnly=True,
                   exportAllLODs=False, splitLoopVertices=False, useBlenderMaterialName=False,
                   preserveBoneMatrices=True, exportBoundingBoxes=False, autoSolveRepeatedUVs=False,
                   preserveSharpEdges=False, limitTotal=True, limitTotalCount=6, normalizeWeights=True)
    def datablock_state():
        return {name:tuple(sorted((item.name_full, item.as_pointer()) for item in getattr(bpy.data, name)))
                for name in ('objects', 'meshes', 'armatures', 'shape_keys', 'collections')}

    def passed(name, **details):
        record = dict(test=name, passed=True, **details)
        results.append(record)
        print('PASS '+json.dumps(record), flush=True)
        (args.report_json or args.output_dir/'mirror-solidify-results.json').write_text(json.dumps(results, indent=2)+'\n')

    def validate(name, compare_ordinary=True):
        case = args.output_dir/name
        case.mkdir(exist_ok=True)
        keys = target.data.shape_keys.key_blocks
        samples, signatures, triangles, polygons, uvs, normals = helpers['evaluate_oracle'](target, mesh_io)
        assert all(signature == signatures[keys[0].name] for signature in signatures.values())
        basis = samples[keys[0].name]
        before = helpers['scene_state'](collection)
        ordinary = case/'ordinary.mesh.230110883'
        if compare_ordinary:
            with helpers['zero_values'](collection):
                assert mesh_io.exportREMeshFile(str(ordinary), dict(options, exportBlendShapes=False))
            assert helpers['scene_state'](collection) == before
        # The legacy ordinary oracle can retain zero-user meshes. Capture the
        # IDs after that separate path; the hybrid adapter preserves existing
        # orphans while removing only its own temporary IDs.
        before_ids = datablock_state()
        destination = case/'hybrid.mesh.230110883'
        settings = dict(options, exportBlendShapes=True, sf6HybridPreserve=True)
        assert mesh_io.exportREMeshFile(str(destination), settings)
        assert helpers['scene_state'](collection) == before
        assert datablock_state() == before_ids, 'Export leaked or changed mesh/key/armature/collection datablocks'
        exported = sf6.SourceMesh(destination.read_bytes())
        part = exported.parts[0]
        mapping = helpers['output_row_mapping'](exported, part, basis, triangles, rotate, sf6)
        helpers['validate_corner_uvs'](exported, part, uvs)
        max_normal_error = helpers['validate_evaluated_corner_normals'](exported, part, normals, rotate, sf6)
        actual = {name:delta for name,delta,_ in exported.part_shapes(part)}
        assert actual.keys() == set(samples)-{keys[0].name}
        for shape, points in samples.items():
            if shape == keys[0].name:
                continue
            desired = sf6._from_blender((points-basis)[mapping], rotate).astype('<f2').astype('<f4')
            assert np.array_equal(actual[shape], desired), shape
        if compare_ordinary:
            # Positions, indices, weights, UVs and colors are independently
            # written by the ordinary path. Normals retain evaluated shading.
            helpers['geometry_equal'](sf6.SourceMesh(ordinary.read_bytes()), exported, compare_shading=False)
        helpers['validate_target_bounds'](exported)
        passed(name, target=target.name, editable_vertices=len(target.data.vertices),
               evaluated_vertices=len(basis), export_vertices=part['count'], triangles=part['faces'],
               shape_links=len(actual), max_normal_snorm8_error=max_normal_error,
               geometry_uv_weights_match_ordinary=compare_ordinary, original_scene_unchanged=True,
               temporary_datablocks_removed=True,
               shape_key_names=list(actual),
               moving_vertices_per_key={name:int(np.count_nonzero(np.any(delta, axis=1))) for name,delta in actual.items()},
               report=settings['_sf6HybridReport'])
        return destination, exported, basis, samples

    mirror = target.modifiers.new('Focus Mirror Merge Clip', 'MIRROR')
    mirror.use_axis = (True, False, False)
    mirror.use_mirror_merge = True
    mirror.merge_threshold = .0001
    mirror.use_clip = True
    mirror.use_mirror_vertex_groups = True
    validate('mirror_full_part_merge_clipping_enabled')
    mirror.use_mirror_u = True
    mirror.use_mirror_v = True
    validate('mirror_full_part_uv_flip_u_and_v')
    target.modifiers.remove(mirror)

    # Keep a small source-derived patch where the left corrective moves. This
    # lets Solidify exercise real rim construction within the game's palette.
    keys = target.data.shape_keys.key_blocks
    moving_keys = [key for key in keys[1:] if np.any(helpers['coords'](key.data)-helpers['coords'](keys[0].data))]
    assert moving_keys, 'Fixture requires at least one nonzero corrective key'
    focus_key = next((key for key in moving_keys if '.L_' in key.name), moving_keys[0])
    delta = helpers['coords'](focus_key.data)-helpers['coords'](keys[0].data)
    center = int(np.argmax(np.linalg.norm(delta, axis=1)))
    points = helpers['coords'](target.data.vertices)
    # A compact spatial region supplies a more realistic open rim than one
    # isolated triangle. Selection is only test construction; all export
    # correspondence below still comes from explicit triangle corner indices.
    keep = set(np.argsort(np.linalg.norm(points-points[center], axis=1))[:120].tolist())
    target.active_shape_key_index = 0
    target.show_only_shape_key = False
    for key in keys:
        key.value = 0
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(target.data)
    bm.verts.ensure_lookup_table()
    for vertex in bm.verts:
        vertex.select = vertex.index not in keep
    bpy.ops.mesh.delete(type='VERT')
    bpy.ops.object.mode_set(mode='OBJECT')
    used = {vi for face in target.data.polygons for vi in face.vertices}
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(target.data)
    bm.verts.ensure_lookup_table()
    for vertex in bm.verts:
        vertex.select = vertex.index not in used
    bpy.ops.mesh.delete(type='VERT')
    bpy.ops.object.mode_set(mode='OBJECT')
    assert 3 <= len(target.data.vertices) < 200
    assert len(target.data.shape_keys.key_blocks[0].data) == len(target.data.vertices)
    assert np.any(helpers['coords'](focus_key.data)-helpers['coords'](keys[0].data))
    patch_vertices = len(target.data.vertices)

    for mode in ('EXTRUDE', 'NON_MANIFOLD'):
        solidify = target.modifiers.new('Focus Solidify Rims '+mode, 'SOLIDIFY')
        solidify.solidify_mode = mode
        solidify.thickness, solidify.offset = .005, 0
        solidify.use_rim = True
        solidify.use_even_offset = True
        try:
            validate('solidify_rims_even_offset_'+mode.lower())
        finally:
            target.modifiers.remove(solidify)

    # Make an actual two-vertex center seam without relying on distance-based
    # matching. All keys keep that seam on the plane for the stable case.
    face_edges = {}
    for face in target.data.polygons:
        for edge in face.edge_keys:
            face_edges[edge] = face_edges.get(edge, 0)+1
    boundary = [edge for edge,count in face_edges.items() if count == 1]
    assert boundary
    seam = min(boundary, key=lambda edge:sum(target.data.vertices[vi].co.x for vi in edge))
    min_x = min(vertex.co.x for vertex in target.data.vertices)
    for key in keys:
        for vi, point in enumerate(key.data):
            point.co.x -= min_x-.03
            if vi in seam:
                point.co.x = 0
    for vertex in target.data.vertices:
        vertex.co = keys[0].data[vertex.index].co
    target.data.update()
    mirror = target.modifiers.new('Focus Mirror Actual Seam', 'MIRROR')
    mirror.use_axis = (True, False, False)
    mirror.use_mirror_merge = True
    mirror.merge_threshold = .0001
    mirror.use_clip = True
    mirror.use_mirror_vertex_groups = True
    stable_path, stable_export, basis, samples = validate('mirror_actual_center_seam_merge_and_clip')
    assert len(basis) == patch_vertices*2-len(seam), (len(basis), patch_vertices, seam)
    left_key_name = focus_key.name
    keyed_delta = samples[left_key_name]-basis
    assert np.any(keyed_delta[basis[:,0] > .0001]) and np.any(keyed_delta[basis[:,0] < -.0001])
    passed('mirror_unilateral_key_retains_name_and_moves_both_sides',
           shape_name=left_key_name, positive_side_moving_vertices=int(np.count_nonzero(np.any(keyed_delta[basis[:,0] > .0001],axis=1))),
           negative_side_moving_vertices=int(np.count_nonzero(np.any(keyed_delta[basis[:,0] < -.0001],axis=1))),
           note='Mirror samples each existing key; it does not create or retarget opposite-side corrective target names.')

    solidify = target.modifiers.new('Focus Mirror Solidify Rims Stack', 'SOLIDIFY')
    solidify.thickness, solidify.offset = .005, 0
    solidify.use_rim = True
    solidify.use_even_offset = True
    try:
        validate('mirror_merge_clip_then_solidify_rims_stack')
    finally:
        target.modifiers.remove(solidify)

    clone = target.copy()
    private_mesh = target.data.copy()
    clone.data = private_mesh
    bpy.context.scene.collection.objects.link(clone)
    try:
        for key in private_mesh.shape_keys.key_blocks:
            key.value = 0
        clone.active_shape_key_index = 0
        clone.show_only_shape_key = True
        for mod in clone.modifiers:
            if mod.type == 'ARMATURE':
                mod.show_viewport = False
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        evaluated_mesh = bpy.data.meshes.new_from_object(clone.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
        try:
            weight_rows = [{clone.vertex_groups[g.group].name: g.weight for g in vertex.groups} for vertex in evaluated_mesh.vertices]
            original_rows = [{target.vertex_groups[g.group].name:g.weight for g in vertex.groups} for vertex in target.data.vertices]
            # Mirror's first output run is the original patch; generated rows
            # correspond to all original rows outside the two merged vertices.
            index = patch_vertices
            flipped = 0
            for vi in range(patch_vertices):
                if vi in seam:
                    continue
                expected = {('R_'+name[2:] if name.startswith('L_') else 'L_'+name[2:] if name.startswith('R_') else name):weight for name,weight in original_rows[vi].items()}
                assert weight_rows[index] == expected, (vi, original_rows[vi], weight_rows[index], expected)
                flipped += sum(name.startswith(('L_', 'R_')) for name in original_rows[vi])
                index += 1
            assert flipped > 0
            passed('mirror_prefix_vertex_group_weights_swap_sides', mirrored_vertices=patch_vertices-len(seam),
                   flipped_nonzero_group_entries=flipped, mirror_vertex_groups=True)
        finally:
            bpy.data.meshes.remove(evaluated_mesh)
    finally:
        bpy.data.objects.remove(clone, do_unlink=True)
        if private_mesh.users == 0:
            bpy.data.meshes.remove(private_mesh)

    # One key leaves the merge plane: independent layout and counts must differ,
    # and export must reject atomically without editing the original project.
    focus_key.data[seam[0]].co.x = .01
    samples, signatures, *_ = helpers['evaluate_oracle'](target, mesh_io)
    assert len(samples[focus_key.name]) != len(samples[keys[0].name]) or signatures[focus_key.name] != signatures[keys[0].name]
    sentinel = b'existing destination must survive shape-dependent Mirror merge'
    destination = args.output_dir/'mirror-dynamic-merge-rejected.mesh.230110883'
    destination.write_bytes(sentinel)
    before = helpers['scene_state'](collection)
    before_ids = datablock_state()
    try:
        mesh_io.exportREMeshFile(str(destination), dict(options, exportBlendShapes=True, sf6HybridPreserve=True))
    except ValueError as error:
        reason = str(error)
        assert 'topology' in reason.lower() or 'vertex' in reason.lower()
    else:
        raise AssertionError('Shape-dependent Mirror merge must reject')
    assert helpers['scene_state'](collection) == before
    assert datablock_state() == before_ids, 'Rejected export leaked temporary datablocks'
    assert destination.read_bytes() == sentinel
    assert not tuple(args.output_dir.rglob('.sf6-hybrid-*')), 'Hybrid staging directory leaked'
    passed('mirror_shape_dependent_merge_rejected_atomically', reason=reason, basis_vertices=len(samples[keys[0].name]),
           changed_key_vertices=len(samples[focus_key.name]), destination_unchanged=True, original_scene_unchanged=True,
           temporary_datablocks_removed=True)

    # A fresh import of the valid real seam export must preserve all shapes and
    # roundtrip strictly. The probe's temporary edits end with process exit.
    expected = stable_path.read_bytes()
    assert mesh_io.importREMeshFile(str(stable_path), helpers['IMPORT_OPTIONS'])
    imported = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
    destination = args.output_dir/'mirror-seam-strict-roundtrip.mesh.230110883'
    assert mesh_io.exportREMeshFile(str(destination), dict(targetCollection=imported.name, rotate90=rotate, exportBlendShapes=True))
    assert destination.read_bytes() == expected
    passed('mirror_actual_seam_reimport_strict_roundtrip_bytes', byte_identical=True)
    print('ALL_HYBRID_GENERATE_MODIFIER_TESTS_PASSED '+str(len(results)), flush=True)


if __name__ == '__main__':
    main()
