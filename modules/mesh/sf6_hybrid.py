"""Opt-in SF6 LOD0 hybrid writer for verified source-shaped geometry.

The caller first makes an ordinary export in a temporary file.  This module
keeps that file's geometry and skeleton, then adds original LOD0 shapes where
scene faces prove source vertex identity.  New vertices get zero shape deltas.
It does not claim to preserve the original source layout or lower LODs.
"""

import base64
import binascii
import hashlib
import json
import re
import struct
import zlib

import numpy as np

from . import sf6_source


class HybridExportError(ValueError):
    """An edited scene cannot be encoded without guessing source identity."""


def _need(ok, message):
    if not ok:
        raise HybridExportError('SF6 hybrid export: ' + message)


def _read(data, fmt, offset):
    size = struct.calcsize('<' + fmt)
    _need(0 <= offset and offset + size <= len(data), 'table exceeds file')
    return struct.unpack_from('<' + fmt, data, offset)


def _write(data, fmt, offset, *values):
    _need(0 <= offset and offset + struct.calcsize('<' + fmt) <= len(data),
          'write exceeds file')
    struct.pack_into('<' + fmt, data, offset, *values)


def _append(data, payload, alignment=16):
    _need(alignment > 0 and alignment & (alignment - 1) == 0, 'invalid alignment')
    data.extend(bytes((-len(data)) % alignment))
    pos = len(data)
    data.extend(payload)
    return pos


def _part_key(src, part):
    return part['group'], src.materials[part['material']]


def _parts_by_key(src):
    result = {}
    for part in src.parts:
        if part['lod'] != 0:
            continue
        key = _part_key(src, part)
        _need(key not in result, 'ambiguous LOD0 group/material: ' + repr(key))
        result[key] = part
    return result


def _ids(obj, name, domain, size):
    attr = obj.data.attributes.get(name)
    _need(attr is not None and attr.domain == domain and len(attr.data) == size,
          'missing ' + name + ' on ' + obj.name)
    values = np.empty(size, np.int32)
    attr.data.foreach_get('value', values)
    return values


def _coords(data):
    values = np.empty((len(data), 3), np.float32)
    data.foreach_get('co', values.ravel())
    return values


def _scene_objects(collection, source, source_hash, source_parts, selected=None):
    objects = {}
    no_source = set()
    for obj in collection.all_objects:
        if obj.type != 'MESH' or obj.get('~TYPE') or obj.get('MeshExportExclude'):
            continue
        if selected is not None and obj not in selected:
            continue
        _need(obj.mode == 'OBJECT', 'leave Edit Mode before exporting ' + obj.name)
        raw = obj.get('SF6SourceMeta')
        if raw is None and obj.get('SF6SourceSHA256') is None:
            # A replacement object can lack any retail metadata.  The exact
            # importer-style name is the only safe bridge to an ordinary part.
            match = re.fullmatch(r'Group_(\d+)_Sub_(\d+)__(.+?)(?:\.\d{3})?', obj.name)
            _need(match is not None,
                  'no-source object needs its original Group/Sub/material name: ' + obj.name)
            group, sub, material = int(match[1]), int(match[2]), match[3]
            matches = [key for key, part in source_parts.items()
                       if part['group'] == group and part['sub'] == sub and key[1] == material]
            _need(len(matches) == 1,
                  'no-source object name does not uniquely identify a retail part: ' + obj.name)
            key = matches[0]
            no_source.add(key)
        else:
            _need(raw is not None and obj.get('SF6SourceSHA256') == source_hash,
                  'stale or incomplete source identity on ' + obj.name)
            try:
                part = json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise HybridExportError('SF6 hybrid export: invalid source part metadata on ' + obj.name) from exc
            _need(part in source.parts, 'changed source part metadata on ' + obj.name)
            if part['lod'] != 0:
                continue
            key = _part_key(source, part)
        _need(key in source_parts and key not in objects,
              'duplicated or unexpected source object ' + obj.name)
        objects[key] = obj
    if selected is None:
        _need(objects.keys() == source_parts.keys(),
              'LOD0 source objects changed; hybrid export needs one object per source part')
    else:
        _need(objects, 'no selected LOD0 source meshes in the target collection')
    return objects, no_source


def _verify_source(collection, source_bytes):
    import bpy

    source_hash = hashlib.sha256(source_bytes).hexdigest()
    _need(collection.get('SF6SourceSHA256') == source_hash,
          'source bytes differ from collection source identity')
    name = collection.get('SF6SourceText')
    text = bpy.data.texts.get(name) if name else None
    _need(text is not None, 'missing embedded SF6 source')
    try:
        embedded = zlib.decompress(base64.b64decode(text.as_string()))
    except (ValueError, binascii.Error, zlib.error) as exc:
        raise HybridExportError('SF6 hybrid export: embedded source cannot be decoded') from exc
    _need(embedded == source_bytes, 'embedded SF6 source differs from supplied bytes')
    _need(isinstance(collection.get('SF6SourceRotate'), bool),
          'missing source axis conversion metadata')
    return source_hash


