"""Private, modifier-evaluated Basis/key snapshots for SF6 hybrid export.

The ordinary writer and the hybrid writer must read the same triangulated mesh.
This module samples modifiers on private copies, so an active key, driver, or
animated armature in the user's scene cannot contaminate that mesh. Evaluated
integer provenance is deliberately discarded whenever source identity is no
longer proven; interpolated IDs must never inherit retail corrective deltas.
"""

from contextlib import contextmanager
import json
from pathlib import Path
import struct

import bmesh
import bpy
import numpy as np

from .sf6_hybrid import HybridExportError
from .sf6_source import _is_hip_stub


def _need(condition, message):
    if not condition:
        raise HybridExportError('SF6 hybrid evaluation: ' + message)


def _coords(points):
    result = np.empty((len(points), 3), np.float32)
    points.foreach_get('co', result.ravel())
    return result


def _topology(mesh):
    # Counts alone cannot prove vertex correspondence. Compare the ordered
    # polygon/corner layout and edges before triangulation for every key.
    return (len(mesh.vertices), tuple(tuple(edge.vertices) for edge in mesh.edges),
            tuple(tuple(face.vertices) for face in mesh.polygons))


def _copy_properties(source, destination):
    for name in source.keys():
        destination[name] = source[name]


def _lod0_meshes(collection, selected):
    result = []
    for obj in collection.all_objects:
        if obj.type != 'MESH' or obj.get('~TYPE') or obj.get('MeshExportExclude'):
            continue
        if selected is not None and obj not in selected:
            continue
        _need(obj.mode == 'OBJECT', 'switch to Object Mode before exporting ' + obj.name)
        metadata = obj.get('SF6SourceMeta')
        if metadata is not None:
            try:
                part = json.loads(metadata)
                lod = part['lod']
            except (TypeError, ValueError, KeyError) as error:
                raise HybridExportError(
                    'SF6 hybrid evaluation: invalid source part metadata on ' + obj.name) from error
            if lod != 0:
                continue
        result.append(obj)
    _need(result, 'no LOD0 mesh objects in the target collection')
    return result


def _clear_animation(obj):
    # Clearing animation on copied IDs freezes evaluated modifier settings but
    # removes drivers that could force the private key values back to the pose.
    obj.animation_data_clear()
    if obj.data is not None and hasattr(obj.data, 'animation_data_clear'):
        obj.data.use_fake_user = False
        obj.data.animation_data_clear()
    if obj.type == 'MESH' and obj.data.shape_keys is not None:
        obj.data.shape_keys.use_fake_user = False
        obj.data.shape_keys.animation_data_clear()


def _freeze_transform(obj, matrix, preserve_constraints=False):
    obj.parent = None
    if preserve_constraints:
        # Exporter bounding boxes use a named constraint as bone metadata.
        # Mute evaluation while retaining that name and subtarget.
        for constraint in obj.constraints:
            constraint.mute = True
    else:
        obj.constraints.clear()
    obj.matrix_world = matrix


def _triangulate_basis(mesh):
    """Triangulate once without losing evaluated custom corner normals or rows."""
    if all(len(face.vertices) == 3 for face in mesh.polygons):
        return
    before = _coords(mesh.vertices)
    normals = None
    marker = None
    if mesh.has_custom_normals:
        normals = {
            (face.index, mesh.loops[li].vertex_index): mesh.corner_normals[li].vector.copy()
            for face in mesh.polygons for li in face.loop_indices
        }
        marker_name = '__sf6_evaluated_polygon'
        while mesh.attributes.get(marker_name) is not None:
            marker_name += '_'
        marker = mesh.attributes.new(marker_name, 'INT', 'FACE')
        marker.data.foreach_set('value', np.arange(len(mesh.polygons), dtype=np.int32))
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
    finally:
        bm.free()
    _need(np.array_equal(before, _coords(mesh.vertices)),
          'Basis triangulation changed the evaluated vertex order')
    if normals is not None:
        # BMesh copies FACE data to each new triangle. Its loops still refer to
        # the same original polygon corner, allowing exact split-normal reuse.
        marker = mesh.attributes[marker_name]
        split_normals = [None] * len(mesh.loops)
        for face in mesh.polygons:
            original_face = marker.data[face.index].value
            for li in face.loop_indices:
                split_normals[li] = normals[(original_face, mesh.loops[li].vertex_index)]
        mesh.normals_split_custom_set(split_normals)
        mesh.attributes.remove(marker)
    mesh.update()


