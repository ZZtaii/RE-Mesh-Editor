"""Automatic, private normalization of mixed/Joined SF6 hybrid inputs.

No donor assignments are written to user objects. Verified origin attributes
survive Object Join; unrelated material/origin sections become ordinary parts.
"""
from .sf6_mode_snapshot import logical_name, logical_pointer
from contextlib import contextmanager
import json
import hashlib
from contextvars import ContextVar
CURRENT_EXPORT_PLAN=ContextVar("sf6_export_plan",default=None)
# A private preview may record losses without authorizing publication. Public
# execution refuses to run under this collector (see sf6_unified_export).
CURRENT_REVIEW=ContextVar("sf6_private_review",default=None)
import math
import re
import bpy
import numpy as np
from . import sf6_source
from .sf6_hybrid import HybridExportError, _need, _part_key, _parts_by_key, _part_shapes

class CorrectiveLossConsentRequired(HybridExportError):
    """Exact backend loss event; presentation must not infer scope from prose."""
    def __init__(self, token, object_name, reason, shape_names, *, scope='section',
                 material=None, materials=(), face_ids=(), object_pointer=None,
                 source_status='verified', source_reason=None, source_key=None,
                 output_scope=None):
        self.loss_token = token
        self.object_name = object_name
        self.reason = reason
        self.shape_names = tuple(sorted(set(shape_names)))
        self.scope = scope
        self.material = material
        self.materials = tuple(materials)
        self.face_ids = tuple(face_ids)
        self.object_pointer = object_pointer
        self.source_status = source_status
        self.source_reason = source_reason
        self.source_key = source_key
        self.output_scope = output_scope
        super().__init__('PROTOTYPE_CORRECTIVE_LOSS: '+reason+': '+object_name)

    def as_dict(self):
        return dict(loss_token=self.loss_token, object=self.object_name,
                    reason=self.reason, shape_names=list(self.shape_names),
                    lost_corrective_names=list(self.shape_names), scope=self.scope,
                    material=self.material, materials=list(self.materials),
                    face_ids=list(self.face_ids), object_pointer=self.object_pointer,
                    source_status=self.source_status, source_reason=self.source_reason,
                    source_key=self.source_key, output_scope=self.output_scope)

class DuplicateOwnerChoiceRequired(HybridExportError):
    def __init__(self, groups):
        self.groups=groups
        super().__init__('UNIFIED_DUPLICATE_OWNER_CHOICE_REQUIRED')

def source_key(part_key):
    """Stable JSON identity, independent of object naming or collection copies."""
    return json.dumps(list(part_key),separators=(',',':'),ensure_ascii=False)

def owner_selected(owners,part_key,obj):
    if isinstance(owners,dict):return owners.get(source_key(part_key))==logical_pointer(obj)
    return logical_pointer(obj) in set(owners)

def require_corrective_loss(obj,source_hash,face_ids,reason,expected,approved,*,
                            scope='section',source_status='verified',source_reason=None,
                            source_key=None,output_scope=None):
    face_ids=tuple(sorted(set(face_ids)))
    materials=sorted({_material(obj,obj.data.polygons[index].material_index) for index in face_ids})
    consent=corrective_loss_token(obj,source_hash,face_ids,reason,expected,
                                  scope=scope,source_status=source_status)
    event=CorrectiveLossConsentRequired(consent,logical_name(obj),reason,expected,
        scope=scope,material=materials[0] if len(materials)==1 else None,
        materials=materials,face_ids=face_ids,object_pointer=logical_pointer(obj),
        source_status=source_status,source_reason=source_reason,
        source_key=source_key,output_scope=output_scope)
    review=CURRENT_REVIEW.get()
    if review is not None:
        review['losses'][consent]=event.as_dict()
        if review.get('preview'):return consent
    if consent not in set(approved):raise event
    return consent