def _analyze_part(source, ordinary, old, new, obj, rotate, no_source=False):
    """Accept provenance only from triangles matching their retail face slot."""
    mesh = obj.data
    key = _part_key(source, old)
    label = obj.name
    _need(len(mesh.vertices) == new['count'], 'ordinary vertex order differs from ' + label)
    basis = _coords(mesh.shape_keys.key_blocks[0].data if mesh.shape_keys else mesh.vertices)
    expected_positions = sf6_source._from_blender(basis, rotate)
    _need(np.isfinite(expected_positions).all() and
          np.max(np.abs(expected_positions - ordinary.positions(new)), initial=0) <= 1e-5,
          'ordinary positions differ from Blender object ' + label)
    vids = None if no_source else _ids(obj, 'sf6_source_vertex', 'POINT', len(mesh.vertices))
    fids = None if no_source else _ids(obj, 'sf6_source_face', 'FACE', len(mesh.polygons))
    old_faces = source.faces(old)
    new_faces = ordinary.faces(new)
    mapped_faces = {}
    mapped_vertices = {}
    seen_source_faces = set()
    # Blender's ordinary exporter triangulates quads.  Such faces cannot prove
    # identity against a retail triangle and remain rebuilt geometry.
    if no_source:
        if len(mesh.polygons) == new['faces'] and all(len(p.vertices) == 3 for p in mesh.polygons):
            _need(all(tuple(new_faces[fi]) == tuple(poly.vertices)
                      for fi, poly in enumerate(mesh.polygons)),
                  'ordinary triangles differ from no-source object ' + label)
        else:
            _need(sf6_source._is_hip_stub(obj) and new['count'] == 4 and new['faces'] == 2,
                  'no-source nontriangle geometry is unsupported: ' + label)
    elif len(mesh.polygons) == new['faces']:
        for fi, poly in enumerate(mesh.polygons):
            if len(poly.vertices) != 3:
                continue
            verts = tuple(poly.vertices)
            _need(tuple(new_faces[fi]) == verts,
                  'ordinary triangle order differs from Blender object ' + label)
            fid = int(fids[fi])
            if not 0 <= fid < old['faces']:
                continue
            ids = vids[list(verts)]
            if np.any(ids < 0) or np.any(ids >= old['count']):
                continue
            if not np.array_equal(ids, old_faces[fid]):
                continue
            _need(fid not in seen_source_faces,
                  'ambiguous duplicate retail face identity on ' + label)
            seen_source_faces.add(fid)
            mapped_faces[fi] = fid
            for vi, source_id in zip(verts, ids):
                prior = mapped_vertices.get(vi)
                _need(prior is None or prior == int(source_id),
                      'conflicting source vertex identity on ' + label)
                mapped_vertices[vi] = int(source_id)
    else:
        _need(sf6_source._is_hip_stub(obj) and new['count'] == 4 and new['faces'] == 2 and
              not list(source.part_shapes(old)),
              'unsupported nontriangle source part: ' + label)
    _need(len(set(mapped_vertices.values())) == len(mapped_vertices),
          'ambiguous duplicate mapped source vertex on ' + label)
    # A retail face ID collision in added geometry must never inherit a source
    # delta.  Only vertices touched by verified retail triangles are mapped.
    expected_shapes = {n: d for n, d, _ in source.part_shapes(old)}
    keys = mesh.shape_keys.key_blocks if mesh.shape_keys else None
    actual = set(k.name for k in list(keys)[1:]) if keys else set()
    if no_source:
        _need(not actual, 'no-source object has shape keys that would be lost: ' + label)
    else:
        _need(actual == set(expected_shapes),
              'shape key names differ from source on ' + label)
    if keys and not no_source:
        _need(mesh.shape_keys.use_relative and all(
              key.relative_key == keys[0] and not key.vertex_group
              for key in list(keys)[1:]),
              'shape keys need unmasked Basis-relative deltas on ' + label)
        mapped_index = np.fromiter(mapped_vertices.keys(), dtype=np.int32)
        source_index = np.fromiter(mapped_vertices.values(), dtype=np.int32)
        rebuilt_index = np.setdiff1d(np.arange(new['count'], dtype=np.int32), mapped_index)
        for name, source_delta in expected_shapes.items():
            actual_delta = sf6_source._from_blender(_coords(keys[name].data) - basis, rotate)
            _need(np.isfinite(actual_delta).all(), 'non-finite shape key ' + name)
            if len(mapped_index):
                _need(np.max(np.abs(actual_delta[mapped_index] - source_delta[source_index])) <= 1e-6,
                      'edited source shape delta on ' + label + '/' + name)
            if len(rebuilt_index):
                _need(np.max(np.abs(actual_delta[rebuilt_index])) <= 1e-6,
                      'rebuilt geometry has nonzero shape delta on ' + label + '/' + name)
    status = ('no_source' if no_source else
              'source_mapped' if len(mapped_vertices) == new['count'] and
              len(mapped_faces) == new['faces'] else
              'rebuilt' if not mapped_vertices else 'mixed')
    return {
        'group': key[0], 'material': key[1], 'status': status,
        'source_vertices': len(mapped_vertices),
        'rebuilt_vertices': new['count'] - len(mapped_vertices),
        'source_triangles': len(mapped_faces),
        'rebuilt_triangles': new['faces'] - len(mapped_faces),
        'shape_links': len(expected_shapes),
        'zero_delta_vertices': new['count'] - len(mapped_vertices) if expected_shapes else 0,
    }, mapped_vertices, mapped_faces