def _invalidate_provenance(mesh):
    for name, domain, size in (
            ('sf6_source_vertex', 'POINT', len(mesh.vertices)),
            ('sf6_source_face', 'FACE', len(mesh.polygons))):
        attr = mesh.attributes.get(name)
        if attr is not None and (attr.domain != domain or attr.data_type != 'INT'):
            mesh.attributes.remove(attr)
            attr = None
        if attr is None:
            attr = mesh.attributes.new(name, 'INT', domain)
        attr.data.foreach_set('value', np.full(size, -1, np.int32))


def _ambiguous_corner_rows(mesh, *, include_normals=False, normal_tolerance=1e-5):
    """Find rows needing multiple export corners without modifying the mesh."""
    uv_values = np.empty((len(mesh.loops), len(mesh.uv_layers) * 2), np.float32)
    for layer_index, layer in enumerate(mesh.uv_layers):
        values = np.empty((len(mesh.loops), 2), np.float32)
        layer.data.foreach_get('uv', values.ravel())
        uv_values[:, layer_index * 2:layer_index * 2 + 2] = values
    normals = np.empty((len(mesh.loops), 3), np.float32)
    mesh.corner_normals.foreach_get('vector', normals.ravel())
    first_corner = {}
    ambiguous = set()
    for li, loop in enumerate(mesh.loops):
        signature = tuple(uv_values[li])
        previous = first_corner.setdefault(loop.vertex_index, (signature, normals[li]))
        # Blender compresses custom normals into per-corner normal spaces.
        # Ignore sub-encoding float noise while retaining distinct normal fans.
        normal_differs = include_normals and np.max(np.abs(previous[1] - normals[li])) > normal_tolerance
        if previous[0] != signature or normal_differs:
            ambiguous.add(loop.vertex_index)
    return ambiguous, normals


def _split_basis_uv_corners(mesh, *, include_normals=False, normal_tolerance=1e-5):
    """Split ambiguous Basis corners and return their evaluated row IDs.

    A modifier can produce real seams or tiny distinct corner UV values. The
    legacy geometry writer requires one UV per row. Preserve each authored
    value by splitting the private Basis instead of rounding or guessing which
    corner to keep; copied shape coordinates use the same explicit row map.
    The optional second pass preserves distinct corner normals even when sharp
    splitting is disabled or the custom normal boundary has no sharp flag.
    """
    original_count = len(mesh.vertices)
    identity = np.arange(original_count, dtype=np.int32)
    if not mesh.uv_layers and not include_normals:
        return identity
    ambiguous, normals = _ambiguous_corner_rows(
        mesh, include_normals=include_normals, normal_tolerance=normal_tolerance)
    if not ambiguous:
        return identity
    original_coords = _coords(mesh.vertices)
    # Preserve computed smooth normals too: opening a vertex fan must not turn
    # the new seam into a visible shading seam.
    row_name, corner_name = '__sf6_evaluated_row', '__sf6_evaluated_corner'
    while mesh.attributes.get(row_name) is not None:
        row_name += '_'
    while mesh.attributes.get(corner_name) is not None:
        corner_name += '_'
    row_attr = mesh.attributes.new(row_name, 'INT', 'POINT')
    row_attr.data.foreach_set('value', identity)
    corner_attr = mesh.attributes.new(corner_name, 'INT', 'CORNER')
    corner_attr.data.foreach_set('value', np.arange(len(mesh.loops), dtype=np.int32))
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        row_layer = bm.verts.layers.int[row_name]
        edges = [edge for edge in bm.edges
                 if any(vertex[row_layer] in ambiguous for vertex in edge.verts)]
        bmesh.ops.split_edges(bm, edges=edges)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    rows = np.empty(len(mesh.vertices), np.int32)
    mesh.attributes[row_name].data.foreach_get('value', rows)
    corner_rows = np.empty(len(mesh.loops), np.int32)
    mesh.attributes[corner_name].data.foreach_get('value', corner_rows)
    _need(np.all((rows >= 0) & (rows < original_count)) and
          np.array_equal(_coords(mesh.vertices), original_coords[rows]),
          'corner splitting lost the evaluated vertex row mapping')
    _need(np.all((corner_rows >= 0) & (corner_rows < len(normals))),
          'corner splitting lost the evaluated corner normal mapping')
    mesh.normals_split_custom_set(normals[corner_rows])
    mesh.attributes.remove(mesh.attributes[row_name])
    mesh.attributes.remove(mesh.attributes[corner_name])
    mesh.update()
    return rows