def corrective_loss_token(obj, source_hash, face_ids, reason, expected, *, scope='section', source_status='verified'):
    # One-session consent bound to exact data/loss scope, not explanatory wording.
    # Identical whole-object loss discovered twice must not ask twice.
    keys=obj.data.shape_keys
    state=[(k.name,k.relative_key.name,k.vertex_group) for k in list(keys.key_blocks)[1:]] if keys else []
    payload=[CURRENT_EXPORT_PLAN.get(),scope,source_status,source_hash,logical_pointer(obj),logical_pointer(obj.data),logical_name(obj),
             sorted(set(face_ids)),sorted(set(expected)),state,keys.use_relative if keys else None,
             [slot.material.name if slot.material else None for slot in obj.material_slots],
             [list(row) for row in obj.matrix_world],
             [(tuple(poly.vertices),poly.material_index) for poly in obj.data.polygons]]
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode())
    for values in ([key.data for key in keys.key_blocks] if keys else [obj.data.vertices]):
        coordinates=np.empty(len(values)*3,np.float32);values.foreach_get('co',coordinates);digest.update(coordinates.tobytes())
    return digest.hexdigest()

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

def non_trs_world(obj):
    from mathutils import Matrix
    world=obj.matrix_world
    location,rotation,scale=world.decompose()
    reconstructed=Matrix.LocRotScale(location,rotation,scale)
    return not np.allclose(np.asarray(world),np.asarray(reconstructed),rtol=1e-6,atol=1e-7)

def shape_range_overflow(obj,source,part,face_ids=None):
    """New deltas outside source-authored ranges require Ordinary fallback."""
    if part is None or not obj.data.shape_keys:return False
    keys=obj.data.shape_keys.key_blocks
    if not obj.data.shape_keys.use_relative:return False
    attr=obj.data.attributes.get(sf6_source.VERTEX_ID)
    if attr is None or attr.domain!='POINT' or attr.data_type!='INT':return False
    vertices=sorted({int(v) for fi in face_ids for v in obj.data.polygons[fi].vertices}) if face_ids is not None else list(range(len(obj.data.vertices)))
    vertices=np.asarray(vertices,np.int64)
    all_ids=np.empty(len(obj.data.vertices),np.int32);attr.data.foreach_get('value',all_ids)
    ids=all_ids[vertices].astype(np.int64)+part['start']
    authored={}
    for name,ranges in source.shapes[part['lod']]:authored.setdefault(name,[]).extend(ranges)
    basis=sf6_source._coords(keys[0].data)[vertices]
    for name in _part_shapes(source,part):
        key=keys.get(name)
        if key is None or key.relative_key!=keys[0] or key.vertex_group:continue
        covered=np.zeros(len(vertices),bool)
        for start,offset,count in authored.get(name,[]):covered|=(ids>=start)&(ids<start+count)
        if np.all(covered):continue
        coords=sf6_source._coords(key.data)[vertices]
        if np.any(np.any(coords!=basis,axis=1)&~covered):return True
    return False

def needs_automatic(collection,source,source_hash,selected=None):
    original=_parts_by_key(source);found=set()
    for o in meshes(collection,selected):
        p=native_part(o,source,source_hash)
        if p is not None and p['lod']!=0:continue
        if p is None or non_trs_world(o):return True
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
            domain='POINT' if name==sf6_source.VERTEX_ID else 'FACE'
            size=len(o.data.vertices) if domain=='POINT' else len(o.data.polygons)
            if attr is None or attr.data_type!='INT' or attr.domain!=domain or len(attr.data)!=size:return True
            if attr is not None and attr.data_type=='INT':
                values=[v.value for v in attr.data if v.value>=0]
                if len(values)!=len(set(values)):return True
        used={poly.material_index for poly in o.data.polygons}
        if len(used)>1:return True
        # Even a one-slot assignment can differ from the embedded source.
        # Route it through normalization so it cannot be silently ignored.
        if any(not _source_material_matches(o,index,source.materials[p['material']]) for index in used):return True
        if shape_range_overflow(o,source,p):return True
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
    # Ordinary sections bake the same modifier evaluation as Ordinary export.
    # Their serialized weights must still reference the validated collection rig.
    names={g.index:g.name for g in obj.vertex_groups}
    for v in obj.data.vertices:
        weights=[g for g in v.groups if g.weight>0]
        _need(weights and all(g.group in names and math.isfinite(g.weight) and g.weight>=0 for g in v.groups),
              'Mixed geometry has invalid or unweighted vertices: '+logical_name(obj))
        _need(all(names[g.group] in arm.data.bones for g in weights),
              'Mixed geometry weights reference missing target bones: '+logical_name(obj))

