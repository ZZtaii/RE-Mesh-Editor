"""Automatic, private normalization of mixed/Joined SF6 hybrid inputs.

No donor assignments are written to user objects. Verified origin attributes
survive Object Join; unrelated material/origin sections become ordinary parts.
"""
from contextlib import contextmanager
import json
import math
import re
import bpy
import numpy as np
from . import sf6_source
from .sf6_hybrid import _need, _part_key, _parts_by_key, _part_shapes

AUTO='SF6AutomaticOrdinaryPart'
ORIGIN=[f'sf6_origin_hash_{i}' for i in range(4)]+['sf6_origin_part']

def token(source_hash,part):
    words=[int(source_hash[i*8:i*8+8],16) for i in range(4)]
    return tuple(v-2**32 if v>=2**31 else v for v in words)+(part['start']+1,)

def origins(mesh):
    columns=[]
    for name in ORIGIN:
        a=mesh.attributes.get(name)
        if a is None or a.domain!='POINT' or a.data_type!='INT':return None
        v=np.empty(len(mesh.vertices),np.int32);a.data.foreach_get('value',v);columns.append(v)
    return np.stack(columns,axis=1)

def meshes(collection,selected=None):
    return [o for o in collection.all_objects if o.type=='MESH' and not o.get('~TYPE') and (selected is None or o in selected)]

def native_part(obj,source,source_hash):
    if obj.get('SF6SourceSHA256')!=source_hash:return None
    try:p=json.loads(obj.get(sf6_source.META,''))
    except (ValueError,TypeError):return None
    return p if p in source.parts else None

def needs_automatic(collection,source,source_hash,selected=None):
    original=_parts_by_key(source);found=set()
    for o in meshes(collection,selected):
        p=native_part(o,source,source_hash)
        if p is not None and p['lod']!=0:continue
        if p is None:return True
        key=_part_key(source,p)
        if key in found:return True
        found.add(key)
        rows=origins(o.data)
        if rows is not None:
            unique={tuple(r) for r in rows if r[-1]!=0}
            if unique and unique!={token(source_hash,p)}:return True
        elif len(o.data.vertices)!=p['count']:
            return True
        for name in (sf6_source.VERTEX_ID,sf6_source.FACE_ID):
            attr=o.data.attributes.get(name)
            if attr is not None and attr.data_type=='INT':
                values=[v.value for v in attr.data if v.value>=0]
                if len(values)!=len(set(values)):return True
        used={poly.material_index for poly in o.data.polygons}
        if len(used)>1:return True
        keys=o.data.shape_keys
        actual={k.name for k in list(keys.key_blocks)[1:]} if keys else set()
        if actual!=set(_part_shapes(source,p)) and not sf6_source._is_hip_stub(o):return True
        if keys and actual and (not keys.use_relative or any(k.relative_key!=keys.key_blocks[0] or k.vertex_group for k in list(keys.key_blocks)[1:])):return True
    # Joined source objects disappear from the collection. In automatic mode
    # the actual collection is authoritative; omissions are reported, not guessed.
    if selected is None and found!=set(original):
        return True
    return False

def validate_rig(obj,arm):
    mods=[m for m in obj.modifiers if m.type=='ARMATURE' and m.show_viewport]
    _need(len(mods)==1 and mods[0].object==arm,
          'Bind mixed geometry to the target character armature: '+obj.name)
    names={g.index:g.name for g in obj.vertex_groups}
    for v in obj.data.vertices:
        weights=[g for g in v.groups if g.weight>0]
        _need(weights and all(g.group in names and math.isfinite(g.weight) and g.weight>=0 for g in v.groups),
              'Mixed geometry has invalid or unweighted vertices: '+obj.name)
        _need(all(names[g.group] in arm.data.bones for g in weights),
              'Mixed geometry weights reference missing target bones: '+obj.name)

def _material(obj,index,native=None,source=None):
    if native is not None:return source.materials[native['material']]
    _need(0<=index<len(obj.material_slots) and obj.material_slots[index].material is not None,
          'Assign a material to added geometry: '+obj.name)
    # Match the ordinary writer's established material basename contract.
    return obj.material_slots[index].material.name.split('.')[0]