def _build_shape_table(source, ordinary, source_parts, ordinary_parts, mapped, out):
    raw = source.data
    _need(source.blend_offset and not ordinary.blend_offset,
          'source must have shapes and ordinary export must have none')
    count, table = _read(raw, 'QQ', source.blend_offset)
    _need(count == source.lod_count and count > 0, 'unsupported source shape LOD table')
    lp = _read(raw, 'Q', table)[0]
    target_count, extra_count = _read(raw, 'HH', lp)
    _need(target_count, 'unsupported empty shape target layout')
    tp, ap, sp, ssp = _read(raw, 'QQQQ', lp + 16)
    deltas = bytearray()
    records = []
    target_parts = set()
    shape_count = 0
    preserved_entries = 0
    for ti in range(target_count):
        ss, number, run, range_count, flag, rp = _read(raw, 'HHHBBQ', tp + ti * 16)
        _need(ss == shape_count and number and run <= ti and flag == 1 and range_count,
              'unsupported shape target linkage')
        old_ranges = [_read(raw, 'IIII', rp + ri * 16) for ri in range(range_count)]
        old_span = sum(item[2] for item in old_ranges)
        _need(old_span > 0, 'empty source shape target')
        placements = []
        new_ranges = []
        offset = len(deltas) // 8
        keys_in_target = set()
        for start, old_offset, length, reserved in old_ranges:
            _need(length > 0 and reserved == 0, 'unsupported source shape range')
            matches = [(key, part) for key, part in source_parts.items()
                       if part['start'] <= start and start + length <= part['start'] + part['count']]
            _need(len(matches) == 1, 'shape range crosses or misses source part')
            key, part = matches[0]
            new = ordinary_parts[key]
            vertex_map = mapped[key][0]
            identity = (new['count'] == part['count'] and
                        len(vertex_map) == new['count'] and
                        all(vertex_map.get(vi) == vi for vi in range(new['count'])))
            _need(key not in keys_in_target or identity,
                  'multiple shape ranges in a partly rebuilt part are unsupported: ' + repr(key))
            keys_in_target.add(key)
            target_parts.add(key)
            _need(new['count'] > 0, 'empty ordinary shape part')
            output_start = new['start'] + start - part['start'] if identity else new['start']
            output_length = length if identity else new['count']
            new_ranges.append((output_start, offset, output_length, 0))
            placements.append((key, part, start, old_offset, length, identity))
            offset += output_length
        _need(len(new_ranges) <= 255, 'too many output shape ranges')
        range_ptr = _append(out, b''.join(struct.pack('<IIII', *r) for r in new_ranges))
        records.append(struct.pack('<HHHBBQ', ss, number, run, len(new_ranges), flag, range_ptr))
        for shape in range(number):
            for key, part, start, old_offset, length, identity in placements:
                new = ordinary_parts[key]
                output_length = length if identity else new['count']
                output = np.zeros((output_length, 4), dtype='<f2')
                indices = [(vi - (start - part['start']) if identity else vi,
                            part['start'] + sid - start)
                           for vi, sid in mapped[key][0].items()
                           if start <= part['start'] + sid < start + length]
                if indices:
                    source_delta_at = source.delta_offset + (old_offset + shape * old_span) * 8
                    _need(source_delta_at >= source.delta_offset and
                          source_delta_at + length * 8 <= source.face_offset,
                          'shape delta range exceeds GPU buffer')
                    source_delta = np.frombuffer(raw, '<f2', length * 4,
                                                 source_delta_at).reshape(-1, 4)
                    vi = np.array([pair[0] for pair in indices], dtype=np.int32)
                    si = np.array([pair[1] for pair in indices], dtype=np.int32)
                    output[vi] = source_delta[si]
                    preserved_entries += len(indices)
                deltas.extend(output.tobytes())
        _need(len(deltas) // 8 == offset + (number - 1) * sum(r[2] for r in new_ranges),
              'shape delta packing mismatch')
        shape_count += number
    _need(shape_count == len(source.shapes[0]), 'source shape name table differs from targets')
    _need(preserved_entries > 0, 'nothing to preserve: no source shape vertices map safely')

    # Source extra descriptors cover vertices outside targets.  A part must be
    # wholly target-covered or wholly extra-covered to avoid guessed overlap.
    extra_parts = set()
    for ti in range(target_count, target_count + extra_count):
        ss, number, run, range_count, flag, rp = _read(raw, 'HHHBBQ', tp + ti * 16)
        _need((ss, number, run, flag) == (0, 0, 0, 0),
              'unsupported zero-shape descriptor')
        new_ranges = []
        for ri in range(range_count):
            start, offset, length, reserved = _read(raw, 'IIII', rp + ri * 16)
            _need(offset == 0 and reserved == 0 and length > 0,
                  'unsupported zero-shape range')
            for key, part in source_parts.items():
                lo = max(start, part['start'])
                hi = min(start + length, part['start'] + part['count'])
                if lo >= hi:
                    continue
                new = ordinary_parts[key]
                vertex_map = mapped[key][0]
                identity = (new['count'] == part['count'] and
                            len(vertex_map) == new['count'] and
                            all(vertex_map.get(vi) == vi for vi in range(new['count'])))
                _need(key not in target_parts or identity,
                      'part has target/zero-shape split after rebuilding: ' + repr(key))
                _need(key not in extra_parts or identity,
                      'multiple zero-shape ranges in a rebuilt part are unsupported: ' + repr(key))
                extra_parts.add(key)
                new_ranges.append((new['start'] + lo - part['start'] if identity else new['start'],
                                   0, hi - lo if identity else new['count'], 0))
        _need(new_ranges and len(new_ranges) <= 255,
              'zero-shape descriptor cannot be remapped')
        range_ptr = _append(out, b''.join(struct.pack('<IIII', *r) for r in new_ranges))
        records.append(struct.pack('<HHHBBQ', 0, 0, 0, len(new_ranges), 0, range_ptr))
    _need(target_parts | extra_parts == set(source_parts),
          'shape target/extra descriptors do not cover all parts')

    total_vertices = sum(part['count'] for part in ordinary_parts.values())
    target_coverage = np.zeros(total_vertices, bool)
    extra_coverage = np.zeros(total_vertices, np.uint8)
    for ti, record in enumerate(records):
        _, _, _, n, _, rp = struct.unpack('<HHHBBQ', record)
        for ri in range(n):
            start, _, length, _ = _read(out, 'IIII', rp + ri * 16)
            _need(start + length <= total_vertices,
                  'remapped shape range exceeds LOD0')
            if ti < target_count:
                target_coverage[start:start + length] = True
            else:
                extra_coverage[start:start + length] += 1
    _need(np.all(target_coverage ^ (extra_coverage == 1)) and
          not np.any(extra_coverage > 1),
          'shape target and zero-shape coverage overlap or leave gaps')

    # Verify source AABBs already contain zero for every range where new
    # vertices receive zero deltas.  Changing unknown bound fields is avoided.
    aabbs = raw[ap:ap + target_count * 32]
    _need(len(aabbs) == target_count * 32, 'short shape bounds')
    for ti, record in enumerate(records[:target_count]):
        _, _, _, n, _, rp = struct.unpack('<HHHBBQ', record)
        zero_added = any(ordinary_parts[key]['count'] > len(mapped[key][0])
                         for key, part in source_parts.items()
                         if any(ordinary_parts[key]['start'] == _read(out, 'IIII', rp + ri * 16)[0]
                                for ri in range(n)))
        if zero_added:
            bounds = np.frombuffer(aabbs, '<f4', count=8, offset=ti * 32).reshape(2, 4)
            _need(np.all(bounds[0, :3] <= 0) and np.all(bounds[1, :3] >= 0),
                  'source shape bounds exclude zero for rebuilt geometry')

    target_ptr = _append(out, b''.join(records))
    aabb_ptr = _append(out, aabbs)
    target_map = raw[sp:sp + target_count * 4]
    shape_map = raw[ssp:ssp + shape_count * 4]
    _need(len(target_map) == target_count * 4 and len(shape_map) == shape_count * 4,
          'short source shape mapping tables')
    target_map_ptr = _append(out, target_map)
    shape_map_ptr = _append(out, shape_map)
    new_lod = _append(out, raw[lp:lp + 48])
    _write(out, 'QQQQ', new_lod + 16, target_ptr, aabb_ptr,
           target_map_ptr, shape_map_ptr)
    new_header = _append(out, raw[source.blend_offset:source.blend_offset + 32])
    _write(out, 'QQ', new_header, 1, new_header + 32)
    out.extend(struct.pack('<Q', new_lod))
    _write(out, 'Q', 64, new_header)
    _write(out, 'H', 16, _read(out, 'H', 16)[0] | 4)
    return deltas, shape_count, target_count, extra_count, preserved_entries


