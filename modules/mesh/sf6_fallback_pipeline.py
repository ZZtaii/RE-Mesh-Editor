"""Isolated fallback prototype. No UI text contract; not a production release."""
from .sf6_mode_snapshot import logical_name, logical_pointer
from contextlib import contextmanager,ExitStack
import os,tempfile,json
import bpy,numpy as np
from . import sf6_auto_hybrid as auto,sf6_source as sf
from .sf6_hybrid import _need,_part_key,_part_shapes
from .sf6_evaluated import evaluated_hybrid_collection

def validate_ordinary(data):
    mesh=sf.SourceMesh(data)
    _need(mesh.lod_count==1 and not mesh.blend_offset,'PROTOTYPE_INVALID_ORDINARY_LAYOUT')
    _need(bool(mesh.parts),'PROTOTYPE_EMPTY_ORDINARY_OUTPUT')
    for part in mesh.parts:
        pos=mesh.positions(part);faces=mesh.faces(part)
        _need(np.isfinite(pos).all() and len(pos)>0,'PROTOTYPE_INVALID_POSITION')
        _need(not faces.size or int(faces.max())<len(pos),'PROTOTYPE_INVALID_FACE_REFERENCE')
        _need(0<=part['material']<len(mesh.materials),'PROTOTYPE_INVALID_MATERIAL_REFERENCE')
        if mesh.skeleton_offset and 4 in mesh.elements:
            stride,offset=mesh.elements[4]
            _need(stride==16,'PROTOTYPE_UNSUPPORTED_SKIN_WEIGHT_LAYOUT')
            weights=np.frombuffer(data,np.uint8,part['count']*stride,offset+part['start']*stride).reshape(-1,stride)[:,8:]
            _need(np.all(weights.sum(axis=1)>0),'PROTOTYPE_ZERO_SKIN_WEIGHTS')
        for kind,(stride,offset) in mesh.elements.items():
            _need(stride>0 and offset>=mesh.vertex_offset and
                  offset+(part['start']+part['count'])*stride<=mesh.face_offset,
                  'PROTOTYPE_INVALID_VERTEX_STREAM')
    return mesh

def atomic_ordinary(path,collection,options,exporter,loss_report):
    directory=os.path.dirname(os.path.abspath(path));_need(os.path.isdir(directory),'PROTOTYPE_OUTPUT_DIRECTORY_MISSING')
    markers={k:bpy.context.scene.get(k) for k in ('REMeshLastExportedCollection','REMeshLastExportedMeshVersion')}
    success=False
    try:
        with tempfile.TemporaryDirectory(prefix='.sf6-ordinary-fallback-',dir=directory) as td:
            stage=os.path.join(td,'ordinary.mesh.230110883')
            ordinary=dict(options,sf6HybridPreserve=False,exportBlendShapes=False,exportAllLODs=False,_sf6SuppressExportNotifications=True)
            ordinary.pop('_sf6HybridReport',None)
            if loss_report.get('ordinary_evaluation_before_partition'):ordinary['_sf6EvaluatedSnapshot']=True
            try:
                result=exporter(stage,ordinary)
            finally:
                for key in ('_sf6OrdinaryErrors','_sf6OrdinaryWarnings','_sf6OrdinaryReport'):
                    if key in ordinary:options[key]=ordinary[key]
            _need(result,'PROTOTYPE_ORDINARY_EXPORT_FAILED')
            with open(stage,'rb') as f:data=f.read()
            mesh=validate_ordinary(data)
            with open(stage,'rb') as f:os.fsync(f.fileno())
            if options.get('_sf6PlanCheck'):options['_sf6PlanCheck']()
            os.replace(stage,path)
            options['_sf6HybridReport']={'parts':[{'group':p['group'],'material':mesh.materials[p['material']],
                'status':'ordinary_fallback','source_vertices':0,'rebuilt_vertices':p['count'],
                'source_triangles':0,'rebuilt_triangles':p['faces'],'shape_links':0} for p in mesh.parts],
                'mode':'ordinary_fallback','ordinary_only':True,
                'output_lods':1,'output_shape_links':0,'normal_table_rebuilt':False,
                'automatic_mixed_geometry':loss_report,'verified_output_parts':len(mesh.parts)}
            success=True
            return True
    finally:
        if not success:
            for key,value in markers.items():
                if value is None:
                    if key in bpy.context.scene:del bpy.context.scene[key]
                else:bpy.context.scene[key]=value