def _attach_sampled_keys(snapshot, payload):
    basis_name, coordinates = payload
    if basis_name is None:
        return
    basis_key = snapshot.shape_key_add(name=basis_name, from_mix=False)
    for name, positions in coordinates:
        key = snapshot.shape_key_add(name=name, from_mix=False)
        key.data.foreach_set('co', positions.ravel())
        key.relative_key = basis_key
        key.value = 0.0
    snapshot.data.shape_keys.use_relative = True
    snapshot.data.shape_keys.use_fake_user = False
    snapshot.data.update()


def _set_private_mode(identifier):
    identifiers = {item.identifier for item in bpy.ops.object.mode_set.get_rna_type().properties['mode'].enum_items}
    _need(identifier in identifiers, 'this Blender build has no object mode ' + identifier)
    bpy.ops.object.mode_set(mode=identifier)


def _legacy_sharp_snapshots(samples):
    """Mirror legacy multi-object Edit Mode conversion on private Basis IDs.

    Merely using from_mesh/to_mesh is not equivalent: Edit Mode converts custom
    normal spaces differently, and all selected meshes participate each time
    the old sharp helper enters/exits mode. No keys are attached until this
    exact legacy geometry pass has finished and its copied rows are known.
    """
    private_objects = {obj for obj, _, _ in samples}
    markers = []
    for obj, report, payload in samples:
        mesh = obj.data
        row_name = '__sf6_evaluated_sharp_row'
        while mesh.attributes.get(row_name) is not None:
            row_name += '_'
        attr = mesh.attributes.new(row_name, 'INT', 'POINT')
        attr.data.foreach_set('value', np.arange(len(mesh.vertices), dtype=np.int32))
        normals, corner_name = None, None
        if not report['source_provenance_preserved']:
            normals = np.empty((len(mesh.loops), 3), np.float32)
            mesh.corner_normals.foreach_get('vector', normals.ravel())
            corner_name = '__sf6_evaluated_sharp_corner'
            while mesh.attributes.get(corner_name) is not None:
                corner_name += '_'
            attr = mesh.attributes.new(corner_name, 'INT', 'CORNER')
            attr.data.foreach_set('value', np.arange(len(mesh.loops), dtype=np.int32))
        markers.append((obj, report, payload, row_name, _coords(mesh.vertices), normals, corner_name))
    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    for obj, _, _ in samples:
        obj.select_set(True)
    try:
        for obj in tuple(bpy.context.selected_objects):
            bpy.context.view_layer.objects.active = obj
            _set_private_mode('EDIT')
            bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
            sharp = [edge for edge in bm.edges if not edge.smooth]
            if sharp:
                bmesh.ops.split_edges(bm, edges=sharp)
            bmesh.update_edit_mesh(bpy.context.edit_object.data)
            _set_private_mode('OBJECT')
    finally:
        active = bpy.context.view_layer.objects.active
        if active in private_objects and active.mode != 'OBJECT':
            _set_private_mode('OBJECT')
    remapped_samples = []
    for obj, report, payload, row_name, original_coords, normals, corner_name in markers:
        mesh = obj.data
        rows = np.empty(len(mesh.vertices), np.int32)
        mesh.attributes[row_name].data.foreach_get('value', rows)
        _need(np.all((rows >= 0) & (rows < len(original_coords))) and
              np.array_equal(_coords(mesh.vertices), original_coords[rows]),
              'sharp-edge preprocessing lost the evaluated vertex row mapping')
        mesh.attributes.remove(mesh.attributes[row_name])
        if normals is not None:
            corner_rows = np.empty(len(mesh.loops), np.int32)
            mesh.attributes[corner_name].data.foreach_get('value', corner_rows)
            _need(np.all((corner_rows >= 0) & (corner_rows < len(normals))),
                  'sharp-edge preprocessing lost the evaluated corner normal mapping')
            mesh.normals_split_custom_set(normals[corner_rows])
            mesh.attributes.remove(mesh.attributes[corner_name])
        added_rows = len(mesh.vertices) - len(original_coords)
        if added_rows:
            _invalidate_provenance(mesh)
            report['source_provenance_preserved'] = False
            report['retained_hip_stub'] = False
        report['sharp_split_vertices'] = added_rows
        report['evaluated_vertices'] = len(mesh.vertices)
        report['evaluated_triangles'] = len(mesh.polygons)
        report['evaluated_shape_vertices'] = (
            len(mesh.vertices) if payload[1] and not report['source_provenance_preserved'] else 0)
        remapped_samples.append((obj, report, (payload[0], [
            (name, coordinates[rows]) for name, coordinates in payload[1]])))
        mesh.update()
    return remapped_samples