def _append_shape_names(source, ordinary, out, shape_count):
    old_count = _read(ordinary.data, 'H', 20)[0]
    _need(ordinary.shape_names_offset == 0 and
          old_count + shape_count <= 65535, 'unsupported ordinary name layout')
    table = _read(ordinary.data, 'Q', 144)[0]
    pointers = list(_read(ordinary.data, 'Q' * old_count, table))
    names = [name for name, _ in source.shapes[0]]
    _need(len(names) == shape_count, 'shape name count changed')
    for name in names:
        pointers.append(_append(out, name.encode('utf-8') + b'\0', 1))
    name_table = _append(out, struct.pack('<' + 'Q' * len(pointers), *pointers))
    remap = _append(out, struct.pack('<' + 'H' * shape_count,
                                     *range(old_count, old_count + shape_count)))
    _write(out, 'Q', 144, name_table)
    _write(out, 'Q', 128, remap)
    _write(out, 'H', 20, len(pointers))


def _append_shape_gpu(ordinary, out, deltas):
    mesh = ordinary.mesh_offset
    old_total, old_vertex_size = _read(ordinary.data, 'II', mesh + 24)
    _need(old_vertex_size == ordinary.vertex_size and old_total >= old_vertex_size and
          ordinary.vertex_offset + old_total == len(ordinary.data),
          'ordinary GPU buffer is not terminal')
    streaming = _read(ordinary.data, 'Q', 136)[0]
    _need(not streaming or _read(ordinary.data, 'QQ', streaming) == (0, 0),
          'streamed geometry is unsupported')
    vertex = bytearray(ordinary.data[ordinary.vertex_offset:ordinary.face_offset])
    delta_start = _append(vertex, deltas)
    vertex.extend(bytes((-len(vertex)) % 16))
    faces = ordinary.data[ordinary.face_offset:ordinary.vertex_offset + old_total]
    gpu = vertex + faces
    gpu_offset = _append(out, gpu)
    _write(out, 'Q', mesh + 8, gpu_offset)
    _write(out, 'II', mesh + 24, len(gpu), len(vertex))
    _write(out, 'II', mesh + 36, len(gpu), len(gpu))
    _write(out, 'I', mesh + 44, delta_start)
    _write(out, 'Q', 152, gpu_offset)
    _write(out, 'I', 8, len(out))
    return sf6_source.SourceMesh(bytes(out))


