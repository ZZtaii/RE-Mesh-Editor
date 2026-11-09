"""Isolated unified dispatcher. Structured reports only; no approved UI wording."""
import base64,hashlib,json,os,tempfile,zlib,struct
import bpy,numpy as np
from . import sf6_source as sf
from .sf6_plan_fingerprint import fingerprint
from .sf6_hybrid import _need
from .sf6_mode_snapshot import logical_name, logical_pointer, needs_snapshot, export_snapshot, current_snapshot

BASELINE='SF6UnifiedImportStateV1'
WORKFLOW='unified_v1'
RIG_ROUNDOFF_TOLERANCE=4e-6

class UnifiedSettingsConflict(ValueError):
    def __init__(self,plan):
        self.plan=plan
        self.conflicts=plan['conflicts']
        super().__init__('UNIFIED_SETTINGS_CONFLICT')


def authoring_state(collection):
    return fingerprint(collection,None,{},stable_ids=True)

def rig_state(collection):
    arms=[o for o in collection.objects if o.type=='ARMATURE']
    return [{'signature':json.loads(json.dumps(sf._armature_signature(o))),
             'world':[float(v) for row in o.matrix_world for v in row]} for o in arms]

def equivalent_rest_rigs(left,right):
    try:
        if len(left)!=len(right):return False
        for a,b in zip(left,right):
            if not np.allclose(a['world'],b['world'],rtol=0,atol=RIG_ROUNDOFF_TOLERANCE):return False
            sa,sb=a['signature'],b['signature']
            if len(sa)!=len(sb):return False
            for ba,bb in zip(sa,sb):
                if ba[:2]!=bb[:2] or not np.allclose(ba[2],bb[2],rtol=0,atol=RIG_ROUNDOFF_TOLERANCE):return False
        return True
    except (KeyError,TypeError,ValueError):return False

def cached_rig_state(collection):
    def plain(value):
        if hasattr(value,'to_list'):return plain(value.to_list())
        if isinstance(value,(list,tuple)):return [plain(v) for v in value]
        if isinstance(value,(str,int,float,bool)) or value is None:return value
        return str(value)
    result=[]
    for obj in collection.objects:
        if obj.type!='ARMATURE':continue
        bones=[]
        for bone in obj.data.bones:
            values=[]
            for key in ('reMeshWorldMatrix','reMeshLocalMatrix','reMeshInverseMatrix','unhashedBoneName'):
                value=bone.get(key)
                value=plain(value)
                values.append(value)
            bones.append([bone.name,values])
        result.append(bones)
    return result

def capture_import_baseline(collection):
    collection[BASELINE]=json.dumps({'schema':1,'source_sha256':collection.get('SF6SourceSHA256'),
        'state':authoring_state(collection),'rig':rig_state(collection),'cached_rig':cached_rig_state(collection)},sort_keys=True)

def _source(collection):
    if collection is None:return None,'missing_collection'
    name=collection.get(sf.SOURCE,'')
    if not isinstance(name,str):return None,'source_unavailable'
    text=bpy.data.texts.get(name)
    if text is None:return None,'source_unavailable'
    try:
        data=zlib.decompress(base64.b64decode(text.as_string()))
        if hashlib.sha256(data).hexdigest()!=collection.get('SF6SourceSHA256'):return None,'source_hash_mismatch'
        return sf.SourceMesh(data),'verified'
    except (ValueError,KeyError,IndexError,OverflowError,zlib.error,struct.error):return None,'source_unreadable'