def _evaluate_mesh(capture, owned_meshes):
    capture.data.update()
    capture.update_tag(refresh={'OBJECT', 'DATA'})
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(
        capture.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)
    _need(mesh is not None, 'modifier evaluation produced no mesh on ' + capture.name)
    owned_meshes.append(mesh)
    _need(np.isfinite(_coords(mesh.vertices)).all(),
          'modifier evaluation produced nonfinite coordinates on ' + capture.name)
    return mesh


def _private_rest_armature(original, collection, cache, owned_objects, owned_armatures):
    """Keep legacy rest-armature rounding without baking the user's pose."""
    if original in cache:
        return cache[original]
    copied = original.copy()
    owned_objects.append(copied)
    copied.name = '__SF6_REST_' + original.name
    copied.data = original.data.copy()
    owned_armatures.append(copied.data)
    collection.objects.link(copied)
    _clear_animation(copied)
    _freeze_transform(copied, original.matrix_world.copy())
    identifiers = {item.identifier for item in copied.data.bl_rna.properties['pose_position'].enum_items}
    _need('REST' in identifiers, 'this Blender build has no REST armature evaluation mode')
    copied.data.pose_position = 'REST'
    copied.hide_viewport = False
    copied.hide_render = False
    copied.hide_set(False)
    cache[original] = copied
    return copied


def _validate_multires_external(original, modifier):
    path = Path(bpy.path.abspath(modifier.filepath, library=original.data.library))
    remedy = '; restore its displacement file or pack it in Blender before exporting'
    _need(original.data.library is None or not modifier.filepath.startswith('//'),
          'library-relative Multires sculpt data on ' + original.name +
          '; make the mesh local and pack its displacement file before exporting')
    _need(bool(modifier.filepath) and path.is_file(),
          'Multires external sculpt data is missing on ' + original.name + remedy)
    try:
        # Blender's BCDF reader can return without loading grids on a damaged
        # file, leaving evaluation to use zero displacement. Check the declared
        # float grid payload before allowing that silent loss. Follow recorded
        # structure sizes rather than assuming native padding or byte order.
        with path.open('rb') as stream:
            size = path.stat().st_size
            header = stream.read(20)
            _need(len(header) == 20 and header[:4] == b'BCDF' and
                  header[4] in (0, 1) and header[5] == 0,
                  'invalid Multires external sculpt file on ' + original.name + remedy)
            endian = '<' if header[4] == 0 else '>'
            header_size, file_type, layers = struct.unpack(endian + 'iii', header[8:])
            _need(20 <= header_size <= size - 4 and file_type == 1 and
                  0 < layers <= size // 84,
                  'invalid Multires external sculpt header on ' + original.name + remedy)
            stream.seek(header_size)
            mesh_size = struct.unpack(endian + 'i', stream.read(4))[0]
            _need(4 <= mesh_size <= size - header_size,
                  'invalid Multires external mesh header on ' + original.name + remedy)
            stream.seek(header_size + mesh_size)
            layer_data = []
            for _ in range(layers):
                position = stream.tell()
                descriptor = stream.read(84)
                _need(len(descriptor) == 84,
                      'truncated Multires external sculpt header on ' + original.name + remedy)
                entry_size, data_type, data_size, layer_type = struct.unpack(
                    endian + 'iiQi', descriptor[:20])
                _need(84 <= entry_size <= size - position and data_type == 0,
                      'invalid Multires external sculpt layer on ' + original.name + remedy)
                layer_data.append((layer_type, descriptor[20:84].split(b'\0', 1)[0], data_size))
                stream.seek(position + entry_size)
            _need(sum(entry[2] for entry in layer_data) <= size - stream.tell(),
                  'truncated Multires external sculpt data on ' + original.name + remedy)
            grids = [entry[2] for entry in layer_data if entry[:2] == (19, b'')]
            expected_size = len(original.data.loops) * (((1 << max(0, modifier.total_levels - 1)) + 1) ** 2) * 12
            _need(grids == [expected_size],
                  'Multires external sculpt grids do not match the mesh on ' + original.name + remedy)
    except (OSError, struct.error) as error:
        raise HybridExportError('SF6 hybrid evaluation: cannot read Multires external sculpt data on ' +
                                original.name + remedy) from error
    return str(path)