def _padded_indices(part):
    return (part['faces'] * 3 + 1) // 2 * 2


def _normal_pointers(src):
    header = _read(src.data, 'Q', 56)[0]
    _need(header != 0, 'retail source has no normal recalculation table')
    count, ptr = _read(src.data, 'QQ', header)
    _need(count == src.lod_count and count > 0, 'unexpected normal table LOD count')
    lod = _read(src.data, 'Q', ptr)[0]
    return _read(src.data, 'QQ', lod)


def _synthesize_normals(mesh, part, first_start):
    """Generate the SF6 packed LOD0 normal references from GPU geometry."""
    _need(0 in mesh.elements and 1 in mesh.elements,
          'missing position or normal vertex stream')
    position_stride, position_offset = mesh.elements[0]
    normal_stride, normal_offset = mesh.elements[1]
    _need(position_stride == 12 and normal_stride == 8,
          'unsupported position or normal vertex stride')
    start, count = part['start'], part['count']
    positions = mesh.data[position_offset + start * 12:
                          position_offset + (start + count) * 12]
    normals = mesh.data[normal_offset + start * 8:
                        normal_offset + (start + count) * 8]
    _need(len(positions) == count * 12 and len(normals) == count * 8,
          'short position or normal stream')
    first = {}
    canonical = np.empty(count, dtype=np.uint32)
    for vi in range(count):
        key = positions[vi * 12:vi * 12 + 12] + normals[vi * 8:vi * 8 + 3]
        canonical[vi] = first.setdefault(key, vi)
    triangles = mesh.faces(part).astype(np.int32)
    _need(len(triangles) == part['faces'] and
          np.all(triangles >= 0) and np.all(triangles < count),
          'triangle outside part vertex range')
    welded = canonical[triangles]
    edges = {}
    for triangle in welded:
        for a, b in ((triangle[0], triangle[1]),
                     (triangle[1], triangle[2]),
                     (triangle[2], triangle[0])):
            if a == b:
                continue
            edge = (int(min(a, b)), int(max(a, b)))
            edges[edge] = edges.get(edge, 0) + 1
    boundary = np.zeros(count, np.uint32)
    for (a, b), n in edges.items():
        if n == 1:
            boundary[a] = boundary[b] = 1
    base = start - first_start
    vertex = ((boundary[canonical] << 31) | (canonical + base)).astype('<u4')
    faces = np.zeros(_padded_indices(part), dtype='<u4')
    for begin in range(0, part['faces'], 512):
        end = min(begin + 512, part['faces'])
        values = welded[begin:end].astype(np.uint32) + base
        palette = np.unique(values)
        _need(len(palette) <= 1024, 'normal palette exceeds ten-bit rank')
        ranks = np.searchsorted(palette, values).astype(np.uint32)
        faces[begin * 3:end * 3] = ((ranks << 22) | values).reshape(-1)
    return vertex, faces


def _normal_remap(value, mapping, mask, flags, disabled_zero):
    raw = int(value)
    if raw == 0 and disabled_zero:
        return 0
    old_ref = raw & mask
    _need(old_ref in mapping, 'normal entry references an unmapped retail vertex')
    new_ref = mapping[old_ref]
    _need(new_ref <= mask, 'normal reference exceeds format width')
    return (raw & flags) | new_ref


def _validate_palette(face_records, part):
    for begin in range(0, part['faces'], 512):
        end = min(begin + 512, part['faces'])
        block = face_records[begin * 3:end * 3]
        values = block & 0x3fffff
        palette = np.unique(values)
        _need(np.array_equal(block >> 22, np.searchsorted(palette, values)),
              'normal palette rank changed in ' + repr((part['group'], part['sub'])))


