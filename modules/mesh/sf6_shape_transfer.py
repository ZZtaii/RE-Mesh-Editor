"""Transfer SF6 body shape displacement onto added vertices of an edited mesh.

The target's retail vertices are identified by *verified triangles*, not merely
by the presence of importer ID attributes.  Blender initializes new integer
attributes to zero, so a copied/modeled vertex can otherwise look like source
vertex zero.  This module changes shape keys in Blender only; export support
for nonzero deltas on rebuilt vertices is a separate concern.
"""

import base64
import binascii
import hashlib
import json
import re
import zlib

import numpy as np

from . import sf6_source


class ShapeTransferError(ValueError):
    """An unsafe or ambiguous shape-key transfer request."""


_BODY_PREFIX = re.compile(r'^BS_Body\d+\.')
_LEG_SUFFIX = re.compile(r'(?:^|_)(?:Knee|Thigh)(?:_|$)', re.IGNORECASE)


def semantic_shape_name(name):
    """Treat BS_Body00.R_Knee and BS_Body37.R_Knee as the same correction."""
    return _BODY_PREFIX.sub('', name, count=1)


def _need(condition, message):
    if not condition:
        raise ShapeTransferError('SF6 shape transfer: ' + message)


def _coords(block):
    coords = np.empty((len(block), 3), dtype=np.float64)
    block.foreach_get('co', coords.ravel())
    _need(np.isfinite(coords).all(), 'non-finite mesh or shape-key coordinates')
    return coords


def _ids(mesh, name, domain, count):
    attr = mesh.attributes.get(name)
    _need(attr is not None and attr.domain == domain and attr.data_type == 'INT'
          and len(attr.data) == count, 'missing or changed ' + name + ' provenance')
    values = np.empty(count, dtype=np.int32)
    attr.data.foreach_get('value', values)
    return values


def _embedded_source(target):
    """Find the exact C2 source stored on the target's imported collection."""
    import bpy

    source_hash = target.get('SF6SourceSHA256')
    raw_meta = target.get('SF6SourceMeta')
    _need(source_hash is not None and raw_meta is not None,
          'target needs SF6 source metadata; provide target_vertex_indices for another mesh')
    try:
        part = json.loads(raw_meta)
    except (TypeError, ValueError) as exc:
        raise ShapeTransferError('SF6 shape transfer: invalid target source metadata') from exc
    matches = [collection for collection in bpy.data.collections
               if collection.get('SF6SourceSHA256') == source_hash
               and target.name in collection.all_objects]
    _need(len(matches) == 1, 'cannot uniquely find the target source collection')
    source_name = matches[0].get(sf6_source.SOURCE)
    source_text = bpy.data.texts.get(source_name) if source_name else None
    _need(source_text is not None, 'embedded target source bytes are missing')
    try:
        source_bytes = zlib.decompress(base64.b64decode(source_text.as_string()))
    except (ValueError, binascii.Error, zlib.error) as exc:
        raise ShapeTransferError('SF6 shape transfer: embedded source cannot be decoded') from exc
    _need(hashlib.sha256(source_bytes).hexdigest() == source_hash,
          'embedded source hash differs from target source identity')
    try:
        source = sf6_source.SourceMesh(source_bytes)
    except (ValueError, IndexError) as exc:
        raise ShapeTransferError('SF6 shape transfer: embedded source is invalid') from exc
    _need(part in source.parts and part['lod'] == 0,
          'target is not an imported LOD0 source part')
    return source, part