def validate_output(data,ordinary=False):
    mesh=sf.SourceMesh(data)
    _need(mesh.lod_count>0 and bool(mesh.parts),'UNIFIED_EMPTY_OUTPUT')
    if ordinary:_need(not mesh.blend_offset,'UNIFIED_UNEXPECTED_CORRECTIVES')
    for part in mesh.parts:
        pos=mesh.positions(part);faces=mesh.faces(part)
        _need(len(pos)>0 and np.isfinite(pos).all(),'UNIFIED_INVALID_POSITION')
        _need(not faces.size or int(faces.max())<len(pos),'UNIFIED_INVALID_FACE_REFERENCE')
        _need(0<=part['material']<len(mesh.materials),'UNIFIED_INVALID_MATERIAL_REFERENCE')
        for kind,(stride,offset) in mesh.elements.items():
            _need(stride>0 and offset>=mesh.vertex_offset and offset+(part['start']+part['count'])*stride<=mesh.face_offset,'UNIFIED_INVALID_VERTEX_STREAM')
        if mesh.skeleton_offset and 4 in mesh.elements:
            stride,offset=mesh.elements[4];_need(stride==16,'UNIFIED_UNSUPPORTED_SKIN_LAYOUT')
            weights=np.frombuffer(data,np.uint8,part['count']*stride,offset+part['start']*stride).reshape(-1,stride)[:,8:]
            _need(np.all(weights.sum(axis=1)>0),'UNIFIED_ZERO_SKIN_WEIGHTS')
    if mesh.skeleton_offset:
        sk=mesh.skeleton_offset
        _need(sk>=0 and sk+48<=len(data),'UNIFIED_INVALID_SKELETON_HEADER')
        count=struct.unpack_from('<I',data,sk)[0]
        _need(count>0,'UNIFIED_EMPTY_SKELETON')
        for offset in struct.unpack_from('<QQQ',data,sk+24):
            _need(offset>0 and offset+count*64<=len(data),'UNIFIED_INVALID_BONE_MATRIX_RANGE')
            _need(np.isfinite(np.frombuffer(data,'<f4',count*16,offset)).all(),'UNIFIED_INVALID_BONE_MATRIX')
    return mesh

def _source_identity_digest(collection):
    """Bind provenance and exclusion metadata omitted by authoring-state hashing."""
    collection_keys=(sf.SOURCE,'SF6SourceSHA256','SF6SourceRotate','SF6PreserveSource',
                     'SF6ImportedParts','SF6ArmatureSignature')
    object_keys=(sf.META,'SF6SourceSHA256','SF6OriginalWeights',
                 'SF6CollapsedDuplicateFaces','SF6HybridDonorSlotV1','~TYPE')
    value=[{key:collection.get(key) for key in collection_keys},
        [(logical_name(obj),logical_pointer(obj),{key:obj.get(key) for key in object_keys})
         for obj in sorted(collection.all_objects,key=lambda obj:logical_name(obj))]]
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def _exact_source_scope(collection,source):
    """Validate cheap strict-writer scope checks without matching display names.

    Unchanged import-origin rows prove the source metadata belongs to each
    actual object. Old snapshots without these rows keep the strict writer;
    they do not gain a new raw-copy permission or need a new snapshot schema.
    """
    from . import sf6_auto_hybrid as auto
    if not collection.get('SF6PreserveSource'):return False,'source_preservation_marker_missing'
    try:imported=json.loads(collection.get('SF6ImportedParts',''))
    except (ValueError,TypeError):return False,'imported_source_parts_unreadable'
    if not isinstance(imported,list) or not imported or any(p not in source.parts for p in imported):
        return False,'imported_source_parts_invalid'
    source_hash=str(collection.get('SF6SourceSHA256',''))
    actual=[];raw_eligible=True
    for obj in collection.all_objects:
        if obj.type!='MESH':continue
        if obj.get('~TYPE'):
            if sf.META in obj:return False,'source_geometry_marked_as_helper'
            continue
        part=auto.native_part(obj,source,source_hash)
        if part is None:return False,'source_object_identity_unverified'
        actual.append(part)
        rows=auto.origins(obj.data)
        if rows is None:
            raw_eligible=False
        elif not len(rows) or not np.all(rows==np.asarray(auto.token(source_hash,part),np.int32)):
            return False,'source_object_origin_mismatch'
    if len({p['start'] for p in actual})!=len(actual):return False,'duplicate_source_object_identity'
    encode=lambda p:json.dumps(p,sort_keys=True)
    if sorted(map(encode,actual))!=sorted(map(encode,imported)):
        return False,'imported_source_scope_changed'
    return raw_eligible,None