def _normal_table(source, candidate, source_parts, candidate_parts, mapped, objects):
    vp, fp = _normal_pointers(source)
    old_first = next(part for part in source.parts if part['lod'] == 0)
    new_first = next(part for part in candidate.parts if part['lod'] == 0)
    total_vertices = sum(part['count'] for part in candidate_parts.values())
    total_indices = sum(_padded_indices(part) for part in candidate_parts.values())
    vertex = np.zeros(total_vertices, dtype='<u4')
    faces = np.zeros(total_indices, dtype='<u4')
    used_vertex = np.zeros(total_vertices, bool)
    used_index = np.zeros(total_indices, bool)
    regenerated_parts = []
    copied_parts = []
    stub_exceptions = []
    for key, old in source_parts.items():
        new = candidate_parts[key]
        vertex_map, face_map = mapped[key]
        old_vbase = old['start'] - old_first['start']
        new_vbase = new['start'] - new_first['start']
        old_fbase = old['face_start'] - old_first['face_start']
        new_fbase = new['face_start'] - new_first['face_start']
        old_nv = np.frombuffer(source.data, '<u4', old['count'], vp + old_vbase * 4)
        old_nf = np.frombuffer(source.data, '<u4', _padded_indices(old),
                               fp + old_fbase * 4)
        _need(0 <= new_vbase and new_vbase + new['count'] <= total_vertices and
              0 <= new_fbase and new_fbase + _padded_indices(new) <= total_indices,
              'normal table part allocation exceeds LOD0')
        _need(not np.any(used_vertex[new_vbase:new_vbase + new['count']]) and
              not np.any(used_index[new_fbase:new_fbase + _padded_indices(new)]),
              'normal table part allocations overlap')
        used_vertex[new_vbase:new_vbase + new['count']] = True
        used_index[new_fbase:new_fbase + _padded_indices(new)] = True
        if (len(vertex_map) == new['count'] and len(face_map) == new['faces'] and
                old['count'] == new['count'] and old['faces'] == new['faces']):
            # Exact retail geometry may be reordered by the ordinary writer.
            # Keep unknown high bits, remap only vertex references, and demand
            # that every 512-triangle palette still ranks exactly.
            mapping = {old_vbase + sid: new_vbase + vi
                       for vi, sid in vertex_map.items()}
            for vi, sid in vertex_map.items():
                vertex[new_vbase + vi] = _normal_remap(
                    old_nv[sid], mapping, 0x7fffffff, 0x80000000, False)
            for fi, source_fid in face_map.items():
                for corner in range(3):
                    faces[new_fbase + fi * 3 + corner] = _normal_remap(
                        old_nf[source_fid * 3 + corner], mapping,
                        0x3fffff, 0xffc00000, old_vbase != 0)
            _validate_palette(faces[new_fbase:new_fbase + _padded_indices(new)], new)
            copied_parts.append(key)
        else:
            # The reconstruction rule must first reproduce this retail part
            # byte-for-byte.  A different source encoding is outside the
            # proven SF6 hybrid format and fails closed.
            retail_v, retail_f = _synthesize_normals(source, old, old_first['start'])
            retail_exact = (np.array_equal(retail_v, old_nv) and
                            np.array_equal(retail_f, old_nf))
            exact_stub = (new['count'] == 4 and new['faces'] == 2 and
                          not list(source.part_shapes(old)) and
                          sf6_source._is_hip_stub(objects[key]))
            _need(retail_exact or exact_stub,
                  'normal reconstruction is not retail-exact for ' + repr(key))
            if not retail_exact:
                stub_exceptions.append(key)
            new_v, new_f = _synthesize_normals(candidate, new, new_first['start'])
            _validate_palette(new_f, new)
            vertex[new_vbase:new_vbase + new['count']] = new_v
            faces[new_fbase:new_fbase + _padded_indices(new)] = new_f
            regenerated_parts.append(key)
    _need(used_vertex.all() and used_index.all(), 'normal table has allocation gaps')
    _need(np.all((vertex & 0x7fffffff) < total_vertices),
          'normal vertex reference out of range')
    active = faces != 0
    _need(np.all((faces[active] & 0x3fffff) < total_vertices),
          'normal face reference out of range')
    return vertex, faces, copied_parts, regenerated_parts, stub_exceptions


def _append_normal_table(candidate, out, vertex, faces):
    _need(_read(candidate.data, 'Q', 56)[0] == 0,
          'ordinary export unexpectedly contains a normal table')
    # The shaped GPU allocation is terminal.  Move it after the new normal
    # metadata, instead of leaving a second unused copy in the file.
    _need(len(out) == len(candidate.data), 'shape candidate changed before normal rebuild')
    del out[candidate.vertex_offset:]
    block = _append(out, bytes(16))
    vertex_ptr = _append(out, vertex.tobytes())
    face_ptr = _append(out, faces.tobytes())
    _write(out, 'QQ', block, vertex_ptr, face_ptr)
    header = _append(out, struct.pack('<QQ', 1, 0))
    _write(out, 'Q', header + 8, header + 16)
    out.extend(struct.pack('<Q', block))
    _write(out, 'Q', 56, header)
    old_gpu = candidate.data[candidate.vertex_offset:]
    new_gpu = _append(out, old_gpu)
    _write(out, 'Q', candidate.mesh_offset + 8, new_gpu)
    _write(out, 'Q', 152, new_gpu)
    _write(out, 'I', 8, len(out))