@contextmanager
def _normalized_collection(collection,source,source_hash,selected_objects=None,approved_corrective_losses=(),preserve_source_owners=()):
    with auto.automatic_collection(collection,source,source_hash,selected_objects,
            approved_corrective_losses,preserve_source_owners,plan_only=True) as (_,__,plan):
        raw_objects=auto.meshes(collection,None if selected_objects is None else set(selected_objects))
        raw_materials_safe=all({face.material_index for face in obj.data.polygons}=={0} for obj in raw_objects)
        if plan['ordinary_only'] and raw_materials_safe:
            plan['raw_ordinary_source']=True
            yield collection,selected_objects,plan
            return
    planned_ordinary_objects={section['object'] for section in plan['ordinary_sections']}
    selected=None if selected_objects is None else set(selected_objects)
    objects=auto.meshes(collection,selected);counts={}
    for obj in objects:
        p=auto.native_part(obj,source,source_hash)
        if p is not None and p['lod']==0:
            k=_part_key(source,p);counts[k]=counts.get(k,0)+1
    affected=[];losses=[]
    for obj in objects:
        p=auto.native_part(obj,source,source_hash)
        if p is not None and p['lod']!=0:continue
        active=any(m.type!='ARMATURE' and m.show_viewport for m in obj.modifiers)
        duplicate=(p is not None and counts[_part_key(source,p)]>1 and not auto.owner_selected(preserve_source_owners,_part_key(source,p),obj))
        # Valid joined sections can be split with provenance intact. Only bake
        # genuinely ordinary sections, or whole modifier-dependent joins.
        incompatible=(logical_name(obj) in planned_ordinary_objects or duplicate or
                      (active and auto.needs_automatic(collection,source,source_hash,{obj})))
        if not incompatible:continue
        # Evaluate this entire object before material partitioning. Its modifier
        # context cannot safely be separated into retained and demoted faces.
        expected=set(_part_shapes(source,p)) if p is not None else set()
        rows=auto.origins(obj.data)
        if rows is not None:
            tokens={tuple(row) for row in rows}
            for part in source.parts:
                if part['lod']==0 and auto.token(source_hash,part) in tokens:expected.update(_part_shapes(source,part))
        if obj.data.shape_keys:expected.update(k.name for k in list(obj.data.shape_keys.key_blocks)[1:])
        expected=sorted(expected);reason='Evaluate whole fallback object using Ordinary geometry before partitioning'
        if expected:
            auto.require_corrective_loss(obj,source_hash,range(len(obj.data.polygons)),reason,expected,
                                         approved_corrective_losses,scope='object')
        affected.append(obj);losses.append(dict(object=logical_name(obj),reason=reason,lost_corrective_names=expected,scope='object',object_pointer=logical_pointer(obj),source_status='verified',face_ids=list(range(len(obj.data.polygons))),materials=sorted({auto._material(obj,p.material_index) for p in obj.data.polygons})))
    if not affected:
        with auto.automatic_collection(collection,source,source_hash,selected_objects,approved_corrective_losses,preserve_source_owners) as result:yield result
        return
    owned_objects=[];owned_meshes=[];merged=None
    try:
        merged=bpy.data.collections.new('__SF6_ORDINARY_EVALUATED');bpy.context.scene.collection.children.link(merged)
        for key in collection.keys():merged[key]=collection[key]
        lods=sorted((c for c in collection.children if 'Main Mesh LOD' in c.name),key=lambda c:c.name)
        if lods:merged['LOD Distance']=lods[0].get('LOD Distance',0.167932)
        for obj in collection.all_objects:
            if obj.type=='ARMATURE' or obj.get('~TYPE'):merged.objects.link(obj)
        for obj in objects:
            p=auto.native_part(obj,source,source_hash)
            if obj not in affected and (p is None or p['lod']==0):merged.objects.link(obj)
        bpy.context.view_layer.update();depsgraph=bpy.context.evaluated_depsgraph_get()
        from .blender_re_mesh import triangulateMesh
        for obj in affected:
            mesh=bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph),preserve_all_data_layers=True,depsgraph=depsgraph)
            mesh.use_fake_user=False;owned_meshes.append(mesh)
            if any(len(face.vertices)!=3 for face in mesh.polygons):triangulateMesh(mesh)
            copy=obj.copy();owned_objects.append(copy);copy.data=mesh;copy.animation_data_clear()
            copy.parent=None;copy.constraints.clear();copy.matrix_world=obj.matrix_world.copy();copy.modifiers.clear()
            copy['SF6OrdinaryWorldMatrix']=[v for row in obj.matrix_world for v in row]
            for name in list(copy.keys()):
                if name.startswith('SF6Source'):del copy[name]
            for name in auto.ORIGIN:
                attr=mesh.attributes.get(name)
                if attr is not None:mesh.attributes.remove(attr)
            capture_ordinary_shading(mesh,obj,bool(collection.get('SF6SourceRotate',True)))
            copy[auto.AUTO]=True;copy['SF6OrdinaryEvaluatedSnapshot']=True;copy['SF6OrdinaryShadingPayload']=True
            merged.objects.link(copy)
        with auto.automatic_collection(merged,source,source_hash,None,approved_corrective_losses,preserve_source_owners) as (prepared,chosen,report):
            report['whole_object_modifier_losses']=losses
            report['ordinary_evaluation_before_partition']=True
            yield prepared,chosen,report
    finally:
        if merged is not None:bpy.data.collections.remove(merged)
        for obj in reversed(owned_objects):bpy.data.objects.remove(obj,do_unlink=True)
        for mesh in reversed(owned_meshes):
            if mesh.users==0:bpy.data.meshes.remove(mesh)