def _clone_section(obj,face_ids,material_index,keep_keys,owned_objects,owned_meshes):
    """Copy an exact indexed polygon subset, including authored corner channels."""
    old=obj.data
    faces=[old.polygons[i] for i in face_ids]
    indices=sorted({int(v) for f in faces for v in f.vertices})
    row={v:i for i,v in enumerate(indices)}
    corners=[i for f in faces for i in f.loop_indices]
    base=old.shape_keys.key_blocks[0].data if old.shape_keys else old.vertices
    data=bpy.data.meshes.new('__SF6_AUTO_GEOMETRY');owned_meshes.append(data)
    data.from_pydata([base[i].co[:] for i in indices],[],[[row[v] for v in f.vertices] for f in faces])
    copied=obj.copy();copied.data=data;owned_objects.append(copied)
    for n in list(copied.keys()):
        if n.startswith('SF6Source') or n=='SF6HybridDonorSlotV1':del copied[n]
    if 0<=material_index<len(obj.material_slots):data.materials.append(obj.material_slots[material_index].material)
    for layer in old.uv_layers:
        new=data.uv_layers.new(name=layer.name)
        new.data.foreach_set('uv',np.array([layer.data[i].uv[:] for i in corners],np.float32).ravel())
        new.active_render=layer.active_render
    if old.uv_layers:data.uv_layers.active_index=old.uv_layers.active_index
    for attr in old.color_attributes:
        mapping=indices if attr.domain=='POINT' else corners if attr.domain=='CORNER' else None
        if mapping is None:continue
        new=data.color_attributes.new(name=attr.name,type=attr.data_type,domain=attr.domain)
        new.data.foreach_set('color',np.array([attr.data[i].color[:] for i in mapping],np.float32).ravel())
    if old.color_attributes.active_color:
        data.color_attributes.active_color_name=old.color_attributes.active_color.name
    for attr in old.attributes:
        if attr.data_type!='INT' or attr.name.startswith('.') or attr.name in data.attributes:continue
        mapping=indices if attr.domain=='POINT' else face_ids if attr.domain=='FACE' else corners if attr.domain=='CORNER' else None
        if mapping is not None:
            new=data.attributes.new(attr.name,'INT',attr.domain)
            new.data.foreach_set('value',np.array([attr.data[i].value for i in mapping],np.int32))
    for f,original in zip(data.polygons,faces):f.use_smooth=original.use_smooth
    if len(corners):data.normals_split_custom_set([old.corner_normals[i].vector[:] for i in corners])
    # Replacing Object.data can clear group definitions in Blender.
    copied.vertex_groups.clear()
    for group in obj.vertex_groups:copied.vertex_groups.new(name=group.name)
    for vi,old_index in enumerate(indices):
        for g in old.vertices[old_index].groups:copied.vertex_groups[g.group].add([vi],g.weight,'REPLACE')
    if keep_keys and old.shape_keys:
        basis=copied.shape_key_add(name='Basis',from_mix=False)
        for name in keep_keys:
            key=copied.shape_key_add(name=name,from_mix=False)
            key.data.foreach_set('co',np.array([old.shape_keys.key_blocks[name].data[i].co[:] for i in indices],np.float32).ravel())
            key.relative_key=basis
    data.update()
    return copied