def _bone_names(mesh):
    if not mesh.skeleton_offset:
        return []
    count = _read(mesh.data, 'I', mesh.skeleton_offset)[0]
    ids = _read(mesh.data, 'H' * count, mesh.bone_names_offset)
    return [mesh.names[index] for index in ids]


def _verify_result(source, ordinary, result, source_parts, ordinary_parts,
                   mapped, shape_count, normal_vertex, normal_face):
    final = sf6_source.SourceMesh(result)
    _need(final.lod_count == ordinary.lod_count == 1 and
          final.parts == ordinary.parts and final.materials == ordinary.materials,
          'hybrid changed ordinary LOD0 draw layout or materials')
    _need(final.skeleton_offset == ordinary.skeleton_offset and
          _bone_names(final) == _bone_names(ordinary),
          'hybrid changed ordinary skeleton names')
    _need(len(final.shapes[0]) == shape_count and
          [name for name, _ in final.shapes[0]] ==
          [name for name, _ in source.shapes[0]],
          'hybrid shape names differ from source')
    total_vertices = sum(part['count'] for part in ordinary_parts.values())
    for kind, (stride, old_offset) in ordinary.elements.items():
        new_stride, new_offset = final.elements[kind]
        size = total_vertices * stride
        _need(stride == new_stride and
              ordinary.data[old_offset:old_offset + size] ==
              result[new_offset:new_offset + size],
              'ordinary vertex stream changed: ' + str(kind))
    for key, old in source_parts.items():
        new = ordinary_parts[key]
        actual = next(part for part in final.parts if _part_key(final, part) == key)
        _need(np.array_equal(ordinary.faces(new), final.faces(actual)),
              'ordinary triangles changed: ' + repr(key))
        original_shapes = {name: delta for name, delta, _ in source.part_shapes(old)}
        output_shapes = {name: delta for name, delta, _ in final.part_shapes(actual)}
        _need(output_shapes.keys() == original_shapes.keys(),
              'part shape names changed: ' + repr(key))
        source_ids = mapped[key][0]
        for name, expected in original_shapes.items():
            desired = np.zeros((new['count'], 3), dtype='<f4')
            for vi, sid in source_ids.items():
                desired[vi] = expected[sid]
            _need(np.array_equal(output_shapes[name], desired),
                  'shape deltas differ from verified source or zero: ' + repr(key) + '/' + name)
    header = _read(result, 'Q', 56)[0]
    _need(header != 0 and _read(result, 'Q', header)[0] == 1,
          'hybrid normal table missing')
    ptr = _read(result, 'Q', header + 8)[0]
    block = _read(result, 'Q', ptr)[0]
    vp, fp = _read(result, 'QQ', block)
    _need(result[vp:vp + normal_vertex.nbytes] == normal_vertex.tobytes() and
          result[fp:fp + normal_face.nbytes] == normal_face.tobytes(),
          'normal recalculation bytes changed')
    _need(_read(result, 'I', 8)[0] == len(result), 'hybrid size header is wrong')

    # Only explicitly known pointer/size/flag fields may differ within the
    # ordinary file.  For a shape-free selection, normal metadata reuses the
    # original GPU location; the active vertex streams and faces were checked
    # above, so compare only the original metadata prefix in that case.
    allowed = [(8, 12), (16, 18), (20, 22), (56, 64), (64, 72),
               (128, 136), (144, 152), (152, 160),
               (ordinary.mesh_offset + 8, ordinary.mesh_offset + 16),
               (ordinary.mesh_offset + 24, ordinary.mesh_offset + 32),
               (ordinary.mesh_offset + 36, ordinary.mesh_offset + 48)]
    scan_limit = ordinary.vertex_offset if shape_count == 0 else len(ordinary.data)
    for pos, (before, after) in enumerate(zip(
            ordinary.data[:scan_limit], result[:scan_limit])):
        if before != after:
            _need(any(lo <= pos < hi for lo, hi in allowed),
                  'ordinary bytes changed outside documented pointer/size fields')
    return final


