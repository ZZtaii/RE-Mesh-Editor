"""Subset an already validated SF6 source export without re-evaluating geometry.

The ordinary exporter does not use this module. Kept vertex attributes, weights,
shape deltas and packed normal flags are copied from the preservation result.
Only their layout and references change. Unrelated source metadata stays at its
original address; replacement tables are placed before the new GPU buffers.
"""
import struct

import numpy as np


def read(data, fmt, offset):
    size = struct.calcsize('<'+fmt)
    if offset < 0 or offset+size > len(data):
        raise ValueError('SF6 subset: source table is outside the file')
    return struct.unpack_from('<'+fmt, data, offset)


def write(data, fmt, offset, *values):
    struct.pack_into('<'+fmt, data, offset, *values)


def align(data, size=16):
    data.extend(bytes((-len(data)) % size))
    return len(data)


def append(data, block, alignment=16):
    offset = align(data, alignment)
    data.extend(block)
    return offset


def padded_indices(part):
    return (part['faces']*3+1)//2*2


def compact_source_subset(patched, src, included_starts, all_lods=True):
    """Copy selected source parts and remap all supported dependent tables.

    At least one part from source LOD0 is required because lower-LOD shape
    correspondence tables are indexed by LOD0. Nonstandard layouts fail before
    the caller opens its destination rather than guessing at unknown offsets.
    """
    source = src.data
    parts = [p for p in src.parts if p['start'] in included_starts
             and (all_lods or p['lod'] == 0)]
    if not parts:
        raise ValueError('No selected source mesh objects in the requested LODs')
    if parts == src.parts:
        return patched
    if parts[0]['lod'] != 0:
        raise ValueError('Selected source export requires a selected LOD0 mesh')
    if src.index_size != 2:
        raise ValueError('SF6 subset: 32-bit source index layouts are not supported')
    if any(read(source, 'Q', offset)[0] for offset in (40, 48, 80, 160)):
        raise ValueError('SF6 subset: separate shadow/occlusion or auxiliary geometry is not supported')
    if read(source, 'Q', src.mesh_offset+16)[0]:
        raise ValueError('SF6 subset: secondary source buffers are not supported')
    streaming = read(source, 'Q', 136)[0]
    if streaming and any(read(source, 'QQ', streaming)):
        raise ValueError('SF6 subset: streamed source geometry is not supported')
    if any(read(source, 'QQQ', src.mesh_offset+56)):
        raise ValueError('SF6 subset: auxiliary mesh buffer tables are not supported')

    # Keep all independent metadata at its source address, but replace the GPU
    # allocation itself. No excluded object's vertices remain in that buffer.
    out = bytearray(patched[:src.vertex_offset])
    lods = sorted({p['lod'] for p in parts})
    original_lods = {li:[p for p in src.parts if p['lod'] == li] for li in range(src.lod_count)}
    kept_lods = {li:[p for p in parts if p['lod'] == li] for li in lods}
    new_starts, new_faces, local_starts = {}, {}, {}
    vertex_count = face_count = 0
    for li in lods:
        local_vertex = 0
        for p in kept_lods[li]:
            if p['start'] in new_starts:
                raise ValueError('SF6 subset: reused vertex ranges across LODs are not supported')
            new_starts[p['start']] = vertex_count
            new_faces[p['start']] = face_count
            local_starts[p['start']] = local_vertex
            vertex_count += p['count']
            local_vertex += p['count']
            face_count += padded_indices(p)
    if vertex_count >= 1 << 22:
        raise ValueError('SF6 subset exceeds the normal-table vertex index limit')

    # Copy attributes verbatim, with a common new vertex order for every stream.
    vertices = bytearray()
    element_offsets = {}
    for kind, (stride, offset) in src.elements.items():
        if kind not in (0, 1, 2, 3, 4, 5):
            raise ValueError('SF6 subset: unknown vertex attribute '+str(kind))
        element_offsets[kind] = len(vertices)
        for p in parts:
            start = offset+p['start']*stride
            vertices.extend(patched[start:start+p['count']*stride])
    delta_start = align(vertices)

    # Main draw tables: one entry per original visibility group, with no gaps
    # or duplicate group IDs introduced by omitting an interior submesh.
    old_table = read(source, 'Q', src.lod_offset+56)[0]
    old_lods = read(source, 'Q'*src.lod_count, old_table)
    new_lod_offsets = []
    for li in lods:
        old_lod = old_lods[li]
        group_table = read(source, 'Q', old_lod+8)[0]
        groups = []
        for group in read(source, 'Q'*source[old_lod], group_table):
            group_id, sub_count = read(source, 'BB', group)
            original_parts = [p for p in original_lods[li] if p['group'] == group_id]
            if len(original_parts) != sub_count:
                raise ValueError('SF6 subset: duplicate/ambiguous source visibility group')
            kept = [p for p in original_parts if p in kept_lods[li]]
            if not kept:
                continue
            block = bytearray(source[group:group+16])
            block[1] = len(kept)
            write(block, 'II', 8, sum(p['count'] for p in kept), sum(padded_indices(p) for p in kept))
            for p in kept:
                record = bytearray(source[group+16+p['sub']*24:group+40+p['sub']*24])
                write(record, 'II', 8, new_faces[p['start']], new_starts[p['start']])
                block.extend(record)
            groups.append(block)
        lp = append(out, source[old_lod:old_lod+16])
        out[lp] = len(groups)
        write(out, 'Q', lp+8, lp+16)
        out.extend(bytes(8*len(groups)))
        align(out)
        for gi, block in enumerate(groups):
            gp = append(out, block, alignment=1)
            write(out, 'Q', lp+16+gi*8, gp)
        new_lod_offsets.append(lp)
    # Keep the LOD pointer array inline with MainMeshHeader. Some readers use
    # that inline layout instead of seeking through offsetOffset.
    write(out, 'Q'*len(lods), old_table, *new_lod_offsets)
    out[src.lod_offset] = len(lods)
    out[src.lod_offset+7] = 0
    write(out, 'H', src.lod_offset+4, len(kept_lods[0]))
    write(out, 'Q', src.lod_offset+56, old_table)

    _normal_tables(out, source, src, lods, original_lods, kept_lods, local_starts)
    shape_names = _shape_tables(out, patched, src, lods, kept_lods, new_starts, vertices, delta_start)
    _name_table(out, source, src, shape_names)
    if not shape_names:
        write(out, 'Q', 64, 0)
        write(out, 'H', 16, read(source, 'H', 16)[0] & ~4)

    vertex_size = align(vertices)
    faces = bytearray()
    for p in parts:
        start = src.face_offset+p['face_start']*2
        faces.extend(patched[start:start+padded_indices(p)*2])
    buffer = vertices+faces
    unpadded_size = len(buffer)
    align(buffer)
    vertex_offset = append(out, buffer)
    write(out, 'Q', src.mesh_offset+8, vertex_offset)
    write(out, 'II', src.mesh_offset+24, len(buffer), vertex_size)
    write(out, 'III', src.mesh_offset+36, unpadded_size, unpadded_size, delta_start)
    write(out, 'Q', 152, vertex_offset)
    for i in range(len(src.elements)):
        kind = read(source, 'H', src.vertex_element_offset+i*8)[0]
        write(out, 'I', src.vertex_element_offset+i*8+4, element_offsets[kind])
    write(out, 'I', 8, len(out))
    return out