def _verified_source_vertices(target):
    """Return target vertices proved by a retail face and all three IDs."""
    source, part = _embedded_source(target)
    mesh = target.data
    vertex_ids = _ids(mesh, sf6_source.VERTEX_ID, 'POINT', len(mesh.vertices))
    face_ids = _ids(mesh, sf6_source.FACE_ID, 'FACE', len(mesh.polygons))
    old_faces = source.faces(part)
    mapped = {}
    seen_faces = set()
    for face_index, polygon in enumerate(mesh.polygons):
        if len(polygon.vertices) != 3:
            continue
        source_face = int(face_ids[face_index])
        if source_face < 0 or source_face >= len(old_faces):
            continue
        vertices = tuple(polygon.vertices)
        ids = vertex_ids[list(vertices)]
        if np.any(ids < 0) or np.any(ids >= part['count']):
            continue
        if not np.array_equal(ids, old_faces[source_face]):
            continue
        _need(source_face not in seen_faces,
              'duplicate retail face identity on ' + target.name)
        seen_faces.add(source_face)
        for vertex, source_id in zip(vertices, ids):
            prior = mapped.get(vertex)
            _need(prior is None or prior == int(source_id),
                  'conflicting source vertex identity on ' + target.name)
            mapped[vertex] = int(source_id)
    _need(len(set(mapped.values())) == len(mapped),
          'ambiguous duplicated source vertices on ' + target.name)
    _need(mapped, 'no retail target triangles could be verified')
    return set(mapped)


def _shape_map(donor, target, explicit, leg_only, target_shape_names):
    donor_keys = list(donor.data.shape_keys.key_blocks)[1:]
    all_target_keys = list(target.data.shape_keys.key_blocks)[1:]
    donor_by_name = {key.name: key for key in donor_keys}
    target_by_name = {key.name: key for key in all_target_keys}
    if target_shape_names is None:
        selected_names = set(target_by_name)
    else:
        selected_names = set(target_shape_names)
        _need(selected_names <= target_by_name.keys(),
              'target_shape_names contains a key absent from the target')
    if leg_only:
        selected_names = {name for name in selected_names
                          if _LEG_SUFFIX.search(semantic_shape_name(name))}
    target_keys = [key for key in all_target_keys if key.name in selected_names]
    skipped_target = [key.name for key in all_target_keys if key.name not in selected_names]
    donor_by_semantic = {}
    for key in donor_keys:
        donor_by_semantic.setdefault(semantic_shape_name(key.name), []).append(key)
    target_by_semantic = {}
    for key in target_keys:
        target_by_semantic.setdefault(semantic_shape_name(key.name), []).append(key)

    explicit = explicit or {}
    _need(isinstance(explicit, dict), 'shape_name_map must be a target-to-donor dictionary')
    _need(all(name in selected_names and donor_name in donor_by_name
              for name, donor_name in explicit.items()),
          'shape_name_map names must exist and be selected on the two meshes')
    matches = []
    used_donor = set()
    for target_key in target_keys:
        if target_key.name in explicit:
            donor_key = donor_by_name[explicit[target_key.name]]
        else:
            semantic = semantic_shape_name(target_key.name)
            choices = donor_by_semantic.get(semantic, ())
            _need(len(choices) <= 1 and len(target_by_semantic[semantic]) <= 1,
                  'ambiguous body shape suffix ' + semantic)
            donor_key = choices[0] if choices else None
        if donor_key is not None:
            _need(donor_key.name not in used_donor,
                  'one donor shape was mapped to multiple target shapes: ' + donor_key.name)
            used_donor.add(donor_key.name)
            matches.append((target_key, donor_key))
    unmatched_target = [key.name for key in target_keys
                        if key.name not in {target_key.name for target_key, _ in matches}]
    unmatched_donor = [key.name for key in donor_keys if key.name not in used_donor]
    return matches, unmatched_target, unmatched_donor, skipped_target


def _world_coords(coords, matrix):
    transform = np.array(matrix, dtype=np.float64)
    homogeneous = np.concatenate((coords, np.ones((len(coords), 1))), axis=1)
    return (homogeneous @ transform.T)[:, :3]


def _vertex_normals(mesh, points):
    """Area-weighted target normals from its Basis, ignoring active key values."""
    normals = np.zeros_like(points)
    for polygon in mesh.polygons:
        indices = tuple(polygon.vertices)
        if len(indices) < 3:
            continue
        origin = points[indices[0]]
        for corner in range(1, len(indices) - 1):
            normal = np.cross(points[indices[corner]] - origin,
                              points[indices[corner + 1]] - origin)
            for index in (indices[0], indices[corner], indices[corner + 1]):
                normals[index] += normal
    length = np.linalg.norm(normals, axis=1)
    good = length > 1e-12
    normals[good] /= length[good, None]
    return normals, good