def build_hybrid_mesh(source_bytes, ordinary_bytes, collection, selected_objects=None):
    """Build a one-LOD hybrid and a precise preservation report.

    This is a pure byte builder with respect to the destination: it never
    writes files or changes the Blender scene.  The caller must commit the
    returned bytes atomically only after this function succeeds.  Source
    identity, face provenance, shape keys, and normal encoding are checked
    before any result is returned.
    """
    _need(isinstance(source_bytes, bytes) and isinstance(ordinary_bytes, bytes),
          'source and ordinary exports must be bytes')
    source_hash = _verify_source(collection, source_bytes)
    original = sf6_source.SourceMesh(source_bytes)
    selected = None if selected_objects is None else set(selected_objects)
    original_parts = _parts_by_key(original)
    if selected is None:
        source = original
        objects, no_source = _scene_objects(
            collection, original, source_hash, original_parts)
    else:
        # The source compactor rewrites global starts and sub indices.  Resolve
        # selected objects against the original import metadata before that
        # rewrite, then use stable group/material identities in the subset.
        objects, no_source = _scene_objects(
            collection, original, source_hash, original_parts, selected)
        from .sf6_source_subset import compact_source_subset
        included = {original_parts[key]['start'] for key in objects}
        source = sf6_source.SourceMesh(bytes(compact_source_subset(
            source_bytes, original, included, all_lods=False)))
    ordinary = sf6_source.SourceMesh(ordinary_bytes)
    _need(source.lod_count >= 1 and ordinary.lod_count == 1,
          'hybrid export currently supports an ordinary single-LOD output')
    if selected is None:
        _need(source.blend_offset and source.shapes[0],
              'nothing to preserve: retail source has no LOD0 shapes')
    _need(not ordinary.blend_offset,
          'ordinary input already has a shape table')
    _need(_read(ordinary_bytes, 'Q', 56)[0] == 0,
          'ordinary input already has a normal recalculation table')
    source_parts = _parts_by_key(source)
    ordinary_parts = _parts_by_key(ordinary)
    _need(source_parts.keys() == ordinary_parts.keys(),
          'ordinary output does not have the same unique LOD0 part identities')
    _need(source_parts.keys() == objects.keys(),
          'selected source parts differ from selected scene objects')
    rotate = collection['SF6SourceRotate']
    mapped = {}
    parts_report = []
    for key, old in source_parts.items():
        report, vertices, faces = _analyze_part(
            source, ordinary, old, ordinary_parts[key], objects[key], rotate,
            no_source=key in no_source)
        mapped[key] = vertices, faces
        parts_report.append(report)

    out = bytearray(ordinary_bytes)
    if source.blend_offset:
        delta, shape_count, targets, extras, preserved_entries = _build_shape_table(
            source, ordinary, source_parts, ordinary_parts, mapped, out)
        _append_shape_names(source, ordinary, out, shape_count)
        shaped = _append_shape_gpu(ordinary, out, delta)
    else:
        _need(selected is not None and not source.shapes[0],
              'nothing to preserve: retail source has no LOD0 shapes')
        shape_count = targets = extras = preserved_entries = 0
        shaped = ordinary
    normal_vertex, normal_face, copied, rebuilt, stub_exceptions = _normal_table(
        source, shaped, source_parts, ordinary_parts, mapped, objects)
    _append_normal_table(shaped, out, normal_vertex, normal_face)
    result = bytes(out)
    _verify_result(source, ordinary, result, source_parts, ordinary_parts,
                   mapped, shape_count, normal_vertex, normal_face)
    source_bones = _bone_names(source)
    output_bones = _bone_names(ordinary)
    report = {
        'mode': 'hybrid_lod0',
        'full_source_preservation': False,
        'source_sha256': source_hash,
        'ordinary_sha256': hashlib.sha256(ordinary_bytes).hexdigest(),
        'output_sha256': hashlib.sha256(result).hexdigest(),
        'output_bytes': len(result),
        'source_lods': original.lod_count,
        'output_lods': 1,
        'source_parts': len(original.parts),
        'source_lod0_parts': len(original_parts),
        'selected_parts': len(source_parts),
        'selected_source_parts': len(source_parts),
        'output_parts': len(ordinary.parts),
        'source_shape_links': sum(len(shapes) for shapes in original.shapes),
        'source_lod0_shape_links': len(original.shapes[0]),
        'selected_source_shape_links': len(source.shapes[0]),
        'selected_only': selected is not None,
        'output_shape_links': shape_count,
        'shape_targets': targets,
        'zero_shape_descriptors': extras,
        'source_shape_delta_entries_retained': preserved_entries,
        'source_vertices': sum(part['source_vertices'] for part in parts_report),
        'rebuilt_vertices': sum(part['rebuilt_vertices'] for part in parts_report),
        'source_triangles': sum(part['source_triangles'] for part in parts_report),
        'rebuilt_triangles': sum(part['rebuilt_triangles'] for part in parts_report),
        'normal_table_rebuilt': True,
        'normal_parts_copied': [material for _, material in copied],
        'normal_parts_rebuilt': [material for _, material in rebuilt],
        'normal_exact_stub_exceptions': [material for _, material in stub_exceptions],
        'source_auxiliary_table_omitted': bool(_read(original.data, 'Q', 88)[0]) and
                                          not bool(_read(ordinary.data, 'Q', 88)[0]),
        'source_bones': len(source_bones),
        'output_bones': len(output_bones),
        'bone_names_added': sorted(set(output_bones) - set(source_bones)),
        'bone_names_removed': sorted(set(source_bones) - set(output_bones)),
        'parts': parts_report,
    }
    return result, report