def _normal_tables(out, source, src, lods, original_lods, kept_lods, local_starts):
    header = read(source, 'Q', 56)[0]
    if not header:
        return
    count, table = read(source, 'QQ', header)
    if count != src.lod_count:
        raise ValueError('SF6 subset: unexpected normal LOD count')
    old_blocks = read(source, 'Q'*count, table)
    blocks = []
    for li in lods:
        vp, fp = read(source, 'QQ', old_blocks[li])
        original = original_lods[li]
        vertex_base = original[0]['start']
        face_base = original[0]['face_start']
        vertex_data, face_data = bytearray(), bytearray()
        for p in kept_lods[li]:
            old = p['start']-vertex_base
            new = local_starts[p['start']]
            lookup = np.frombuffer(source, '<u4', p['count'], vp+old*4).copy()
            indices = lookup & 0x7fffffff
            if np.any((indices < old) | (indices >= old+p['count'])):
                raise ValueError('SF6 subset: normal vertex references another part')
            lookup = (lookup & 0x80000000) | (indices-old+new)
            vertex_data.extend(lookup.astype('<u4').tobytes())
            values = np.frombuffer(source, '<u4', padded_indices(p),
                                   fp+(p['face_start']-face_base)*4).copy()
            used = values[:p['faces']*3]
            indices = used & 0x3fffff
            # Some retail assets contain disabled zero entries. Preserve them;
            # never interpret their zero sentinel as a reference to another part.
            active = used != 0 if old else np.ones(len(used), dtype=bool)
            if np.any((indices[active] < old) | (indices[active] >= old+p['count'])):
                raise ValueError('SF6 subset: normal face references another part')
            used[active] = (used[active] & 0xffc00000) | (indices[active]-old+new)
            if len(values) > len(used):
                values[-1] = 0
            face_data.extend(values.astype('<u4').tobytes())
        block = append(out, bytes(16))
        new_vp = append(out, vertex_data)
        new_fp = append(out, face_data)
        write(out, 'QQ', block, new_vp, new_fp)
        blocks.append(block)
    new_header = append(out, struct.pack('<QQ', len(blocks), 0))
    write(out, 'Q', new_header+8, new_header+16)
    out.extend(struct.pack('<'+'Q'*len(blocks), *blocks))
    write(out, 'Q', 56, new_header)