def _barycentric(point, a, b, c):
    v0, v1, v2 = b - a, c - a, point - a
    d00, d01, d11 = np.dot(v0, v0), np.dot(v0, v1), np.dot(v1, v1)
    denominator = d00 * d11 - d01 * d01
    if denominator <= 1e-20:
        return None
    d20, d21 = np.dot(v2, v0), np.dot(v2, v1)
    v = (d11 * d20 - d01 * d21) / denominator
    w = (d00 * d21 - d01 * d20) / denominator
    weights = np.maximum(np.array((1 - v - w, v, w)), 0)
    return weights / weights.sum()


def _uv0_vertices(mesh):
    """Get every loop UV0 per vertex; seams can have more than one value."""
    if not mesh.uv_layers or len(mesh.uv_layers[0].data) != len(mesh.loops):
        return None
    loop_vertices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get('vertex_index', loop_vertices)
    uv = np.empty((len(mesh.loops), 2), dtype=np.float32)
    mesh.uv_layers[0].data.foreach_get('uv', uv.ravel())
    if not np.isfinite(uv).all():
        return None
    result = [set() for _ in mesh.vertices]
    for index, value in zip(loop_vertices, uv):
        result[int(index)].add((float(value[0]), float(value[1])))
    return result


def transfer_shape_keys(donor_obj, target_obj, *, max_distance=0.04,
                        min_normal_dot=-0.25, side_tolerance=0.005,
                        target_vertex_indices=None, shape_name_map=None,
                        target_shape_names=None, leg_only=True,
                        require_donor_source=True, dry_run=False):
    """Copy matching Basis-relative deltas onto only rebuilt target vertices.

    Nearest donor *surface* is sampled with barycentric triangle weights in
    world/rest space.  The target's Basis stays unchanged, as do all verified
    retail vertices and unmatched target keys.  A target without SF6 provenance
    must provide explicit ``target_vertex_indices``.  Geometry gates intentionally
    leave distant or mismatched points unchanged, including donor-free regions.
    """
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    _need(donor_obj is not target_obj and donor_obj.type == 'MESH'
          and target_obj.type == 'MESH', 'provide distinct donor and target mesh objects')
    _need(donor_obj.data is not target_obj.data and target_obj.data.users == 1,
          'edited body mesh data is shared; make it single-user before transfer')
    _need(donor_obj.mode == 'OBJECT' and target_obj.mode == 'OBJECT',
          'both meshes must be in Object Mode')
    donor_verified = (len(_verified_source_vertices(donor_obj))
                      if require_donor_source else None)
    _need(max_distance is not None and np.isfinite(max_distance) and max_distance > 0,
          'max_distance must be a positive finite world-space value')
    _need(min_normal_dot is None or
          (np.isfinite(min_normal_dot) and -1 <= min_normal_dot <= 1),
          'min_normal_dot must be between -1 and 1')
    _need(side_tolerance is None or
          (np.isfinite(side_tolerance) and side_tolerance >= 0),
          'side_tolerance must be a nonnegative world-space value')
    _need(donor_obj.data.shape_keys is not None and
          target_obj.data.shape_keys is not None,
          'both meshes need a Basis and shape keys')
    donor_keys = donor_obj.data.shape_keys
    target_keys = target_obj.data.shape_keys
    _need(donor_keys.use_relative and target_keys.use_relative,
          'both meshes need relative shape keys')
    matches, unmatched_target, unmatched_donor, skipped_target = _shape_map(
        donor_obj, target_obj, shape_name_map, leg_only, target_shape_names)
    donor_basis = donor_keys.key_blocks[0]
    target_basis = target_keys.key_blocks[0]
    _need(all(d.relative_key == donor_basis and not d.vertex_group and
              t.relative_key == target_basis and not t.vertex_group
              for t, d in matches),
          'matched keys must be unmasked and Basis-relative')
    _need(matches, 'no donor and target shape keys match by body suffix')

    target_count = len(target_obj.data.vertices)
    if target_vertex_indices is None:
        protected = _verified_source_vertices(target_obj)
        candidates = sorted(set(range(target_count)) - protected)
    else:
        try:
            candidates = sorted(set(int(i) for i in target_vertex_indices))
        except (TypeError, ValueError) as exc:
            raise ShapeTransferError('SF6 shape transfer: invalid target_vertex_indices') from exc
        _need(all(0 <= index < target_count for index in candidates),
              'target_vertex_indices contains an out-of-range vertex')
        protected = set(range(target_count)) - set(candidates)

    donor_base_local = _coords(donor_basis.data)
    target_base_local = _coords(target_basis.data)
    donor_world = _world_coords(donor_base_local, donor_obj.matrix_world)
    target_world = _world_coords(target_base_local, target_obj.matrix_world)
    target_normals, normal_valid = _vertex_normals(target_obj.data, target_world)
    donor_mesh = donor_obj.data
    donor_normals, donor_normal_valid = _vertex_normals(donor_mesh, donor_world)
    donor_shape_deltas = {donor_key.name: _coords(donor_key.data) - donor_base_local
                          for _, donor_key in matches}
    donor_uv = _uv0_vertices(donor_mesh)
    target_uv = _uv0_vertices(target_obj.data)
    uv_to_donor = {}
    if donor_uv is not None and target_uv is not None:
        for donor_index, values in enumerate(donor_uv):
            for value in values:
                uv_to_donor.setdefault(value, set()).add(donor_index)
    donor_mesh.calc_loop_triangles()
    triangles = [tuple(tri.vertices) for tri in donor_mesh.loop_triangles]
    triangles = [tri for tri in triangles if
                 np.linalg.norm(np.cross(donor_world[tri[1]] - donor_world[tri[0]],
                                         donor_world[tri[2]] - donor_world[tri[0]])) > 1e-12]
    _need(triangles, 'donor has no nondegenerate surface triangles')
    bvh = BVHTree.FromPolygons([Vector(p) for p in donor_world], triangles,
                               all_triangles=True)
    midpoint_x = (np.min(donor_world[:, 0]) + np.max(donor_world[:, 0])) / 2
    accepted = []
    rejected = {'distance': 0, 'normal': 0, 'side': 0, 'degenerate': 0}
    distances = []
    uv_exact = 0
    surface_sampled = 0
    uv_ambiguous = 0
    for index in candidates:
        position = target_world[index]
        if uv_to_donor and target_uv[index]:
            uv_candidates = set().union(*(uv_to_donor.get(uv, ())
                                          for uv in target_uv[index]))
            ranked = sorted((float(np.linalg.norm(position - donor_world[di])), di)
                            for di in uv_candidates)
            viable = []
            for distance, donor_index in ranked:
                if distance > max_distance:
                    break
                if side_tolerance is not None:
                    target_x = position[0] - midpoint_x
                    donor_x = donor_world[donor_index, 0] - midpoint_x
                    if (target_x > side_tolerance and donor_x < -side_tolerance or
                            target_x < -side_tolerance and donor_x > side_tolerance):
                        continue
                if min_normal_dot is not None and (not normal_valid[index] or
                        not donor_normal_valid[donor_index] or
                        np.dot(target_normals[index], donor_normals[donor_index])
                        < min_normal_dot):
                    continue
                viable.append((distance, donor_index))
            if viable:
                distance, donor_index = viable[0]
                ties = [other for other_distance, other in viable[1:]
                        if other_distance - distance <= 1e-6]
                if ties:
                    same_position = all(np.linalg.norm(donor_world[other] -
                                                       donor_world[donor_index]) <= 1e-6
                                        for other in ties)
                    same_delta = all(np.linalg.norm(delta[other] - delta[donor_index])
                                     <= 1e-6 for delta in donor_shape_deltas.values()
                                     for other in ties)
                    if not same_position or not same_delta:
                        uv_ambiguous += 1
                    else:
                        accepted.append((index, (donor_index,), np.array((1.0,))))
                        distances.append(distance)
                        uv_exact += 1
                        continue
                else:
                    accepted.append((index, (donor_index,), np.array((1.0,))))
                    distances.append(distance)
                    uv_exact += 1
                    continue
        hit, face_normal, triangle_index, distance = bvh.find_nearest(Vector(position))
        if hit is None or distance is None or distance > max_distance:
            rejected['distance'] += 1
            continue
        if side_tolerance is not None:
            target_x = position[0] - midpoint_x
            donor_x = hit.x - midpoint_x
            if (target_x > side_tolerance and donor_x < -side_tolerance or
                    target_x < -side_tolerance and donor_x > side_tolerance):
                rejected['side'] += 1
                continue
        tri = triangles[triangle_index]
        a, b, c = donor_world[list(tri)]
        normal = np.cross(b - a, c - a)
        length = np.linalg.norm(normal)
        if length <= 1e-12:
            rejected['degenerate'] += 1
            continue
        if min_normal_dot is not None and (not normal_valid[index] or
                np.dot(target_normals[index], normal / length) < min_normal_dot):
            rejected['normal'] += 1
            continue
        weights = _barycentric(np.array(hit), a, b, c)
        if weights is None:
            rejected['degenerate'] += 1
            continue
        accepted.append((index, tri, weights))
        distances.append(float(distance))
        surface_sampled += 1

    donor_linear = np.array(donor_obj.matrix_world, dtype=np.float64)[:3, :3]
    target_linear = np.array(target_obj.matrix_world, dtype=np.float64)[:3, :3]
    try:
        world_to_target = np.linalg.inv(target_linear)
    except np.linalg.LinAlgError as exc:
        raise ShapeTransferError('SF6 shape transfer: target object transform is singular') from exc
    report_shapes = []
    writes = []
    skipped_existing_total = 0
    for target_key, donor_key in matches:
        donor_delta = donor_shape_deltas[donor_key.name]
        donor_delta_world = donor_delta @ donor_linear.T
        target_current = _coords(target_key.data)
        values = []
        nonzero = 0
        skipped_existing = 0
        for index, tri, weights in accepted:
            # A previous transfer or an artist may already have shaped some
            # rebuilt vertices.  Never replace that work implicitly.
            if np.linalg.norm(target_current[index] - target_base_local[index]) > 1e-7:
                skipped_existing += 1
                continue
            delta_world = weights @ donor_delta_world[list(tri)]
            delta_local = delta_world @ world_to_target.T
            position = target_base_local[index] + delta_local
            _need(np.isfinite(position).all(),
                  'non-finite transferred delta for ' + target_key.name)
            nonzero += np.linalg.norm(delta_local) > 1e-7
            values.append((index, position))
        skipped_existing_total += skipped_existing
        writes.append((target_key, values))
        report_shapes.append({'target': target_key.name, 'donor': donor_key.name,
                              'transferred_vertices': len(values),
                              'nonzero_vertices': int(nonzero),
                              'skipped_existing_vertices': skipped_existing})
    if not dry_run:
        for target_key, values in writes:
            for index, position in values:
                target_key.data[index].co = position
        target_obj.data.update()
    return {
        'donor': donor_obj.name, 'target': target_obj.name,
        'donor_source_sha256': donor_obj.get('SF6SourceSHA256'),
        'target_source_sha256': target_obj.get('SF6SourceSHA256'),
        'verified_donor_vertices': donor_verified,
        'verified_source_vertices': len(protected),
        'candidate_vertices': len(candidates),
        'mapped_vertices': len(accepted),
        'uv_exact_vertices': uv_exact,
        'surface_sampled_vertices': surface_sampled,
        'ambiguous_uv_fallback_vertices': uv_ambiguous,
        'rejected': rejected,
        'max_accepted_distance': max(distances, default=0.0),
        'matched_shapes': report_shapes,
        'skipped_existing_shape_vertices': skipped_existing_total,
        'unmatched_target_shapes': unmatched_target,
        'unmatched_donor_shapes': unmatched_donor,
        'skipped_target_shapes': skipped_target,
        'leg_only': bool(leg_only),
        'dry_run': bool(dry_run),
    }