def _validate_plan_inputs(path,options):
    """Reject cheap invalid input before any editor synchronization."""
    _need(path.endswith('.mesh.230110883'),'UNIFIED_SF6_ONLY')
    collection=bpy.data.collections.get(options.get('targetCollection',''))
    _need(collection is not None,'UNIFIED_COLLECTION_MISSING')
    _need(options.get('sf6LodPolicy','retain_if_compatible') in ('retain_if_compatible','lod0'),'UNIFIED_INVALID_LOD_POLICY')
    preserve=options.get('exportBlendShapes',True)
    _need(isinstance(preserve,(bool,int)) and preserve in (0,1),'UNIFIED_INVALID_CORRECTIVE_POLICY')
    _need(options.get('sf6RigPolicy','auto') in ('auto','export_scene_rest'),'UNIFIED_INVALID_RIG_POLICY')
    return collection


def plan_export(path,options):
    collection=_validate_plan_inputs(path,options)
    if collection is not None and needs_snapshot(collection):
        with export_snapshot(collection) as (target, snapshot):
            return plan_export(path,dict(options,targetCollection=target.name))
    _need(path.endswith('.mesh.230110883'),'UNIFIED_SF6_ONLY')
    collection=bpy.data.collections.get(options.get('targetCollection',''))
    _need(collection is not None,'UNIFIED_COLLECTION_MISSING')
    bpy.context.view_layer.update()
    lod_policy=options.get('sf6LodPolicy','retain_if_compatible')
    _need(lod_policy in ('retain_if_compatible','lod0'),'UNIFIED_INVALID_LOD_POLICY')
    # An explicit historic preserve-off choice is never reinterpreted as preserve.
    preserve=options.get('exportBlendShapes',True)
    _need(isinstance(preserve,(bool,int)) and preserve in (0,1),'UNIFIED_INVALID_CORRECTIVE_POLICY')
    ordinary=not bool(preserve)
    source,status=_source(collection);reasons=[]
    identity_digest=_source_identity_digest(collection)
    raw_source_eligible,source_scope_reason=(_exact_source_scope(collection,source) if source is not None else (False,None))
    if source_scope_reason is not None:reasons.append('source_scope_unverified')
    try:baseline=json.loads(collection.get(BASELINE,''))
    except (ValueError,TypeError):baseline={}
    if not isinstance(baseline,dict):baseline={}
    state=authoring_state(collection)
    if status!='verified':reasons.append(status)
    if baseline.get('schema')!=1 or baseline.get('source_sha256')!=collection.get('SF6SourceSHA256'):reasons.append('no_verified_import_snapshot')
    elif baseline.get('state')!=state:reasons.append('authoring_changed')
    if options.get('selectedOnly'):reasons.append('selected_scope_requires_rebuild')
    if lod_policy=='lod0' and (source is None or source.lod_count!=1):reasons.append('requested_lod0')
    if bool(options.get('rotate90',True))!=bool(collection.get('SF6SourceRotate',True)):reasons.append('axis_conversion_changed')
    if any(options.get(k,False) for k in ('autoSolveRepeatedUVs','preserveSharpEdges','splitLoopVertices','exportBoundingBoxes','useBlenderMaterialName','limitTotal')):reasons.append('output_processing_requested')
    if not options.get('preserveBoneMatrices',True):reasons.append('bone_matrix_rebuild_requested')
    if source is not None:
        from .sf6_fallback_pipeline import source_recalculation_issue
        problem=source_recalculation_issue(source)
        if problem:reasons.append(problem)
        try:validate_output(source.data)
        except (ValueError,IndexError,OverflowError) as error:reasons.append('source_needs_geometry_validation')
    rig_policy=options.get('sf6RigPolicy','auto')
    _need(rig_policy in ('auto','export_scene_rest'),'UNIFIED_INVALID_RIG_POLICY')
    current_rig=rig_state(collection);original_rig=baseline.get('rig');rig_unverified=False
    if original_rig is None:
        # Legacy Preserve imports retain a rest-bone signature; source mode
        # historically requires identity armature world transforms.
        try:signature=json.loads(collection.get('SF6ArmatureSignature',''))
        except (ValueError,TypeError):signature=None
        identity=[1.0 if i%5==0 else 0.0 for i in range(16)]
        rig_unverified=signature is None and bool(current_rig) and any(b.get('reMeshWorldMatrix') is not None for o in collection.objects if o.type=='ARMATURE' for b in o.data.bones)
        rest_changed=rig_unverified or (signature is not None and not equivalent_rest_rigs(current_rig,[{'signature':signature,'world':identity}]))
    else:rest_changed=not equivalent_rest_rigs(current_rig,original_rig)
    if 'cached_rig' in baseline:
        cached_changed=baseline['cached_rig']!=cached_rig_state(collection)
        cached_evidence={'status':'changed' if cached_changed else 'unchanged','reason':'import_snapshot_comparison'}
    else:
        from .sf6_cached_rig import compare_cached_source_rig
        cached_evidence=compare_cached_source_rig(collection,source)
        cached_changed=bool(current_rig) and cached_evidence['status']!='unchanged'
    if cached_changed:reasons.append('cached_rig_metadata_changed')
    conflicts=[]
    if rest_changed and options.get('preserveBoneMatrices',True) and rig_policy=='auto':
        conflicts.append({'code':'edited_rest_rig_conflicts_with_preserved_bone_matrices',
                          'setting':'preserveBoneMatrices','resolution_option':'sf6RigPolicy',
                          'resolution_value':'export_scene_rest'})
    if rig_unverified:reasons.append('rest_rig_unverified')
    if rest_changed:reasons.append('rest_rig_changed_corrective_compatibility_unproven')
    route=('ordinary_rig' if rest_changed and not ordinary else
           'ordinary' if ordinary else 'ordinary_cached_rig' if cached_changed else 'ordinary_axis' if 'axis_conversion_changed' in reasons else 'exact_source' if not reasons else 'hybrid')
    known_lods=source.lod_count if source is not None else None
    output_lods=known_lods if route=='exact_source' else None if route=='ordinary' and lod_policy!='lod0' and options.get('exportAllLODs',True) else 1
    dropped=list(range(1,known_lods)) if known_lods and output_lods==1 else []
    report={'schema':1,'workflow':WORKFLOW,'route':route,'reasons':reasons,'lod_policy':lod_policy,
        'source_status':status,'source_identity_digest':identity_digest,
        'source_scope_reason':source_scope_reason,'exact_source_raw_eligible':raw_source_eligible,
        'source_lods':known_lods,'planned_output_lods':output_lods,
        'lower_lods_proven_compatible':route=='exact_source','dropped_source_lods':dropped,
        'lod_disposition_reason':'source_unchanged' if route=='exact_source' else 'compatibility_not_proven' if dropped else 'output_scope_or_unknown_source',
        'corrective_policy':'omit_explicitly' if ordinary else 'scoped_ordinary_loss' if route.startswith('ordinary_') else 'retain_where_compatible',
        'rig_cached_evidence':cached_evidence,'rig_evidence_unverified':rig_unverified,'rig_cached_metadata_changed':cached_changed,'rig_rest_changed':rest_changed,'rig_policy':rig_policy,'conflicts':conflicts,'rig_roundoff_tolerance':RIG_ROUNDOFF_TOLERANCE,
        'effective_preserve_bone_matrices':False if rest_changed and (rig_policy=='export_scene_rest' or not options.get('preserveBoneMatrices',True)) else bool(options.get('preserveBoneMatrices',True)),
        'source_sha256':collection.get('SF6SourceSHA256'),'state':state}
    selected=tuple(bpy.context.selected_objects) if options.get('selectedOnly') else None
    digest=fingerprint(collection,selected,dict(options,_sf6LogicalTargetPath=os.path.abspath(path)))
    runtime_state=[[(logical_pointer(obj),obj.active_shape_key_index,obj.show_only_shape_key)
                    for obj in sorted(collection.all_objects,key=logical_name) if obj.type=='MESH'],
                   bpy.context.scene.render.use_simplify,bpy.context.scene.render.simplify_subdivision]
    report['plan_token']=hashlib.sha256(json.dumps([os.path.abspath(path),digest,report,runtime_state],sort_keys=True).encode()).hexdigest()
    return report