def _material(obj,index,native=None,source=None):
    if native is not None:return source.materials[native['material']]
    _need(0<=index<len(obj.material_slots) and obj.material_slots[index].material is not None,
          'Assign a material to every used mesh section: '+logical_name(obj))
    # Match the ordinary writer's established material basename contract.
    return obj.material_slots[index].material.name.split('.')[0]

def _source_material_matches(obj,index,original_name):
    basename=_material(obj,index)  # Also validate empty/missing used slots.
    name=obj.material_slots[index].material.name
    # Source identities can contain dots; do not mistake a literal source
    # name or Blender's numeric duplicate suffix for a reassignment. Keep
    # the ordinary writer's basename contract for ordinary sections only.
    return (name==original_name or basename==original_name or
            re.fullmatch(re.escape(original_name)+r'\.\d{3,}',name) is not None)

def _clone_section(obj,face_ids,material_index,keep_keys,owned_objects,owned_meshes):
    """Copy an exact indexed polygon subset, including authored corner channels."""
    old=obj.data
    if not keep_keys and old.shape_keys is None and len(face_ids)==len(old.polygons) and all(p.material_index==material_index for p in old.polygons):
        copied=obj.copy();copied.data=old.copy();copied.data.use_fake_user=False
        owned_objects.append(copied);owned_meshes.append(copied.data)
        material=obj.material_slots[material_index].material
        copied.data.materials.clear();copied.data.materials.append(material)
        for face in copied.data.polygons:face.material_index=0
        for name in list(copied.keys()):
            if name.startswith('SF6Source') or name=='SF6HybridDonorSlotV1':del copied[name]
        return copied
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
def automatic_collection(collection,source,source_hash,selected_objects=None,approved_corrective_losses=(),preserve_source_owners=(),plan_only=False):
    selected=None if selected_objects is None else set(selected_objects)
    parts=_parts_by_key(source)
    by_token={token(source_hash,p):p for p in parts.values()}
    candidates=[];dropped=[]
    arms=[o for o in collection.objects if o.type=='ARMATURE']
    _need(len(arms)==1,'Automatic hybrid needs one target armature on the collection root')
    arm=arms[0]
    for obj in meshes(collection,selected):
        _need(obj.mode=='OBJECT','Leave Edit Mode before exporting '+logical_name(obj))
        p=native_part(obj,source,source_hash)
        if p is not None and p['lod']!=0:continue
        rows=origins(obj.data)
        known_names=set(_part_shapes(source,p)) if p is not None else set()
        if rows is not None:
            for identity in {tuple(row) for row in rows}:
                proven=by_token.get(identity)
                if proven is not None:known_names.update(_part_shapes(source,proven))
        all_actual={k.name for k in list(obj.data.shape_keys.key_blocks)[1:]} if obj.data.shape_keys else set()
        extra_names=all_actual-known_names
        groups={};source_slots={} 
        for f in obj.data.polygons:
            origin=None
            if rows is not None:
                tokens={tuple(rows[v]) for v in f.vertices}
                if len(tokens)==1:origin=next(iter(tokens))
            # Legacy intact imported objects retain their existing checks.
            if rows is None and p is not None and len(obj.data.vertices)==p['count']:
                origin=token(source_hash,p)
            mat=f.material_index
            native=by_token.get(origin)
            if native is not None and _source_material_matches(obj,mat,source.materials[native['material']]):
                # Duplicate slots for the same source material are one section,
                # not duplicate claims that should lose their correctives.
                mat=source_slots.setdefault(origin,mat)
            groups.setdefault((origin,mat),[]).append(f.index)
        _need(groups,'Mixed mesh has no faces: '+logical_name(obj))
        for (origin,mat),face_ids in groups.items():
            native=by_token.get(origin)
            keys=obj.data.shape_keys
            expected=list(_part_shapes(source,native)) if native is not None else []
            actual={k.name for k in list(keys.key_blocks)[1:]} if keys else set()
            reason=None
            if native is not None and shape_range_overflow(obj,source,native,face_ids):
                reason='Corrective edit extends outside source-authored ranges';native=None
            if native is not None and non_trs_world(obj):
                reason='World shear requires original-scene Ordinary evaluation';native=None
            if extra_names and native is not None:
                reason='Additional shape keys have no verified source corrective identity';native=None
            if native is not None and not _source_material_matches(obj,mat,source.materials[native['material']]):
                # Source corrective identity currently includes its material.
                # Reclassifying a shaped section as ordinary would silently
                # discard those correctives. Refuse before allocating copies
                # or touching the output; unshaped parts can safely rebuild.
                reason='Material changed on source section';native=None
            if native is not None and expected and not keys:
                # Deleted keys are missing, not masked; say so in the review.
                reason='Missing compatible target shape keys';native=None
            if native is not None and expected and (not keys or not keys.use_relative or any(
                    keys.key_blocks[name].relative_key!=keys.key_blocks[0] or keys.key_blocks[name].vertex_group
                    for name in expected if name in actual)):
                reason='Non-Basis-relative or masked target keys';native=None
            if native is not None and not set(expected)<=actual:
                reason='Missing compatible target shape keys';native=None
            if native is not None:
                va=obj.data.attributes.get(sf6_source.VERTEX_ID)
                fa=obj.data.attributes.get(sf6_source.FACE_ID)
                used_rows=sorted({v for fi in face_ids for v in obj.data.polygons[fi].vertices})
                valid=(va is not None and fa is not None and va.domain=='POINT' and fa.domain=='FACE')
                if valid:
                    vids=[va.data[v].value for v in used_rows]
                    fids=[fa.data[f].value for f in face_ids]
                    repeated=(len([v for v in vids if v>=0])!=len({v for v in vids if v>=0}) or
                              len([f for f in fids if f>=0])!=len({f for f in fids if f>=0}))
                    valid=(len(vids)==len(set(vids)) and len(fids)==len(set(fids)) and
                           all(0<=v<native['count'] for v in vids) and all(0<=f<native['faces'] for f in fids))
                if not valid:
                    reason='Ambiguous or missing joined source row identities';native=None
            if native is None:
                # Every animation key on an ordinary section is lost as a live key.
                expected=sorted(set(expected)|actual)
                reason=reason or 'Foreign or unproven vertex origin'
                if expected:
                    require_corrective_loss(obj,source_hash,face_ids,str(reason),expected,
                                            approved_corrective_losses)
                validate_rig(obj,arm)
                reason=reason or ('Foreign or unproven vertex origin')
                dropped.append({'object':logical_name(obj),'material':_material(obj,mat),'reason':reason,'lost_corrective_names':list(expected),'face_ids':list(face_ids),'scope':'section','source_status':'verified','object_pointer':logical_pointer(obj)})
                expected=[]
            candidates.append(dict(obj=obj,native=native,faces=face_ids,material_index=mat,keys=expected,
                                   material=_material(obj,mat,native,source),reason=reason))
    # Duplicate source claims cannot share the binary corrective identity.
    # Never silently discard correctives from either the original or its copy.
    # Unshaped duplicates can safely become independent ordinary sections.
    claims={}
    for c in candidates:
        if c['native'] is not None:
            claims.setdefault(_part_key(source,c['native']),[]).append(c)
    duplicate_groups=[]
    for key,group in sorted(claims.items()):
        if len(group)<2:continue
        claimants=[c for c in group if owner_selected(preserve_source_owners,key,c['obj'])]
        _need(len(claimants)<=1,'PROTOTYPE_AMBIGUOUS_OWNER')
        identifier=source_key(key)
        explicit=isinstance(preserve_source_owners,dict) and identifier in preserve_source_owners
        if explicit and preserve_source_owners[identifier] is not None:
            _need(bool(claimants),'UNIFIED_INELIGIBLE_SOURCE_OWNER')
        duplicate_groups.append(dict(source_key=identifier,group=key[0],material=key[1],
            shape_names=list(_part_shapes(source,group[0]['native'])),
            selected_owner=logical_pointer(claimants[0]['obj']) if claimants else None,
            requires_choice=bool(group[0]['keys']) and not (explicit or claimants),
            claimants=[dict(object=logical_name(c['obj']),object_pointer=logical_pointer(c['obj']),
                eligible=True,material=c['material'],face_ids=list(c['faces']),
                shape_names=list(c['keys'])) for c in group]))
    review=CURRENT_REVIEW.get()
    if review is not None:
        for group in duplicate_groups:review['duplicate_groups'][group['source_key']]=group
        if review.get('preview') and any(g['requires_choice'] for g in duplicate_groups):
            raise DuplicateOwnerChoiceRequired(duplicate_groups)
    for key,group in claims.items():
        if len(group)<2:continue
        for c in group:
            if owner_selected(preserve_source_owners,key,c['obj']):continue
            expected=list(_part_shapes(source,c['native']))
            if expected:
                reason='Duplicate source claim; ordinary fallback loses its corrective links'
                require_corrective_loss(c['obj'],source_hash,c['faces'],reason,expected,
                    approved_corrective_losses,source_key=source_key(key))
            validate_rig(c['obj'],arm)
            c['native']=None;c['keys']=[];c['reason']='Source part split or duplicated; identity is ambiguous'
            dropped.append({'object':logical_name(c['obj']),'object_pointer':logical_pointer(c['obj']),
                'material':c['material'],'reason':c['reason'],'lost_corrective_names':expected,
                'face_ids':list(c['faces']),'scope':'section','source_status':'verified'})
    kept={_part_key(source,c['native']) for c in candidates if c['native'] is not None}
    # No retained source sections: use the separately validated atomic Ordinary path.
    used=set(kept)
    for c in candidates:
        if c['native'] is not None:c['key']=_part_key(source,c['native']);continue
        match=re.search(r'Group_(\d+)_',logical_name(c['obj']))
        preferred=int(match[1]) if match else 0
        choices=([preferred] if 0<=preferred<=255 else [])+list(range(256))
        group=next((g for g in choices if (g,c['material']) not in used),None)
        _need(group is not None,'No free group/material identity for added part '+logical_name(c['obj']))
        c['key']=(group,c['material']);used.add(c['key'])
    retained_sections=[dict(object=logical_name(c['obj']),object_pointer=logical_pointer(c['obj']),
        group=c['key'][0],material=c['key'][1],source_key=source_key(c['key']),
        face_ids=list(c['faces']),shape_names=list(c['keys']),source_status='verified',
        status='retained',scope='section') for c in candidates if c['native'] is not None]
    if plan_only:
        yield None,(),{'ordinary_only':not bool(kept),'ordinary_sections':dropped,
            'retained_sections':retained_sections,'duplicate_groups':duplicate_groups,
            'retained_source_parts':len(kept),'input_mesh_objects':len(meshes(collection,selected)),
            'output_sections':len(candidates),'new_materials':sorted({c['material'] for c in candidates}-set(source.materials))}
        return
    objects=[];meshes_owned=[];collections=[];mapped_selection=[]
    try:
        prep=bpy.data.collections.new('__SF6_AUTO_'+collection.name);collections.append(prep)
        for k in collection.keys():prep[k]=collection[k]
        prep['SF6AutomaticHybrid']=True
        bpy.context.scene.collection.children.link(prep)
        lod=bpy.data.collections.new('__SF6_AUTO_Main Mesh LOD0');collections.append(lod);prep.children.link(lod)
        if collection.get('LOD Distance') is not None:lod['LOD Distance']=collection['LOD Distance']
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
        report={'ordinary_only':not bool(kept),'ordinary_sections':dropped,
            'retained_sections':retained_sections,'duplicate_groups':duplicate_groups,'retained_source_parts':len(kept),
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
        view['SF6PlanCollectionIdentity']=str(collection.get('SF6PlanCollectionIdentity',(logical_name(collection),logical_pointer(collection))))
        bpy.context.scene.collection.children.link(view)
        for o in collection.all_objects:view.objects.link(o)
        yield view,anchor.name
    finally:bpy.data.collections.remove(view)