def _active_modifier_report(original):
    result = []
    for modifier in original.modifiers:
        if modifier.type == 'ARMATURE' or not modifier.show_viewport:
            continue
        entry = {'name': modifier.name, 'type': modifier.type}
        if modifier.type == 'MULTIRES':
            # Object-mode export reads the viewport level, not the separately
            # chosen sculpt/render levels. The private mesh retains stored
            # sculpt grids; an external grid file must also remain available.
            if modifier.is_external:
                _validate_multires_external(original, modifier)
            entry.update(viewport_level=modifier.levels,
                         sculpt_level=modifier.sculpt_levels,
                         render_level=modifier.render_levels,
                         stored_levels=modifier.total_levels,
                         external_displacements=modifier.is_external)
            if bpy.context.scene.render.use_simplify:
                entry['simplify_subdivision_cap'] = bpy.context.scene.render.simplify_subdivision
        result.append(entry)
    return result


def _sample_object(original, capture_collection, snapshot_collection,
                   owned_objects, owned_meshes, owned_armatures, rest_armatures,
                   preserve_sharp_edges):
    original_keys = original.data.shape_keys
    if original_keys is not None and len(original_keys.key_blocks) > 1:
        keys = original_keys.key_blocks
        _need(original_keys.use_relative and all(
              key.relative_key == keys[0] and not key.vertex_group for key in list(keys)[1:]),
              'shape keys need unmasked Basis-relative deltas on ' + original.name)
    active_modifiers = _active_modifier_report(original)

    capture = original.copy()
    capture.name = '__SF6_CAPTURE_' + original.name
    capture.data = original.data.copy()
    owned_objects.append(capture)
    owned_meshes.append(capture.data)
    capture_collection.objects.link(capture)
    # Mesh.copy must own its keys; otherwise any temporary values/drivers would
    # affect the user. Fail before touching them if Blender changes this contract.
    _need(original_keys is None or capture.data.shape_keys != original_keys,
          'private mesh copy shares original shape keys on ' + original.name)
    _clear_animation(capture)
    _freeze_transform(capture, original.matrix_world.copy())
    capture.hide_viewport = False
    capture.hide_render = False
    capture.hide_set(False)
    capture.show_only_shape_key = False
    capture.active_shape_key_index = 0
    transformed = not np.allclose(np.asarray(original.matrix_world), np.eye(4), rtol=0, atol=1e-7)
    proven_source = (not active_modifiers and not transformed and
                     all(len(face.vertices) == 3 for face in original.data.polygons))
    if proven_source:
        # Decide before sampling keys: retail REST-armature arithmetic is only
        # compatible while rows remain retail-mapped. Authored corner fans need
        # rebuilt deltas, so their Basis/keys must exclude that extra rounding.
        ambiguous, _ = _ambiguous_corner_rows(
            capture.data, include_normals=True, normal_tolerance=1 / 127)
        proven_source = not ambiguous
    preserve_source_transform = proven_source
    for modifier in capture.modifiers:
        if modifier.type == 'ARMATURE':
            # SF6 exports rest geometry and skin weights. Baking a preview pose
            # here would apply that pose again when the game animates the mesh.
            # A rest rig can still produce sub-ulp transform rounding. Keep
            # that historical arithmetic once for unchanged source geometry,
            # using a private REST rig rather than the original animated rig.
            if proven_source and modifier.show_viewport and modifier.object is not None:
                modifier.object = _private_rest_armature(
                    modifier.object, capture_collection, rest_armatures,
                    owned_objects, owned_armatures)
            else:
                modifier.show_viewport = False
                modifier.show_render = False
    keys = capture.data.shape_keys.key_blocks if capture.data.shape_keys else ()
    for key in keys:
        key.mute = False
        key.slider_min = min(key.slider_min, 0.0)
        key.slider_max = max(key.slider_max, 1.0)
        key.value = 0.0
    basis = _evaluate_mesh(capture, owned_meshes)
    basis_topology = _topology(basis)
    key_coordinates = []
    for key in list(keys)[1:]:
        key.value = 1.0
        shaped = None
        try:
            shaped = _evaluate_mesh(capture, owned_meshes)
            _need(_topology(shaped) == basis_topology,
                  'modifier topology or vertex order changes for ' + original.name + '/' + key.name)
            key_coordinates.append((key.name, _coords(shaped.vertices)))
        finally:
            key.value = 0.0
            if shaped is not None:
                owned_meshes.remove(shaped)
                bpy.data.meshes.remove(shaped)
    retained_hip_stub = not active_modifiers and not transformed and _is_hip_stub(original)
    # The legacy placeholder detector deliberately requires its exact quad.
    # Ordinary export's existing 4-vertex/2-triangle exception handles that one
    # shape. Other geometry is triangulated here before both writers read it.
    if not retained_hip_stub:
        _triangulate_basis(basis)
    row_map = _split_basis_uv_corners(basis)
    corner_split_vertices = len(basis.vertices) - basis_topology[0]
    before_normal_split = len(basis.vertices)
    # Imported source normals can differ slightly after Blender's per-corner
    # compression. Keep the proven retail row layout within one SF6 encoding
    # step, but split meaningful authored fans even without an active modifier.
    normal_rows = _split_basis_uv_corners(
        basis, include_normals=True,
        normal_tolerance=1 / 127 if proven_source and not corner_split_vertices else 1e-5)
    row_map = row_map[normal_rows]
    normal_split_vertices = len(basis.vertices) - before_normal_split
    if corner_split_vertices or normal_split_vertices:
        proven_source = False
        retained_hip_stub = False
    if not proven_source:
        _invalidate_provenance(basis)

    snapshot = original.copy()
    owned_objects.append(snapshot)
    snapshot.data = basis
    # Keep the Group/Sub/material prefix for ordinary export and no-source
    # matching. Blender adds a legal .001 suffix instead of changing that prefix.
    snapshot_collection.objects.link(snapshot)
    _clear_animation(snapshot)
    # Keep the original serialized transform hierarchy on exact source parts.
    # Reparenting/recomposing a nominal identity can change sub-ulp matrix terms
    # that flip packed near-zero tangent components. This is a read-only parent
    # reference; baked snapshots have no Armature modifier or pose deformation.
    if not preserve_source_transform:
        _freeze_transform(snapshot, original.matrix_world.copy())
    snapshot.modifiers.clear()
    snapshot.hide_viewport = False
    snapshot.hide_render = False
    snapshot.hide_set(False)
    snapshot.show_only_shape_key = False
    snapshot.active_shape_key_index = 0
    payload = (keys[0].name if keys else None,
               [(name, coordinates[row_map]) for name, coordinates in key_coordinates])
    if not preserve_sharp_edges:
        _attach_sampled_keys(snapshot, payload)
    basis.update()
    report = {
        'source_name': original.name, 'snapshot_name': snapshot.name,
        'source_vertices': len(original.data.vertices),
        'evaluated_vertices': len(basis.vertices),
        'evaluated_triangles': len(basis.polygons),
        'shape_keys': len(key_coordinates),
        'active_modifiers': active_modifiers,
        'source_provenance_preserved': bool(proven_source),
        'source_transform_preserved': bool(preserve_source_transform),
        'evaluated_shape_vertices': len(basis.vertices) if key_coordinates and not proven_source else 0,
        'retained_hip_stub': bool(retained_hip_stub),
        'corner_split_vertices': corner_split_vertices,
        'normal_split_vertices': normal_split_vertices,
        'sharp_split_vertices': 0,
    }
    return snapshot, report, payload