def _ordinary_losses(collection,options,source):
    from . import sf6_auto_hybrid as auto
    selected=set(bpy.context.selected_objects) if options.get('selectedOnly') else None
    lods=sorted((c for c in collection.children if 'Main Mesh LOD' in c.name),key=lambda c:c.name) or [collection]
    if not options.get('exportAllLODs',True) or options.get('sf6LodPolicy')=='lod0':lods=lods[:1]
    exported={obj for lod in lods for obj in lod.objects if selected is None or obj in selected}
    source_hash=str(collection.get('SF6SourceSHA256',''))
    proven={auto.token(source_hash,p):p for p in source.parts} if source is not None else {}
    result=[]
    for obj in auto.meshes(collection,exported):
        keys=obj.data.shape_keys;names={k.name for k in list(keys.key_blocks)[1:]} if keys else set()
        part=auto.native_part(obj,source,source_hash) if source is not None else None
        if part is not None:names.update(n for n,_,_ in source.part_shapes(part))
        rows=auto.origins(obj.data)
        if rows is not None:
            for identity in {tuple(row) for row in rows}:
                part=proven.get(identity)
                if part is not None:names.update(n for n,_,_ in source.part_shapes(part))
        if names:result.append(dict(object=logical_name(obj),object_pointer=logical_pointer(obj),
            lost_corrective_names=sorted(names),shape_names=sorted(names),reason='explicit_ordinary_policy',
            scope='output',source_status='verified' if source is not None else 'unverified',
            materials=sorted({auto._material(obj,p.material_index) for p in obj.data.polygons}),
            face_ids=list(range(len(obj.data.polygons)))))
    return result