@contextmanager
def normalized_collection(collection,source,source_hash,selected_objects=None,approved_corrective_losses=(),preserve_source_owners=(),export_options=None):
    from .sf6_plan_fingerprint import fingerprint
    plan=fingerprint(collection,selected_objects,export_options)
    state=auto.CURRENT_EXPORT_PLAN.set(plan)
    if export_options is not None:
        export_options['_sf6PlanCheck']=lambda:_need(fingerprint(collection,selected_objects,export_options)==plan,'PROTOTYPE_EXPORT_PLAN_CHANGED')
    try:
        with _normalized_collection(collection,source,source_hash,selected_objects,approved_corrective_losses,preserve_source_owners) as result:
            result[2]['plan_fingerprint']=plan
            yield result
    finally:
        auto.CURRENT_EXPORT_PLAN.reset(state)
        if export_options is not None:export_options.pop('_sf6PlanCheck',None)

@contextmanager
def partition_ordinary_collection(collection,selected,rotate):
    """Evaluate whole objects once, then preserve every used material section."""
    import re
    from .blender_re_mesh import triangulateMesh
    objects=[];meshes=[];prepared=None
    try:
        prepared=bpy.data.collections.new('__SF6_GLOBAL_ORDINARY_PARTITION')
        bpy.context.scene.collection.children.link(prepared)
        for key in collection.keys():prepared[key]=collection[key]
        for obj in collection.all_objects:
            if obj.type=='ARMATURE' or obj.get('~TYPE'):prepared.objects.link(obj)
        bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get()
        for obj in auto.meshes(collection,None if selected is None else set(selected)):
            mesh=bpy.data.meshes.new_from_object(obj.evaluated_get(dg),preserve_all_data_layers=True,depsgraph=dg)
            meshes.append(mesh);mesh.use_fake_user=False
            if any(len(p.vertices)!=3 for p in mesh.polygons):triangulateMesh(mesh)
            snapshot=obj.copy();objects.append(snapshot);snapshot.data=mesh;snapshot.animation_data_clear()
            snapshot.parent=None;snapshot.constraints.clear();snapshot.matrix_world=obj.matrix_world.copy();snapshot.modifiers.clear()
            snapshot['SF6OrdinaryWorldMatrix']=[v for row in obj.matrix_world for v in row]
            capture_ordinary_shading(mesh,obj,rotate)
            snapshot['SF6OrdinaryShadingPayload']=True
            group=re.search(r'Group_(\d+)_',logical_name(obj));group=int(group.group(1)) if group else 0
            for index in sorted({p.material_index for p in mesh.polygons}):
                material=auto._material(snapshot,index)
                faces=[p.index for p in mesh.polygons if p.material_index==index]
                copy=auto._clone_section(snapshot,faces,index,[],objects,meshes)
                copy.name=f'Group_{group}_Sub_{index}__{material}'
                prepared.objects.link(copy)
        yield prepared
    finally:
        if prepared is not None:bpy.data.collections.remove(prepared)
        for obj in reversed(objects):bpy.data.objects.remove(obj,do_unlink=True)
        for mesh in reversed(meshes):
            if mesh.users==0:bpy.data.meshes.remove(mesh)