@contextmanager
def automatic_collection(collection,source,source_hash,selected_objects=None):
    selected=None if selected_objects is None else set(selected_objects)
    parts=_parts_by_key(source)
    by_token={token(source_hash,p):p for p in parts.values()}
    candidates=[];dropped=[]
    arms=[o for o in collection.objects if o.type=='ARMATURE']
    _need(len(arms)==1,'Automatic hybrid needs one target armature on the collection root')
    arm=arms[0]
    for obj in meshes(collection,selected):
        _need(obj.mode=='OBJECT','Leave Edit Mode before exporting '+obj.name)
        p=native_part(obj,source,source_hash)
        if p is not None and p['lod']!=0:continue
        rows=origins(obj.data)
        groups={}
        for f in obj.data.polygons:
            origin=None
            if rows is not None:
                tokens={tuple(rows[v]) for v in f.vertices}
                if len(tokens)==1:origin=next(iter(tokens))
            # Legacy intact imported objects retain their existing checks.
            if rows is None and p is not None and len(obj.data.vertices)==p['count']:
                origin=token(source_hash,p)
            groups.setdefault((origin,f.material_index),[]).append(f.index)
        _need(groups,'Mixed mesh has no faces: '+obj.name)
        for (origin,mat),face_ids in groups.items():
            native=by_token.get(origin)
            keys=obj.data.shape_keys
            expected=list(_part_shapes(source,native)) if native is not None else []
            actual={k.name for k in list(keys.key_blocks)[1:]} if keys else set()
            reason=None
            if native is not None and expected and (not keys or not keys.use_relative or any(
                    keys.key_blocks[name].relative_key!=keys.key_blocks[0] or keys.key_blocks[name].vertex_group
                    for name in expected if name in actual)):
                reason='Non-Basis-relative or masked target keys';native=None
            if native is not None and not set(expected)<=actual:
                reason='Missing compatible target shape keys';native=None
            if native is not None:
                # A slot with changed material is ordinary geometry, not a
                # reason to rewrite other parts' source shape identities.
                original_name=source.materials[native['material']]
                if _material(obj,mat)!=original_name:
                    reason='Material changed on source section';native=None
            if native is not None:
                va=obj.data.attributes.get(sf6_source.VERTEX_ID)
                fa=obj.data.attributes.get(sf6_source.FACE_ID)
                used_rows=sorted({v for fi in face_ids for v in obj.data.polygons[fi].vertices})
                valid=(va is not None and fa is not None and va.domain=='POINT' and fa.domain=='FACE')
                if valid:
                    vids=[va.data[v].value for v in used_rows]
                    fids=[fa.data[f].value for f in face_ids]
                    valid=(len(vids)==len(set(vids)) and len(fids)==len(set(fids)) and
                           all(0<=v<native['count'] for v in vids) and all(0<=f<native['faces'] for f in fids))
                if not valid:
                    reason='Ambiguous or missing joined source row identities';native=None
            if native is None:
                validate_rig(obj,arm)
                reason=reason or ('Foreign or unproven vertex origin')
                expected=[]
                dropped.append({'object':obj.name,'material':_material(obj,mat),'reason':reason})
            candidates.append(dict(obj=obj,native=native,faces=face_ids,material_index=mat,keys=expected,
                                   material=_material(obj,mat,native,source),reason=reason))
    # Two objects claiming the same source part are not both safe copies.
    # Rebuild both without shapes, leaving unrelated parts fully preserved.
    counts={}
    for c in candidates:
        if c['native'] is not None:
            k=_part_key(source,c['native']);counts[k]=counts.get(k,0)+1
    for c in candidates:
        if c['native'] is not None and counts[_part_key(source,c['native'])]>1:
            validate_rig(c['obj'],arm)
            c['native']=None;c['keys']=[];c['reason']='Source part split or duplicated; identity is ambiguous'
            dropped.append({'object':c['obj'].name,'material':c['material'],'reason':c['reason']})
    kept={_part_key(source,c['native']) for c in candidates if c['native'] is not None}
    _need(kept,'No compatible target-source parts remain; use ordinary export for an entirely replaced mesh')
    used=set(kept)
    for c in candidates:
        if c['native'] is not None:c['key']=_part_key(source,c['native']);continue
        match=re.search(r'Group_(\d+)_',c['obj'].name)
        preferred=int(match[1]) if match else 0
        choices=([preferred] if 0<=preferred<=255 else [])+list(range(256))
        group=next((g for g in choices if (g,c['material']) not in used),None)
        _need(group is not None,'No free group/material identity for added part '+c['obj'].name)
        c['key']=(group,c['material']);used.add(c['key'])
    objects=[];meshes_owned=[];collections=[];mapped_selection=[]
    try:
        prep=bpy.data.collections.new('__SF6_AUTO_'+collection.name);collections.append(prep)
        for k in collection.keys():prep[k]=collection[k]
        prep['SF6AutomaticHybrid']=True
        bpy.context.scene.collection.children.link(prep)
        lod=bpy.data.collections.new('__SF6_AUTO_Main Mesh LOD0');collections.append(lod);prep.children.link(lod)
        for child in collection.children:
            if 'Main Mesh LOD0' in child.name:
                for k in child.keys():lod[k]=child[k]
                break
        # Auxiliaries are read-only links; evaluator will own the actual copies.
        for obj in collection.all_objects:
            if obj.type=='ARMATURE' or obj.get('~TYPE'):prep.objects.link(obj)
        for c in candidates:
            o=c['obj'];p=c['native']
            direct=(p is not None and native_part(o,source,source_hash)==p and len(c['faces'])==len(o.data.polygons)
                    and set(c['keys'])==({k.name for k in list(o.data.shape_keys.key_blocks)[1:]} if o.data.shape_keys else set()))
            if direct:
                lod.objects.link(o);copied=o
            else:
                # Splitting before topology-generating modifiers can change
                # their output. Refuse until post-evaluation provenance can
                # establish this case, rather than silently alter the geometry.
                _need(not any(m.type!='ARMATURE' and m.show_viewport for m in o.modifiers),
                      'Apply non-armature modifiers on joined/material-split geometry before hybrid export: '+o.name)
                copied=_clone_section(o,c['faces'],c['material_index'],c['keys'],objects,meshes_owned)
                lod.objects.link(copied)
                group,material=c['key'];sub=p['sub'] if p is not None else 0
                copied.name=f'Group_{group}_Sub_{sub}__{material}'
                if p is not None:
                    copied[sf6_source.META]=json.dumps(p);copied['SF6SourceSHA256']=source_hash
                else:copied[AUTO]=True
            mapped_selection.append(copied)
        report={'ordinary_sections':dropped,'retained_source_parts':len(kept),
                'omitted_source_parts':[{'group':k[0],'material':k[1]} for k in sorted(set(parts)-kept)],
                'input_mesh_objects':len(meshes(collection,selected)), 'output_sections':len(candidates),
                'new_materials':sorted({c['material'] for c in candidates}-set(source.materials))}
        yield prep,tuple(mapped_selection),report
    finally:
        for o in reversed(objects):bpy.data.objects.remove(o,do_unlink=True)
        for c in reversed(collections):bpy.data.collections.remove(c)
        for m in reversed(meshes_owned):
            if m.users==0:bpy.data.meshes.remove(m)