def _review_context(preview):
    return {'preview':preview,'losses':{},'duplicate_groups':{}}

def execute_unified(path,options,exporter,*,logical_path=None):
    """Publish only after current validation and explicit, exact loss consent."""
    from . import sf6_auto_hybrid as auto
    _need(auto.CURRENT_REVIEW.get() is None,'UNIFIED_PREVIEW_CONTEXT_CANNOT_PUBLISH')
    state=_review_context(False);handle=auto.CURRENT_REVIEW.set(state)
    try:return _execute_unified(path,options,exporter,state,logical_path=logical_path)
    finally:auto.CURRENT_REVIEW.reset(handle)

def _preview_unified(path,options,exporter,*,staging_directory=None):
    """Internal validated staging only. Never replaces the logical destination."""
    from . import sf6_auto_hybrid as auto
    _need(auto.CURRENT_REVIEW.get() is None,'UNIFIED_NESTED_PREVIEW')
    state=_review_context(True);handle=auto.CURRENT_REVIEW.set(state)
    try:
        _execute_unified(path,options,exporter,state,staging_directory=staging_directory)
        return options['_sf6UnifiedReport']
    except Exception as error:
        error.export_report=options.get('_sf6UnifiedReport',{})
        raise
    finally:auto.CURRENT_REVIEW.reset(handle)