def unverified_source_ordinary(path,collection,options,exporter,reason,source_status="unverified_or_unavailable",source=None):
    """Use current Blender geometry, never unverified embedded source bytes."""
    from .sf6_plan_fingerprint import fingerprint
    selected=tuple(bpy.context.selected_objects) if options.get('selectedOnly') else None
    plan=fingerprint(collection,selected,options);state=auto.CURRENT_EXPORT_PLAN.set(plan)
    options['_sf6PlanCheck']=lambda:_need(fingerprint(collection,selected,options)==plan,'PROTOTYPE_EXPORT_PLAN_CHANGED')
    lods=sorted((child for child in collection.children if 'Main Mesh LOD' in child.name),key=lambda child:child.name)
    scope=lods[0] if lods else collection
    scoped_objects={obj for obj in scope.objects if obj.type=='MESH' and not obj.get('~TYPE') and (selected is None or obj in selected)}
    losses=[];ordinary=None
    try:
        for obj in auto.meshes(collection,scoped_objects):
            keys=obj.data.shape_keys;names=[k.name for k in list(keys.key_blocks)[1:]] if keys else []
            if source is not None:
                native=auto.native_part(obj,source,str(collection.get('SF6SourceSHA256','')))
                if native is not None:names=sorted(set(names)|set(_part_shapes(source,native)))
            if names or source is None:
                auto.require_corrective_loss(obj,str(collection.get('SF6SourceSHA256','unverified')),
                    range(len(obj.data.polygons)),reason,names,options.get('_sf6ApprovedCorrectiveLosses',()),
                    scope='output',source_status='verified' if source is not None else 'unverified',
                    source_reason=source_status,output_scope='selection' if selected is not None else 'collection')
            losses.append(dict(object=logical_name(obj),reason=reason,lost_corrective_names=names,source_correctives_unverified=source is None,scope='output',
                source_status='verified' if source is not None else 'unverified',source_reason=source_status,
                object_pointer=logical_pointer(obj),face_ids=list(range(len(obj.data.polygons))),
                materials=sorted({auto._material(obj,p.material_index) for p in obj.data.polygons})))
        report={'ordinary_only':True,'raw_ordinary_source':True,'source_status':source_status,
                'reason':reason,'ordinary_sections':losses,'plan_fingerprint':plan}
        ordinary=dict(options,useBlenderMaterialName=True)
        objects=auto.meshes(collection,scoped_objects)
        # Check evaluated materials too: modifiers can introduce additional slots.
        bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get()
        needs_partition=any({p.material_index for p in o.evaluated_get(dg).data.polygons}!={0} for o in objects)
        if needs_partition:
            report['ordinary_evaluation_before_partition']=True
            report['material_assignments_preserved']=True
            with partition_ordinary_collection(collection,scoped_objects,bool(options.get('rotate90',True))) as prepared:
                prepared['LOD Distance']=scope.get('LOD Distance',0.167932)
                ordinary.update(targetCollection=prepared.name,selectedOnly=False)
                result=atomic_ordinary(path,prepared,ordinary,exporter,report)
        else:
            result=atomic_ordinary(path,collection,ordinary,exporter,report)
        options['_sf6HybridReport']=ordinary['_sf6HybridReport']
        bpy.context.scene['REMeshLastExportedCollection']=collection.name
        return result
    finally:
        if ordinary is not None:
            for key in ('_sf6OrdinaryErrors','_sf6OrdinaryWarnings','_sf6OrdinaryReport'):
                if key in ordinary:options[key]=ordinary[key]
        auto.CURRENT_EXPORT_PLAN.reset(state)
        options.pop('_sf6PlanCheck',None)

