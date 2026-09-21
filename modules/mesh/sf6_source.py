"""SF6 source-layout round trips.

Keep the retail deformation tables, vertex numbering, material order and all LODs.
This deliberately does not synthesize the still-unknown runtime deformation data.
Existing vertices/shape keys can be edited; faces/objects can be removed. New or
rewired topology is rejected before opening the destination file.
"""
import base64
import hashlib
import json
import os
import struct
import tempfile
import zlib

import numpy as np

SOURCE = 'SF6SourceText'
META = 'SF6SourceMeta'
VERTEX_ID = 'sf6_source_vertex'
FACE_ID = 'sf6_source_face'


def _unpack(data, fmt, offset):
    return struct.unpack_from('<' + fmt, data, offset)


class SourceMesh:
    """Read only the SF6 structures needed here, with explicit offsets and bounds."""
    def __init__(self, data):
        self.data = data
        if data[:4] != b'MESH' or len(data) < 168:
            raise ValueError('Not an SF6 MESH file')
        self.version, size = _unpack(data, 'II', 4)
        if self.version not in (220705151, 230403828) or size != len(data):
            raise ValueError('Unsupported SF6 internal version or incomplete source file')
        self.lod_offset = _unpack(data, 'Q', 32)[0]
        self.blend_offset = _unpack(data, 'Q', 64)[0]
        self.mesh_offset = _unpack(data, 'Q', 72)[0]
        self.skeleton_offset = _unpack(data, 'Q', 104)[0]
        self.material_names_offset, self.bone_names_offset, self.shape_names_offset = _unpack(data, 'QQQ', 112)
        name_offset = _unpack(data, 'Q', 144)[0]
        name_count = _unpack(data, 'H', 20)[0]
        self.names = []
        for p in _unpack(data, 'Q' * name_count, name_offset):
            self.names.append(data[p:data.index(b'\0', p)].decode('utf-8'))
        self.vertex_element_offset, self.vertex_offset = _unpack(data, 'QQ', self.mesh_offset)
        self.vertex_size = _unpack(data, 'I', self.mesh_offset + 28)[0]
        self.face_offset = self.vertex_offset + self.vertex_size
        element_count = _unpack(data, 'H', self.mesh_offset + 34)[0]
        self.elements = {}
        for i in range(element_count):
            kind, stride, offset = _unpack(data, 'HHI', self.vertex_element_offset + i * 8)
            self.elements[kind] = (stride, self.vertex_offset + offset)
        self.delta_offset = self.vertex_offset + _unpack(data, 'I', self.mesh_offset + 44)[0]
        self.lod_count, mat_count = _unpack(data, 'BB', self.lod_offset)
        self.materials = [self.names[i] for i in _unpack(data, 'H' * mat_count, self.material_names_offset)]
        self.index_size = 4 if data[self.lod_offset + 6] else 2
        self.parts = []
        # LOD header: flags (8), sphere (16), AABB (32), pointer (8), offsets.
        lod_ptr = _unpack(data, 'Q', self.lod_offset + 56)[0]
        for li, lp in enumerate(_unpack(data, 'Q' * self.lod_count, lod_ptr)):
            group_count = data[lp]
            gp = _unpack(data, 'Q', lp + 8)[0]
            for group_offset in _unpack(data, 'Q' * group_count, gp):
                group, count = _unpack(data, 'BB', group_offset)
                vertex_count = _unpack(data, 'I', group_offset + 8)[0]
                subs = []
                for si in range(count):
                    p = group_offset + 16 + si * 24
                    material, quad, buffer = _unpack(data, 'BBB', p)
                    if buffer or quad:
                        raise ValueError('Source mode does not support streamed/quad SF6 meshes')
                    faces, face_start, start = _unpack(data, 'III', p + 4)
                    subs.append(dict(lod=li, group=group, sub=si, material=material,
                                     start=start, faces=faces // 3, face_start=face_start))
                for si, s in enumerate(subs):
                    s['count'] = (subs[si+1]['start'] if si+1 < count else subs[0]['start']+vertex_count)-s['start']
                    if s['count'] < 0:
                        raise ValueError('Unsupported source vertex layout')
                    self.parts.append(s)
        self.shapes = [[] for _ in range(self.lod_count)]
        if self.blend_offset:
            count, ptr = _unpack(data, 'QQ', self.blend_offset)
            name_index = 0
            for li, lp in enumerate(_unpack(data, 'Q' * count, ptr)):
                target_count = _unpack(data, 'H', lp)[0]
                target_ptr = _unpack(data, 'Q', lp+16)[0]
                for ti in range(target_count):
                    ss, number, run, ranges, flag, rp = _unpack(data, 'HHHBBQ', target_ptr+ti*16)
                    entries = [_unpack(data, 'IIII', rp+ri*16) for ri in range(ranges)]
                    span = sum(e[2] for e in entries)
                    for shape in range(number):
                        name_id = _unpack(data, 'H', self.shape_names_offset+name_index*2)[0]
                        self.shapes[li].append((self.names[name_id], [(start, self.delta_offset+(off+shape*span)*8, n) for start, off, n, _ in entries]))
                        name_index += 1
        # Validate every accessed vertex/range against the file before Blender edits.
        for part in self.parts:
            if part['start']+part['count'] > (self.elements[1][1]-self.elements[0][1])//12:
                raise ValueError('Source vertex range exceeds the vertex buffer')
        for shapes in self.shapes:
            for name, ranges in shapes:
                for start, offset, n in ranges:
                    if offset < self.delta_offset or offset+n*8 > self.face_offset:
                        raise ValueError('Invalid source blend-shape range: '+name)

    def positions(self, part):
        return np.frombuffer(self.data, '<f4', part['count']*3, self.elements[0][1]+part['start']*12).reshape(-1,3).copy()

    def faces(self, part):
        return np.frombuffer(self.data, '<u'+str(self.index_size), part['faces']*3, self.face_offset+part['face_start']*self.index_size).reshape(-1,3).copy()

    def part_shapes(self, part):
        begin, end = part['start'], part['start']+part['count']
        for name, ranges in self.shapes[part['lod']]:
            delta = np.zeros((part['count'],3), '<f4')
            touched = False
            for start, offset, n in ranges:
                lo, hi = max(start,begin), min(start+n,end)
                if lo < hi:
                    delta[lo-begin:hi-begin] = np.frombuffer(self.data,'<f2',(hi-lo)*4,offset+(lo-start)*8).reshape(-1,4)[:,:3]
                    touched = True
            if touched:
                yield name, delta, ranges


def _to_blender(a, rotate):
    return a[:, (0,2,1)] * np.array((1,-1,1), '<f4') if rotate else a.copy()


def _from_blender(a, rotate):
    return a[:, (0,2,1)] * np.array((1,1,-1), '<f4') if rotate else a.copy()


def _coords(block):
    result = np.empty((len(block),3), '<f4')
    block.foreach_get('co', result.ravel())
    return result


def _ids(mesh, name, size):
    attr = mesh.attributes.get(name)
    if attr is None or len(attr.data) != size:
        raise ValueError('Source identity was lost; re-import with SF6 source mode')
    a = np.empty(size, np.int32)
    attr.data.foreach_get('value',a)
    return a


def _armature_signature(armature):
    if armature is None:
        return None
    return [(b.name, b.parent.name if b.parent else None, [v for row in b.matrix_local for v in row]) for b in armature.data.bones]


def _is_hip_stub(obj):
    """Recognize the exact output of Game Modding Stub Tools 1.7."""
    mesh=obj.data
    expected=np.array([[-.0001,-.0001,0],[.0001,-.0001,0],[.0001,.0001,0],[-.0001,.0001,0]],'<f4')
    return (len(mesh.vertices)==4 and len(mesh.polygons)==1
            and tuple(mesh.polygons[0].vertices)==(0,1,2,3)
            and not mesh.shape_keys and len(obj.vertex_groups)==1
            and obj.vertex_groups[0].name=='C_Hip'
            and all(len(v.groups)==1 and v.groups[0].group==0 and v.groups[0].weight==1 for v in mesh.vertices)
            and np.array_equal(_coords(mesh.vertices),expected))


def _write_hip_stub(out,src,part,obj,rotate):
    """Store the operator's tiny quad without renumbering any later vertex slots."""
    if any(True for _ in src.part_shapes(part)):
        raise ValueError('Cannot stub a part with corrective shapes in source mode')
    if part['count']<4 or part['faces']<2:
        raise ValueError('Source part is too small for a C_Hip stub')
    sk=src.skeleton_offset
    bone_count,remap_count=_unpack(src.data,'II',sk)
    names=[src.names[i] for i in _unpack(src.data,'H'*bone_count,src.bone_names_offset)]
    remap=_unpack(src.data,'H'*remap_count,sk+48)
    hip=list(remap).index(names.index('C_Hip'))
    if hip>1023:
        raise ValueError('C_Hip cannot be represented by SF6 weights')
    positions=np.zeros((part['count'],3),'<f4')
    positions[:4]=_from_blender(_coords(obj.data.vertices),rotate)
    start=src.elements[0][1]+part['start']*12
    out[start:start+len(positions)*12]=positions.tobytes()
    # Six 10-bit bone indices, with two padding bits after each set of three.
    weight=struct.pack('<Q8B',hip,255,0,0,0,0,0,0,0)
    start=src.elements[4][1]+part['start']*16
    out[start:start+part['count']*16]=weight*part['count']
    faces=np.zeros((part['faces'],3),'<u'+str(src.index_size))
    faces[:2]=((0,1,2),(0,2,3))
    start=src.face_offset+part['face_start']*src.index_size
    out[start:start+faces.nbytes]=faces.tobytes()
    if 2 in src.elements:
        uv=np.zeros((part['count'],2),'<f2')
        uv[:4]=((0,1),(1,1),(1,0),(0,0))
        start=src.elements[2][1]+part['start']*4
        out[start:start+uv.nbytes]=uv.tobytes()


def attach_source(filepath, collection, objects, rotate=True, source=None):
    """Attach verified retail metadata after the clean upstream geometry importer."""
    import bpy
    if source is None:
        with open(filepath, 'rb') as source_file:
            source = SourceMesh(source_file.read())
    src = source
    data = src.data
    source_hash = hashlib.sha256(data).hexdigest()
    text_name = '.SF6_SOURCE_'+source_hash[:40]
    txt = bpy.data.texts.get(text_name)
    if txt is None:
        txt = bpy.data.texts.new(text_name)
        encoded=base64.b64encode(zlib.compress(data)).decode('ascii')
        # Blender's text insertion is quadratic for a multi-megabyte single line.
        txt.from_string('\n'.join(encoded[i:i+1024] for i in range(0,len(encoded),1024)))
    txt.use_fake_user = True
    collection[SOURCE] = txt.name
    collection['SF6SourceSHA256'] = source_hash
    collection['SF6SourceRotate'] = rotate
    collection['SF6PreserveSource'] = True
    by_start = {}
    for part in src.parts:
        by_start.setdefault(part['start'],part)
    for start, obj in objects.items():
        part = by_start[start]
        mesh = obj.data
        if len(mesh.vertices) != part['count'] or len(mesh.polygons) != part['faces']:
            raise ValueError('Import changed topology: '+obj.name)
        obj[META] = json.dumps(part)
        obj['SF6SourceSHA256'] = source_hash
        for name, domain, n in ((VERTEX_ID,'POINT',len(mesh.vertices)),(FACE_ID,'FACE',len(mesh.polygons))):
            a=mesh.attributes.new(name,'INT',domain)
            a.data.foreach_set('value', np.arange(n,dtype=np.int32))
        basis = _to_blender(src.positions(part),rotate)
        mesh.vertices.foreach_set('co',basis.ravel())
        shapes = list(src.part_shapes(part))
        if shapes:
            obj.shape_key_add(name='Basis',from_mix=False)
            for name, delta, _ in shapes:
                key = obj.shape_key_add(name=name,from_mix=False)
                key.data.foreach_set('co',(basis+_to_blender(delta,rotate)).ravel())
        mesh.update()
        # Snapshot editable channels Blender represents differently from raw SF6.
        obj['SF6OriginalUV'] = json.dumps([list(uv.uv) for uv in mesh.uv_layers[0].data]) if mesh.uv_layers else '[]'
        obj['SF6OriginalWeights'] = json.dumps([[(obj.vertex_groups[g.group].name,g.weight) for g in v.groups] for v in mesh.vertices])
        obj['SF6OriginalMaterials'] = json.dumps([m.name if m else None for m in mesh.materials])
    armatures = [o for o in collection.all_objects if o.type == 'ARMATURE']
    collection['SF6ArmatureSignature'] = json.dumps(_armature_signature(armatures[0] if armatures else None))
    collection['SF6ImportedParts'] = json.dumps([json.loads(o[META]) for o in objects.values()])
    return src


def _validate_materials(obj):
    # Object-linked materials can differ from the mesh data's material slots.
    current = [slot.material.name if slot.material else None for slot in obj.material_slots]
    if json.dumps(current) != obj['SF6OriginalMaterials']:
        raise ValueError('Material reassignment is unsupported in source mode: '+obj.name)
    # Each source submesh has a single material, even if a polygon's slot index
    # has been changed without adding a new material slot.
    if any(face.material_index != 0 for face in obj.data.polygons):
        raise ValueError('Face material reassignment is unsupported in source mode: '+obj.name)


def _validate_uvs(src, part, mesh, ids, object_name):
    kinds = [kind for kind in (2, 3) if kind in src.elements]
    if len(mesh.uv_layers) != len(kinds):
        raise ValueError('UV layers were added or removed: '+object_name)
    # Compare every layer with the embedded source. This also works for saved
    # projects from the first preservation build, which only snapshot UV0.
    loop_vertices = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get('vertex_index', loop_vertices)
    for layer, kind in zip(mesh.uv_layers, kinds):
        stride, offset = src.elements[kind]
        if stride != 4:
            raise ValueError('Unsupported source UV stride: '+object_name)
        expected = np.frombuffer(src.data, '<f2', part['count']*2,
                                 offset+part['start']*stride).reshape(-1, 2).copy()
        # Match the importer's half-float V conversion, including rounding.
        expected[:, 1] *= -1
        expected[:, 1] += 1
        expected = expected.astype('<f4')[ids[loop_vertices]]
        current = np.empty((len(layer.data), 2), '<f4')
        layer.data.foreach_get('uv', current.ravel())
        if not np.array_equal(current, expected):
            raise ValueError('UV edits are unsupported in source mode ('+layer.name+'): '+object_name)


def export_source(filepath, collection, options):
    """Patch a source copy, validate all edits, then atomically replace destination."""
    import bpy
    text = bpy.data.texts.get(collection.get(SOURCE,''))
    if text is None:
        raise ValueError('Missing embedded SF6 source. Re-import the original mesh.')
    data = zlib.decompress(base64.b64decode(text.as_string()))
    if hashlib.sha256(data).hexdigest() != collection['SF6SourceSHA256']:
        raise ValueError('Embedded SF6 source hash mismatch')
    src = SourceMesh(data)
    out = bytearray(data)
    rotate = collection['SF6SourceRotate']
    if options.get('selectedOnly'):
        raise ValueError('SF6 source mode exports the source collection; Selected Only is unsupported')
    if options.get('rotate90',True) != rotate:
        raise ValueError('Use the same axis conversion setting as the source import')
    armatures = [o for o in collection.all_objects if o.type == 'ARMATURE']
    signature = _armature_signature(armatures[0] if armatures else None)
    if json.dumps(signature) != collection['SF6ArmatureSignature']:
        raise ValueError('SF6 source mode cannot change the skeleton. Restore the imported armature.')
    from mathutils import Matrix
    if any(o.matrix_world != Matrix.Identity(4) for o in armatures):
        raise ValueError('SF6 source mode requires the imported armature transform')
    edits = {'positions':0,'shape_vertices':0,'removed_faces':0,'removed_objects':0,'hip_stubs':0}
    objects = {}
    for obj in collection.all_objects:
        if obj.type != 'MESH' or obj.get('~TYPE'):
            continue
        if META not in obj or obj.get('SF6SourceSHA256') != collection['SF6SourceSHA256']:
            raise ValueError('New/replacement objects require a rebuilt exporter: '+obj.name)
        part = json.loads(obj[META])
        if part not in src.parts:
            raise ValueError('Source metadata was modified: '+obj.name)
        if part['start'] in objects:
            raise ValueError('Duplicated source mesh: '+obj.name)
        objects[part['start']] = obj
    for part in json.loads(collection['SF6ImportedParts']):
        if part not in src.parts:
            raise ValueError('Collection source metadata was modified')
        obj = objects.get(part['start'])
        face_base = src.face_offset+part['face_start']*src.index_size
        if obj is None or obj.get('MeshExportExclude'):
            out[face_base:face_base+part['faces']*3*src.index_size] = bytes(part['faces']*3*src.index_size)
            edits['removed_faces'] += part['faces']
            edits['removed_objects'] += 1
            continue
        if obj.mode != 'OBJECT':
            raise ValueError('Leave Edit Mode before exporting '+obj.name)
        if obj.matrix_world != Matrix.Identity(4):
            raise ValueError('Apply position edits in Edit Mode; object transforms are unsupported: '+obj.name)
        if any(m.type != 'ARMATURE' and (m.show_viewport or m.show_render) for m in obj.modifiers):
            raise ValueError('Apply/remove non-armature modifiers before source export: '+obj.name)
        _validate_materials(obj)
        if _is_hip_stub(obj):
            _write_hip_stub(out,src,part,obj,rotate)
            edits['hip_stubs']+=1
            continue
        mesh = obj.data
        ids = _ids(mesh, VERTEX_ID,len(mesh.vertices))
        if len(set(ids.tolist())) != len(ids) or (len(ids) and (ids.min()<0 or ids.max()>=part['count'])):
            raise ValueError('Added/duplicated vertices are unsupported in source mode: '+obj.name)
        fids = _ids(mesh,FACE_ID,len(mesh.polygons))
        if len(set(fids.tolist()))!=len(fids) or (len(fids) and (fids.min()<0 or fids.max()>=part['faces'])):
            raise ValueError('Added/duplicated faces are unsupported in source mode: '+obj.name)
        original_faces = src.faces(part)
        for face, fid in zip(mesh.polygons,fids):
            if len(face.vertices)!=3 or not np.array_equal(ids[list(face.vertices)],original_faces[fid]):
                raise ValueError('Rewired topology is unsupported in source mode: '+obj.name)
        for fid in set(range(part['faces']))-set(fids.tolist()):
            off=face_base+fid*3*src.index_size
            out[off:off+3*src.index_size]=bytes(3*src.index_size)
            edits['removed_faces']+=1
        original_weights=json.loads(obj['SF6OriginalWeights'])
        for v, vid in zip(mesh.vertices,ids):
            current=sorted((obj.vertex_groups[g.group].name,g.weight) for g in v.groups)
            original=sorted(tuple(g) for g in original_weights[vid])
            if current!=original:
                raise ValueError('Weight edits are unsupported in source mode: '+obj.name)
        _validate_uvs(src, part, mesh, ids, obj.name)
        keys=mesh.shape_keys.key_blocks if mesh.shape_keys else None
        expected_shapes={name:(delta,ranges) for name,delta,ranges in src.part_shapes(part)}
        actual_names=set(k.name for k in list(keys)[1:]) if keys else set()
        if actual_names != set(expected_shapes):
            raise ValueError('Shape keys were added/renamed/removed: '+obj.name)
        if keys and (not mesh.shape_keys.use_relative or any(k.relative_key != keys[0] or k.vertex_group for k in list(keys)[1:])):
            raise ValueError('Source shapes must be relative to Basis, without vertex-group masks')
        basis=_coords(keys[0].data if keys else mesh.vertices)
        original_basis=_to_blender(src.positions(part),rotate)[ids]
        changed=np.any(basis!=original_basis,axis=1)
        if not np.isfinite(basis).all():
            raise ValueError('Non-finite vertex positions: '+obj.name)
        for vi in np.flatnonzero(changed):
            off=src.elements[0][1]+(part['start']+int(ids[vi]))*12
            out[off:off+12]=_from_blender(basis[vi:vi+1],rotate).astype('<f4').tobytes()
        edits['positions']+=int(changed.sum())
        for name,(delta,ranges) in expected_shapes.items():
            coords=_coords(keys[name].data)
            expected=original_basis+_to_blender(delta[ids],rotate)
            changed=np.any(coords!=expected,axis=1) | np.any(basis!=original_basis,axis=1)
            new_delta=_from_blender(coords-basis,rotate)
            if not np.isfinite(new_delta).all() or (abs(new_delta)>65504).any():
                raise ValueError('Invalid shape coordinates: '+name)
            covered=np.zeros(len(ids),bool)
            for start,offset,n in ranges:
                valid=(ids+part['start']>=start)&(ids+part['start']<start+n)
                covered|=valid
                for vi in np.flatnonzero(changed&valid):
                    off=offset+(part['start']+int(ids[vi])-start)*8
                    out[off:off+6]=new_delta[vi].astype('<f2').tobytes()
                    edits['shape_vertices']+=1
            if np.any(changed&~covered&np.any(new_delta!=0,axis=1)):
                raise ValueError('Shape edit extends outside its authored range: '+name)
    # All source LODs stay present, including levels that were not imported.
    directory=os.path.dirname(os.path.abspath(filepath))
    fd,temp=tempfile.mkstemp(prefix='.sf6-',suffix='.tmp',dir=directory)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(out)
        os.replace(temp,filepath)
    finally:
        if os.path.exists(temp):
            os.remove(temp)
    print('SF6 source export:',json.dumps(edits),'; all source deformation tables and LODs retained')
    return edits