def _execute_unified(path,options,exporter,review,*,logical_path=None,staging_directory=None):
    # An old successful report must not turn a new preflight/snapshot failure
    # into a false postpublication-success result when options are reused.
    options['_sf6UnifiedReport']={'schema':1,'workflow':WORKFLOW,'status':'not_committed','reason':'preflight_not_completed'}
    collection=_validate_plan_inputs(logical_path or path,options)
    directory=staging_directory if review['preview'] and staging_directory is not None else os.path.dirname(os.path.abspath(path))
    _need(os.path.isdir(directory),'UNIFIED_OUTPUT_DIRECTORY_MISSING')
    if collection is not None and needs_snapshot(collection):
        child=dict(options)
        try:
            with export_snapshot(collection) as (target, snapshot):
                child['targetCollection']=target.name
                result=_execute_unified_snapshot(path,child,exporter,review,
                    logical_path=logical_path,staging_directory=staging_directory)
            if result and not review['preview']:
                try:
                    bpy.context.scene['REMeshLastExportedCollection']=collection.name
                    bpy.context.scene['REMeshLastExportedMeshVersion']=230110883
                except Exception as error:
                    child['_sf6UnifiedReport'].setdefault('warnings',[]).append(
                        'The mesh was published, but last-export metadata could not be updated: '+str(error))
            return result
        except Exception as error:
            if child.get('_sf6UnifiedReport',{}).get('status')=='committed':
                child['_sf6UnifiedReport'].setdefault('warnings',[]).append(
                    'The mesh was published, but private snapshot cleanup failed; temporary export data may remain: '+str(error))
                return True
            raise
        finally:
            for key in tuple(options):
                if key.startswith('_sf6') and key not in child:options.pop(key,None)
            options.update({key:value for key,value in child.items() if key.startswith('_sf6')})
    return _execute_unified_snapshot(path,options,exporter,review,
        logical_path=logical_path,staging_directory=staging_directory)


def _current_source_plan(path,options):
    snapshot=current_snapshot()
    if snapshot is None:return plan_export(path,options)
    with snapshot.original_context():
        return plan_export(path,dict(options,targetCollection=snapshot.collection.name))