def capture_ordinary_shading(mesh,obj,rotate=True):
    """Freeze Ordinary's pre-partition corner and legacy first-loop shading."""
    from math import floor
    from .blender_re_mesh import rotateNeg90Matrix
    world=mesh.copy();world.use_fake_user=False
    try:
        world.transform(rotateNeg90Matrix @ obj.matrix_world if rotate else obj.matrix_world)
        try:
            if world.uv_layers.active_index>=2:world.calc_tangents(uvmap=world.uv_layers[0].name)
            else:world.calc_tangents()
        except Exception:pass  # The Ordinary writer reports absent/invalid UVs.
        corners=np.zeros((len(world.loops),7),np.int32)
        points=np.zeros((len(world.vertices),7),np.int32);seen=set()
        for loop in world.loops:
            tangent=loop.tangent*1.001*127.0
            normal=np.array(tuple(loop.normal),np.float32).view(np.int32)
            row=np.concatenate([normal,np.array([int(floor(v))&255 for v in tangent]+[int(floor(loop.bitangent_sign*127.0))&255],np.int32)])
            corners[loop.index]=row
            if loop.vertex_index not in seen:points[loop.vertex_index]=row;seen.add(loop.vertex_index)
        for prefix,domain,values in [('p','POINT',points),('c','CORNER',corners)]:
            for column in range(7):
                name=f'_sf6_ordinary_{prefix}_{column}';existing=mesh.attributes.get(name)
                if existing is not None:mesh.attributes.remove(existing)
                attr=mesh.attributes.new(name,'INT',domain);attr.data.foreach_set('value',np.ascontiguousarray(values[:,column]))
    finally:bpy.data.meshes.remove(world)


def source_recalculation_issue(source):
    """Classify source-only table absence/layout errors, not arbitrary exceptions."""
    import struct
    from .sf6_hybrid import _normal_pointers,_padded_indices,HybridExportError
    header=struct.unpack_from('<Q',source.data,56)[0]
    if not header and not source.blend_offset:return None
    if not header:return 'missing_normal_recalculation_table'
    try:
        vp,fp=_normal_pointers(source)
        parts=[p for p in source.parts if p['lod']==0]
        if not parts:return 'missing_lod0_source_parts'
        first=parts[0]
        for part in parts:
            vbegin=vp+(part['start']-first['start'])*4
            fbegin=fp+(part['face_start']-first['face_start'])*4
            if vp<=0 or fp<=0 or vbegin<vp or fbegin<fp or vbegin+part['count']*4>len(source.data) or fbegin+_padded_indices(part)*4>len(source.data):
                return 'normal_recalculation_range_out_of_bounds'
    except (HybridExportError,struct.error,ValueError,IndexError,OverflowError):
        return 'invalid_normal_recalculation_table_structure'
    return None