@contextmanager
def evaluated_hybrid_collection(source_collection, selected_objects=None, *, preserve_sharp_edges=False):
    """Yield an isolated LOD0 export collection, mapped selection, and report.

    The temporary collection contains only the requested LOD0 meshes and copied
    exporter auxiliaries. Both writers must consume it inside the context. Its
    Basis has zero active keys, no modifiers, evaluated data layers/weights, and
    a single triangle layout shared by all corrective coordinates. Scene
    selection and all owned datablocks are restored/removed on success or error.
    """
    selected = None if selected_objects is None else set(selected_objects)
    meshes = _lod0_meshes(source_collection, selected)
    # Collection.all_objects is a live RNA iterator. Linking any copied object
    # can invalidate it even when that object goes into a different collection.
    source_objects = tuple(source_collection.all_objects)
    source_children = tuple(source_collection.children)
    old_selected = tuple(bpy.context.selected_objects)
    old_active = bpy.context.view_layer.objects.active
    old_meshes = set(bpy.data.meshes)
    old_armatures = set(bpy.data.armatures)
    old_objects = set(bpy.data.objects)
    old_collections = set(bpy.data.collections)
    owned_objects, owned_meshes, owned_armatures, owned_collections = [], [], [], []
    owned_auxiliary_data = []
    rest_armatures = {}
    object_map = {}
    try:
        snapshot = bpy.data.collections.new('__SF6_HYBRID_' + source_collection.name)
        owned_collections.append(snapshot)
        _copy_properties(source_collection, snapshot)
        bpy.context.scene.collection.children.link(snapshot)
        lod = bpy.data.collections.new('__SF6_Main Mesh LOD0')
        owned_collections.append(lod)
        lod_sources = sorted((child for child in source_children
                              if 'Main Mesh LOD' in child.name), key=lambda child: child.name)
        if lod_sources:
            _copy_properties(lod_sources[0], lod)
        elif source_collection.get('LOD Distance') is not None:
            lod['LOD Distance'] = source_collection['LOD Distance']
        snapshot.children.link(lod)
        captures = bpy.data.collections.new('__SF6_HYBRID_CAPTURE')
        owned_collections.append(captures)
        bpy.context.scene.collection.children.link(captures)
        reports = []
        samples = []
        for original in meshes:
            copied, part_report, payload = _sample_object(
                original, captures, lod, owned_objects, owned_meshes,
                owned_armatures, rest_armatures, preserve_sharp_edges)
            object_map[original] = copied
            reports.append(part_report)
            samples.append((copied, part_report, payload))
        if preserve_sharp_edges:
            samples = _legacy_sharp_snapshots(samples)
            for copied, _, payload in samples:
                _attach_sampled_keys(copied, payload)
        source_transform_objects = {copied for copied, report, _ in samples
                                    if report['source_transform_preserved']}
        # Armatures must live on the root: ordinary export finds them there.
        # Other objects are copied only when they serve an exporter role (~TYPE).
        auxiliary_collections = {}
        for original_collection in source_children:
            if original_collection.get('~TYPE') != 'RE_MESH_BOUNDING_BOX_COLLECTION':
                continue
            copied_collection = bpy.data.collections.new('__SF6_' + original_collection.name)
            owned_collections.append(copied_collection)
            _copy_properties(original_collection, copied_collection)
            snapshot.children.link(copied_collection)
            for original in original_collection.all_objects:
                auxiliary_collections[original] = copied_collection
        for original in source_objects:
            if original.type != 'ARMATURE' and not original.get('~TYPE'):
                continue
            copied = original.copy()
            owned_objects.append(copied)
            if original.data is not None:
                copied.data = original.data.copy()
                if copied.type == 'MESH':
                    owned_meshes.append(copied.data)
                elif copied.type == 'ARMATURE':
                    owned_armatures.append(copied.data)
                else:
                    owned_auxiliary_data.append(copied.data)
                if original.type == 'MESH' and original.data.shape_keys is not None:
                    _need(copied.data.shape_keys != original.data.shape_keys,
                          'private auxiliary mesh shares original shape keys on ' + original.name)
            auxiliary_collections.get(original, snapshot).objects.link(copied)
            _clear_animation(copied)
            _freeze_transform(copied, original.matrix_world.copy(), preserve_constraints=bool(original.get('~TYPE')))
            copied.hide_viewport = False
            copied.hide_set(False)
            object_map[original] = copied
        for original, copied in object_map.items():
            if copied not in source_transform_objects and original.parent in object_map:
                world = copied.matrix_world.copy()
                copied.parent = object_map[original.parent]
                copied.matrix_world = world
            for constraint in (() if copied in source_transform_objects else copied.constraints):
                if hasattr(constraint, 'target') and constraint.target in object_map:
                    constraint.target = object_map[constraint.target]
        for obj in tuple(bpy.context.selected_objects):
            obj.select_set(False)
        mapped_selected = None if selected is None else tuple(
            object_map[obj] for obj in selected_objects if obj in object_map)
        for copied in (tuple(object_map.values()) if mapped_selected is None else mapped_selected):
            copied.select_set(True)
        bpy.context.view_layer.objects.active = (
            object_map.get(old_active) or next(iter(object_map.values())))
        bpy.context.view_layer.update()
        report = {'mode': 'evaluated_modifiers', 'parts': reports,
                  'modifier_parts': sum(bool(part['active_modifiers']) for part in reports),
                  'evaluated_shape_vertices': sum(part['evaluated_shape_vertices'] for part in reports),
                  'corner_split_vertices': sum(part['corner_split_vertices'] for part in reports),
                  'normal_split_vertices': sum(part['normal_split_vertices'] for part in reports),
                  'sharp_split_vertices': sum(part['sharp_split_vertices'] for part in reports)}
        yield snapshot, mapped_selected, report
    finally:
        # Ordinary export normally removes these itself, but an exception in
        # its geometry pass can bypass that cleanup. Its private clones use a
        # fixed prefix/collection name. Limit cleanup to IDs created here.
        for obj in tuple(bpy.data.objects):
            if obj not in old_objects and obj not in owned_objects and obj.name.startswith('CLN_'):
                bpy.data.objects.remove(obj, do_unlink=True)
        for collection in tuple(bpy.data.collections):
            if (collection not in old_collections and collection not in owned_collections and
                    collection.name == 'clonedMeshes'):
                bpy.data.collections.remove(collection)
        for obj in reversed(owned_objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in reversed(owned_collections):
            bpy.data.collections.remove(collection)
        for mesh in reversed(owned_meshes):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for armature in reversed(owned_armatures):
            if armature.users == 0:
                bpy.data.armatures.remove(armature)
        if owned_auxiliary_data:
            bpy.data.batch_remove(tuple(data for data in owned_auxiliary_data if data.users == 0))
        # The ordinary writer removes clone objects but can leave their mesh
        # IDs orphaned. Only new orphans from this context are disposable; user
        # meshes that were already orphaned on entry are retained.
        for mesh in tuple(bpy.data.meshes):
            if mesh not in old_meshes and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for armature in tuple(bpy.data.armatures):
            if armature not in old_armatures and armature.users == 0:
                bpy.data.armatures.remove(armature)
        for obj in tuple(bpy.context.selected_objects):
            obj.select_set(False)
        for obj in old_selected:
            if obj.name in bpy.context.view_layer.objects:
                obj.select_set(True)
        bpy.context.view_layer.objects.active = old_active
        bpy.context.view_layer.update()