def _execute_unified_snapshot(path,options,exporter,review,*,logical_path=None,staging_directory=None):
    preview=review['preview']
    logical_path=os.path.abspath(logical_path or path)
    options['_sf6LogicalTargetPath']=logical_path
    options['_sf6UnifiedReport']={'schema':1,'workflow':WORKFLOW,'status':'not_committed','reason':'preflight_not_completed'}
    options.pop('_sf6HybridReport',None)
    options.pop('_sf6OrdinaryErrors',None)
    options.pop('_sf6OrdinaryWarnings',None)
    options.pop('_sf6OrdinaryReport',None)
    directory=staging_directory if preview and staging_directory is not None else os.path.dirname(os.path.abspath(path))
    _need(os.path.isdir(directory),'UNIFIED_OUTPUT_DIRECTORY_MISSING')
    plan=plan_export(logical_path,options);options['_sf6UnifiedReport']=dict(plan,status='planned')
    if options.get('_sf6UnifiedExpectedPlan') is not None and options['_sf6UnifiedExpectedPlan']!=plan['plan_token']:
        options['_sf6UnifiedReport']=dict(plan,status='not_committed',reason='stale_plan')
        _need(False,'UNIFIED_STALE_PLAN')
    if plan['conflicts']:
        options['_sf6UnifiedReport']=dict(plan,status='needs_policy_choice')
        raise UnifiedSettingsConflict(plan)
    collection=bpy.data.collections[options['targetCollection']]
    markers={k:bpy.context.scene.get(k) for k in ('REMeshLastExportedCollection','REMeshLastExportedMeshVersion')}
    success=False;preview_complete=False;child=None
    selected_before=tuple(bpy.context.selected_objects)
    active_before=bpy.context.view_layer.objects.active
    try:
        with tempfile.TemporaryDirectory(prefix='.sf6-unified-',dir=directory) as td:
            staged=os.path.join(td,'output.mesh.230110883')
            child=dict(options,_sf6UnifiedDispatch=True,_sf6SuppressExportNotifications=True)
            child.pop('_sf6UnifiedReport',None)
            if plan['rig_rest_changed']:child['preserveBoneMatrices']=plan['effective_preserve_bone_matrices']
            if plan['route']=='exact_source':
                child.update(exportBlendShapes=True,sf6HybridPreserve=False,selectedOnly=False)
            elif plan['route']=='ordinary':
                child.update(exportBlendShapes=False,sf6HybridPreserve=False)
                if plan['lod_policy']=='lod0':child['exportAllLODs']=False
            else:
                child.update(exportBlendShapes=True,sf6HybridPreserve=True,exportAllLODs=False)
            if plan['route']=='exact_source' and plan['exact_source_raw_eligible']:
                # plan_export has proved the imported authoring state, source
                # digest, full scope, rig and settings unchanged. Blender can
                # truncate long display names even on a pristine import; the
                # original binary already contains their full identities.
                exact_source, exact_status = _source(collection)
                _need(exact_status=='verified' and exact_source is not None and
                      hashlib.sha256(exact_source.data).hexdigest()==plan['source_sha256'],
                      'UNIFIED_EXACT_SOURCE_MISMATCH')
                _write_exact_source(staged, exact_source.data)
            elif plan['route'] in ('ordinary_rig','ordinary_axis','ordinary_cached_rig'):
                from .sf6_fallback_pipeline import unverified_source_ordinary
                source,_=_source(collection)
                _need(unverified_source_ordinary(staged,collection,child,exporter,
                      ('Changed rest rig; source corrective compatibility is unproven' if plan['route']=='ordinary_rig' else 'Changed cached rig metadata; source corrective compatibility is unproven' if plan['route']=='ordinary_cached_rig' else 'Changed axis conversion; source corrective compatibility is unproven'),
                      source_status=('verified_source_'+plan['route']) if source is not None else ('unverified_'+plan['route']),source=source),'UNIFIED_BACKEND_FAILED')
            else:_need(exporter(staged,child),'UNIFIED_BACKEND_FAILED')
            with open(staged,'rb') as f:data=f.read()
            output=validate_output(data,ordinary=plan['route'] in ('ordinary','ordinary_rig','ordinary_axis','ordinary_cached_rig'))
            source,_=_source(collection)
            if plan['route']=='exact_source':
                _need(source is not None and data==source.data,'UNIFIED_EXACT_SOURCE_MISMATCH')
            # Current scene/options/source must still describe the reviewed output.
            _need(_current_source_plan(logical_path,options)['plan_token']==plan['plan_token'],'UNIFIED_PLAN_CHANGED')
            report=dict(plan,status='validated',output_lods=output.lod_count,
                dropped_source_lods=list(range(output.lod_count,plan['source_lods'])) if plan['source_lods'] else [],
                output_shape_links=sum(len(level) for level in output.shapes),
                source_byte_identical=source is not None and data==source.data,
                backend_report=child.get('_sf6HybridReport'),
                effective_route=('ordinary_fallback' if child.get('_sf6HybridReport',{}).get('ordinary_only') else plan['route']))
            if plan['route']=='ordinary':report['ordinary_sections']=_ordinary_losses(collection,options,source)
            report['ordinary_errors']=child.get('_sf6OrdinaryErrors',[])
            report['warnings']=list(child.get('_sf6OrdinaryWarnings',[]))
            report['ordinary_report']=dict(child.get('_sf6OrdinaryReport',{}))
            report['loss_events']=list(review['losses'].values())
            report['duplicate_groups']=list(review['duplicate_groups'].values())
            report['output_sha256']=hashlib.sha256(data).hexdigest()
            report['output_sections']=_output_sections(output,collection,child.get('_sf6HybridReport'),exact_source=plan['route']=='exact_source')
            # The reviewed checklist is exact, not a generic permission to drop keys.
            if not preview and options.get('_sf6UnifiedExpectedPlan') is not None:
                _need(set(options.get('_sf6ApprovedCorrectiveLosses',()))==set(review['losses']),
                      'UNIFIED_LOSS_APPROVAL_CHANGED')
            expected_output=options.get('_sf6UnifiedExpectedOutputSHA256')
            if not preview and expected_output is not None:
                _need(expected_output==report['output_sha256'],'UNIFIED_REVIEWED_OUTPUT_CHANGED')
            options['_sf6UnifiedReport']=report
            if preview:
                report['status']='preview_validated'
                report['published']=False
                preview_complete=True
                return True
            with open(staged,'rb') as f:os.fsync(f.fileno())
            os.replace(staged,path)
            options['_sf6UnifiedReport']['status']='committed'
            options['_sf6UnifiedReport']['published']=True
            if '_sf6HybridReport' in child:options['_sf6HybridReport']=child['_sf6HybridReport']
            success=True
            if plan['route']=='exact_source':
                try:
                    bpy.context.scene['REMeshLastExportedCollection']=collection.name
                    bpy.context.scene['REMeshLastExportedMeshVersion']=230110883
                except Exception as error:
                    report['warnings'].append('The mesh was published, but last-export metadata could not be updated: '+str(error))
            return True
    finally:
        if not success:
            if not preview_complete:
                options['_sf6UnifiedReport']=dict(plan,status='not_committed',
                    ordinary_errors=child.get('_sf6OrdinaryErrors',[]) if child is not None else [])
            for key,value in markers.items():
                if value is None:
                    if key in bpy.context.scene:del bpy.context.scene[key]
                else:bpy.context.scene[key]=value

        if preview:
            # Exporters already preserve editable geometry. Restore UI selection
            # and markers even if a nested ordinary writer changed them.
            for obj in tuple(bpy.context.selected_objects):
                if obj not in selected_before:obj.select_set(False)
            for obj in selected_before:obj.select_set(True)
            bpy.context.view_layer.objects.active=active_before