def _shape_tables(out, patched, src, lods, kept_lods, new_starts, vertices, delta_start):
    source = src.data
    if not src.blend_offset:
        return []
    count, table = read(source, 'QQ', src.blend_offset)
    if count != src.lod_count:
        raise ValueError('SF6 subset: unexpected shape LOD count')
    old_lods = read(source, 'Q'*count, table)
    old_name_count = sum(map(len, src.shapes))
    old_names = read(source, 'H'*old_name_count, src.shape_names_offset)
    name_bases = [sum(len(s) for s in src.shapes[:li]) for li in range(count)]
    all_names, lod_records = [], []
    root_targets = root_shapes = None
    for li in lods:
        lp = old_lods[li]
        tc, extra_count = read(source, 'HH', lp)
        tp, ap, sp, ssp = read(source, 'QQQQ', lp+16)
        records, aabbs, target_map, shape_map = [], bytearray(), {}, {}
        local_shape_count = 0
        for ti in range(tc):
            ss, number, run, n, flag, rp = read(source, 'HHHBBQ', tp+ti*16)
            ranges = [read(source, 'IIII', rp+ri*16) for ri in range(n)]
            span = sum(r[2] for r in ranges)
            slices = []
            for start, offset, length, reserved in ranges:
                if reserved:
                    raise ValueError('SF6 subset: unknown shape range flags')
                for p in kept_lods[li]:
                    lo, hi = max(start,p['start']), min(start+length,p['start']+p['count'])
                    if lo < hi:
                        slices.append((new_starts[p['start']]+lo-p['start'], offset+lo-start, hi-lo))
            if not slices:
                continue
            if len(slices) > 255:
                raise ValueError('SF6 subset has too many shape ranges')
            target_map[ti] = len(records)
            if run not in target_map:
                raise ValueError('SF6 subset: unsupported shape target linkage')
            new_ranges = []
            delta_base = (len(vertices)-delta_start)//8
            offset = delta_base
            for start, _, length in slices:
                new_ranges.append((start, offset, length, 0))
                offset += length
            for shape in range(number):
                shape_map[ss+shape] = local_shape_count+shape
                all_names.append(old_names[name_bases[li]+ss+shape])
                for _, old_offset, length in slices:
                    start = src.delta_offset+(old_offset+shape*span)*8
                    vertices.extend(patched[start:start+length*8])
            range_offset = append(out, b''.join(struct.pack('<IIII', *r) for r in new_ranges))
            records.append(struct.pack('<HHHBBQ', local_shape_count, number,
                                       target_map[run], len(slices), flag, range_offset))
            aabbs.extend(source[ap+ti*32:ap+(ti+1)*32])
            local_shape_count += number
        # The second header count is NOT a format/type field: it counts
        # additional zero-shape descriptors for vertices outside shape ranges.
        # The ordinary Blender shape reader skips these, but the game uses them.
        extras = []
        for ti in range(tc, tc+extra_count):
            ss, number, run, n, flag, rp = read(source, 'HHHBBQ', tp+ti*16)
            if (ss, number, run, flag) != (0, 0, 0, 0):
                raise ValueError('SF6 subset: unknown extra shape descriptor')
            ranges = []
            for ri in range(n):
                start, offset, length, reserved = read(source, 'IIII', rp+ri*16)
                if offset or reserved:
                    raise ValueError('SF6 subset: unknown extra shape range')
                for p in kept_lods[li]:
                    lo, hi = max(start,p['start']), min(start+length,p['start']+p['count'])
                    if lo < hi:
                        ranges.append((new_starts[p['start']]+lo-p['start'], 0, hi-lo, 0))
            if ranges:
                if len(ranges) > 255:
                    raise ValueError('SF6 subset has too many extra shape ranges')
                rp_new = append(out, b''.join(struct.pack('<IIII',*r) for r in ranges))
                extras.append(struct.pack('<HHHBBQ',0,0,0,len(ranges),0,rp_new))
        new_lp = append(out, source[lp:lp+48])
        write(out, 'HH', new_lp, len(records), len(extras))
        new_tp = append(out, b''.join(records+extras))
        new_ap = append(out, aabbs)
        if li == 0:
            root_targets, root_shapes = list(target_map), list(shape_map)
            s_values = [0xffffffff]*len(root_targets)
            ss_values = [0xffffffff]*len(root_shapes)
        else:
            s_values = [target_map.get(read(source,'I',sp+ti*4)[0],0xffffffff) for ti in root_targets]
            ss_values = [shape_map.get(read(source,'I',ssp+si*4)[0],0xffffffff) for si in root_shapes]
        new_sp = append(out, struct.pack('<'+'I'*len(s_values), *s_values))
        new_ssp = append(out, struct.pack('<'+'I'*len(ss_values), *ss_values))
        write(out, 'QQQQ', new_lp+16, new_tp, new_ap, new_sp, new_ssp)
        lod_records.append(new_lp)
    new_header = append(out, source[src.blend_offset:src.blend_offset+32])
    write(out, 'QQ', new_header, len(lods), new_header+32)
    out.extend(struct.pack('<'+'Q'*len(lods), *lod_records))
    write(out, 'Q', 64, new_header)
    return all_names


def _name_table(out, source, src, shape_names):
    bone_count = read(source, 'I', src.skeleton_offset)[0] if src.skeleton_offset else 0
    prefix_count = len(src.materials)+bone_count
    material_ids = read(source, 'H'*len(src.materials), src.material_names_offset)
    bone_ids = read(source, 'H'*bone_count, src.bone_names_offset)
    if set(material_ids+bone_ids) != set(range(prefix_count)):
        raise ValueError('SF6 subset: unsupported source name layout')
    old_table = read(source, 'Q', 144)[0]
    ids = list(range(prefix_count))+shape_names
    pointers = [read(source, 'Q', old_table+ni*8)[0] for ni in ids]
    new_table = append(out, struct.pack('<'+'Q'*len(pointers), *pointers))
    write(out, 'Q', 144, new_table)
    write(out, 'H', 20, len(pointers))
    remap = append(out, struct.pack('<'+'H'*len(shape_names), *range(prefix_count,len(pointers))))
    write(out, 'Q', 128, remap if shape_names else 0)