def has_shape_geometry(collection):
    return collection is not None and any(o.type=='MESH' and o.data.shape_keys and len(o.data.shape_keys.key_blocks)>1
                                          for o in collection.all_objects if not o.get('~TYPE'))


@contextmanager
def source_collection_view(collection,selected_objects=None):
    """Resolve one embedded preserved source from shaped geometry in any collection.

    This lets users bring Luke's preserved body into an ordinary Leon collection.
    Multiple source files are ambiguous; never pick a character by name/order.
    """
    _need(collection is not None,'Choose an export mesh collection')
    selected=None if selected_objects is None else set(selected_objects)
    selected_meshes = meshes(collection, selected)
    if selected is not None:
        _need(selected_meshes, 'No selected mesh objects in the target collection. Select objects or disable Selected Objects Only.')
    full_hashes=set();prefixes=set()
    for obj in selected_meshes:
        if not obj.data.shape_keys or len(obj.data.shape_keys.key_blocks)<2:continue
        h=obj.get('SF6SourceSHA256')
        if isinstance(h,str):full_hashes.add(h)
        rows=origins(obj.data)
        if rows is not None:
            prefixes.update(''.join(f'{int(v)&0xffffffff:08x}' for v in r[:4]) for r in rows if r[-1]!=0)
    matches={}
    for c in bpy.data.collections:
        h=c.get('SF6SourceSHA256')
        if c.get('SF6PreserveSource') and isinstance(h,str) and (h in full_hashes or h[:32] in prefixes):matches[h]=c
    _need(len(matches)==1,'Cannot identify one preserved SF6 source for these shapes. Export from the intended original Preserve-import collection; keep its embedded source available.')
    anchor=next(iter(matches.values()))
    view=bpy.data.collections.new('__SF6_SOURCE_VIEW_'+collection.name)
    try:
        for k in collection.keys():view[k]=collection[k]
        for k in anchor.keys():
            if k.startswith('SF6'):view[k]=anchor[k]
        bpy.context.scene.collection.children.link(view)
        for o in collection.all_objects:view.objects.link(o)
        yield view,anchor.name
    finally:bpy.data.collections.remove(view)