def _write_exact_source(path, data):
    """Stage verified unchanged bytes, never re-encode Blender display names."""
    with open(path, 'wb') as stream:
        stream.write(data)


def _output_sections(output,collection,backend_report,*,exact_source=False):
    """Report actual staged sections, joined back to verified input identities."""
    from . import sf6_auto_hybrid as auto
    backend=backend_report or {}
    automatic=backend.get('automatic_mixed_geometry',{})
    identities={(0,p['group'],p['material']):p for p in automatic.get('retained_sections',[])}
    if not identities:
        source,status=_source(collection)
        if source is not None:
            for obj in auto.meshes(collection):
                p=auto.native_part(obj,source,str(collection.get('SF6SourceSHA256','')))
                if p is not None:
                    identities.setdefault((p['lod'],p['group'],source.materials[p['material']]),
                        dict(object=logical_name(obj),object_pointer=logical_pointer(obj),
                             face_ids=list(range(len(obj.data.polygons)))))
    backend_parts={(p['group'],p['material']):p for p in backend.get('parts',[])}
    result=[]
    for part in output.parts:
        material=output.materials[part['material']]
        names=sorted({name for name,_,_ in output.part_shapes(part)})
        backend_part=backend_parts.get((part['group'],material),{})
        compatible=(exact_source or backend_part.get('status') in ('source_mapped','rebuilt','mixed'))
        identity=identities.get((part['lod'],part['group'],material),{}) if names or compatible else {}
        result.append(dict(identity,group=part['group'],material=material,lod=part['lod'],
            source_key=auto.source_key((part['group'],material)),shape_names=names,
            shape_links=len(names),vertices=part['count'],triangles=part['faces'],scope='section',
            status='retained' if names else 'compatible_geometry' if compatible else 'ordinary_geometry',
            source_status='verified' if names or compatible else 'not_applicable'))
    return result
