#Author: NSA Cloud
import bpy
import os
import json
import hashlib
from copy import deepcopy
from types import SimpleNamespace
import textwrap
from bpy.types import Operator

from bpy.props import (StringProperty,
                       BoolProperty,
                       IntProperty,
                       FloatProperty,
                       FloatVectorProperty,
                       EnumProperty,
                       )
from .blender_re_mesh import solveRepeatedUVs
from .re_mesh_propertyGroups import (ExporterNodePropertyGroup, MESH_UL_REExporterList,
	getBatchMeshWeightLimitDefault, getBatchMeshWeightLimit, setBatchMeshWeightLimit)
from .sf6_export_settings import (BATCH_PRESERVE_SOURCE, BATCH_LOD_POLICY, BATCH_PROCESSING_FIELDS, batch_preserve_source,
	load_batch_workflow, batch_uses_unified_view, apply_batch_new_profile_defaults)
from ..gen_functions import splitNativesPath
from ..blender_utils import showErrorMessageBox


_BATCH_MESH_FIX_DEFAULTS = {
	"autoSolveRepeatedUVs": True,
	"splitLoopVertices": True,
	"normalizeWeights": True,
	"limitTotal": False,
	"limitTotalCount": 8,
	"shapeKeyExportMode": "MODE0",
}


_BATCH_OPTION_DEFAULTS = {
    'selectedOnly': False, 'exportAllLODs': True, 'exportBlendShapes': False,
    'autoSolveRepeatedUVs': True, 'preserveSharpEdges': True,
    'splitLoopVertices': True, 'normalizeWeights': True, 'limitTotal': False,
    'limitTotalCount': 8, 'shapeKeyExportMode': 'MODE0', 'rotate90': True,
    'useBlenderMaterialName': False, 'preserveBoneMatrices': False,
    'exportBoundingBoxes': False,
}
_LAST_BATCH_REVIEW = None
_LAST_BATCH_RESULT = None


def _batch_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf8')).hexdigest()


def _batch_profile(collection):
    if collection is None:
        return None
    keys = ((BATCH_PRESERVE_SOURCE, 'SF6BatchExportWorkflowV1', 'SF6BatchExportLodPolicyV1',
             'BatchExport_path', 'BatchExport_enabled') +
            tuple('BatchExport_' + field for field in BATCH_PROCESSING_FIELDS))
    return {key: collection[key] for key in keys if key in collection}


def _batch_payload(operator):
    entries = []
    for index, item in enumerate(operator.itemList_items):
        if item.hasChild or not item.enabled or not item.exportType:
            continue
        owner = (bpy.data.objects.get(item.name) if item.exportType == 'FBXSKEL'
                 else bpy.data.collections.get(item.name))
        row = dict(entry_id=str(index), name=item.name, path=item.path,
                   export_type=item.exportType, invalid=bool(item.invalid), enabled=True,
                   owner_pointer=owner.as_pointer() if owner is not None else None,
                   saved_profile=_batch_profile(owner))
        row['options'] = {key: getattr(item, key, default)
                          for key, default in _BATCH_OPTION_DEFAULTS.items()}
        row['options']['limitTotalCount'] = getBatchMeshWeightLimit(item)
        row['options']['targetCollection'] = item.name
        row['options']['selectedOnly'] = False
        row['options']['sf6RigPolicy'] = getattr(item, 'sf6RigPolicy', 'auto')
        for key in ('sf6ExportWorkflow', 'sf6LodPolicy'):
            if getattr(item, key, ''):
                row['options'][key] = getattr(item, key)
        row['preserve_review_required'] = bool(getattr(item, 'sf6BatchPreserveReviewRequired', False))
        entries.append(row)
    return dict(schema=1, scope='batch', entries=entries)


def _batch_failure(error=None, *, reason=None, publication_started=False):
    report = deepcopy(getattr(error, 'report', None) or getattr(error, 'plan', None) or {})
    if not isinstance(report, dict):
        report = {}
    report.update(status='not_committed', publication_started=publication_started)
    if reason:
        report['reason'] = reason
    if error is not None:
        report.update(error_code=type(error).__name__, error_detail=str(error))
        if getattr(error, 'conflicts', None):
            report['conflicts'] = deepcopy(error.conflicts)
    return report


def _batch_file(row, report):
    return dict(entry_id=row['entry_id'], file=row['path'], path=row['path'], target_collection=row['name'],
                export_type=row['export_type'], result=deepcopy(report),
                status=report.get('status', 'unverified'))


def _batch_summary(files):
    total = len(files)
    succeeded = sum(row['status'] == 'committed' for row in files)
    pending = sum(row['status'] in ('pending', 'review_pending', 'running') for row in files)
    failed = total - succeeded - pending
    status = ('committed' if succeeded == total and total else
              'partially_committed' if succeeded else 'pending' if pending else 'not_committed')
    return dict(schema=1, scope='batch', status=status, total_count=total,
                succeeded_count=succeeded, completed_count=succeeded, failed_count=failed,
                pending_count=pending, files=files, items=files,
                publication_started=any(row['result'].get('publication_started',
                                                        row['status'] == 'committed') for row in files))


def _batch_commit(row, options):
    from .sf6_unified_ui import commit_batch_settings
    owner = (bpy.data.objects.get(row['name']) if row['export_type'] == 'FBXSKEL'
             else bpy.data.collections.get(row['name']))
    if owner is None or owner.as_pointer() != row['owner_pointer']:
        raise ValueError('BATCH_OWNER_CHANGED_AFTER_EXPORT')
    owner['BatchExport_enabled'] = True
    owner['BatchExport_path'] = row['path']
    if row['export_type'] != 'MESH':
        return
    for key in _BATCH_OPTION_DEFAULTS:
        if key not in ('selectedOnly', 'exportBlendShapes'):
            owner['BatchExport_' + key] = options.get(key, _BATCH_OPTION_DEFAULTS[key])
    if row['path'].endswith('.mesh.230110883'):
        owner[BATCH_PRESERVE_SOURCE] = bool(options['exportBlendShapes'])
        # Ordinary export deliberately keeps no unified workflow marker. Save
        # its visible LOD choice through the legacy field used on the next load.
        if not options['exportBlendShapes'] and options.get('sf6LodPolicy') in ('lod0', 'retain_if_compatible'):
            owner['BatchExport_exportAllLODs'] = options['sf6LodPolicy'] != 'lod0'
        commit_batch_settings(owner, options)


class BatchExportReview:
    """One immutable batch review; publication remains atomic per SF6 file."""
    def __init__(self):
        self.prepared = {}
        self.review = None
        self.payload_digest = None
        self.rig_files = set()

    def _options(self, row, overrides):
        from . import sf6_unified_ui as ui
        options = deepcopy(row['options'])
        collection = bpy.data.collections.get(row['name'])
        options = ui.resolve_batch_options(collection, options, mesh_version=230110883,
                                           enable_unified=True)
        operator = SimpleNamespace(sf6RigPolicy=row['options'].get('sf6RigPolicy', 'auto'),
            sf6ApprovedCorrectiveLosses='[]', sf6ExpectedPlan='', sf6SettingsScope='batch')
        options = ui.apply_review_inputs(operator, options)
        prefix = row['entry_id'] + ':'
        owners = {key[len(prefix):]: value for key, value in
                  overrides.get('_sf6PreserveSourceOwners', {}).items() if key.startswith(prefix)}
        if owners:
            options['_sf6PreserveSourceOwners'] = owners
        if row['entry_id'] in self.rig_files and 'preserveBoneMatrices' in overrides:
            options['preserveBoneMatrices'] = overrides['preserveBoneMatrices']
        return options

    def prepare(self, payload, overrides):
        from .sf6_review_plan import prepare_review
        from .blender_re_mesh import exportREMeshFile
        files, losses, tokens, owners, retained, conflicts = [], [], [], [], [], []
        prepared = {}
        destinations = {}
        for row in payload['entries']:
            key = os.path.normcase(os.path.abspath(row['path']))
            destinations.setdefault(key, []).append(row['entry_id'])
        for row in payload['entries']:
            entry_id = row['entry_id']
            owner = (bpy.data.objects.get(row['name']) if row['export_type'] == 'FBXSKEL'
                     else bpy.data.collections.get(row['name']))
            try:
                if owner is None or owner.as_pointer() != row['owner_pointer']:
                    raise ValueError('BATCH_EXPORT_TARGET_CHANGED')
                if row['invalid'] or not row['path']:
                    raise ValueError('BATCH_INVALID_EXPORT_PATH')
                if len(destinations[os.path.normcase(os.path.abspath(row['path']))]) > 1:
                    raise ValueError('BATCH_DUPLICATE_DESTINATION')
                sf6 = row['export_type'] == 'MESH' and row['path'].endswith('.mesh.230110883')
                if sf6:
                    if row['preserve_review_required']:
                        from .sf6_unified_ui import SettingsReviewRequired, review_report
                        raise SettingsReviewRequired(review_report(['invalid_preservation_value'], scope='batch'))
                    options = self._options(row, overrides)
                    ancestor = os.path.dirname(os.path.abspath(row['path']))
                    while not os.path.isdir(ancestor):
                        parent = os.path.dirname(ancestor)
                        if parent == ancestor:
                            raise ValueError('BATCH_OUTPUT_DIRECTORY_UNAVAILABLE')
                        ancestor = parent
                    review = prepare_review(row['path'], options, exportREMeshFile, staging_directory=ancestor)
                else:
                    if row['export_type'] not in ('MESH', 'MDF', 'FBXSKEL', 'CHAIN', 'CHAIN2', 'CLSP', 'SFUR'):
                        raise ValueError('BATCH_UNSUPPORTED_FORMAT')
                    if row['export_type'] in ('CHAIN', 'CHAIN2', 'CLSP') and not hasattr(bpy.types, 'OBJECT_PT_chain_object_mode_panel'):
                        raise ValueError('BATCH_CHAIN_EDITOR_UNAVAILABLE')
                    options = deepcopy(row['options'])
                    review = dict(status='ready', complete=True,
                        plan_token=_batch_digest(row), output_sha256=None, losses=[],
                        approved_loss_tokens=[], duplicate_groups=[], retained_sections=[],
                        report=dict(status='validation_deferred', reason='format_validated_during_export'))
                prepared[entry_id] = dict(row=deepcopy(row), options=options, review=review, sf6=sf6)
                report = dict(review.get('report', {}), status=review['status'],
                              complete=review.get('complete', False))
                files.append(_batch_file(row, report))
                prefix = entry_id + ':'
                for event in review.get('losses', []):
                    event = deepcopy(event)
                    event.update(file=row['path'], destination=row['path'], entry_id=entry_id, selected_only=False)
                    event['loss_token'] = prefix + event['loss_token']
                    if 'covered_loss_tokens' in event:
                        event['covered_loss_tokens'] = [prefix + token for token in event['covered_loss_tokens']]
                    losses.append(event)
                tokens.extend(prefix + token for token in review.get('approved_loss_tokens', []))
                for group in review.get('duplicate_groups', []):
                    group = deepcopy(group)
                    group.update(file=row['path'], entry_id=entry_id)
                    group['source_key'] = prefix + group['source_key']
                    owners.append(group)
                retained.extend(dict(section, file=row['path'], entry_id=entry_id)
                                for section in review.get('retained_sections', []))
            except Exception as error:
                report = _batch_failure(error)
                for conflict in report.get('conflicts', []):
                    conflicts.append(dict(conflict, file=row['path'], entry_id=entry_id))
                    self.rig_files.add(entry_id)
                prepared[entry_id] = dict(row=deepcopy(row), error=report)
                files.append(_batch_file(row, report))
        ready = sum(item.get('review', {}).get('status') == 'ready' for item in prepared.values())
        needs_owner = any(item.get('review', {}).get('status') == 'needs_owner_choice' for item in prepared.values())
        status = 'needs_policy_choice' if conflicts else 'needs_owner_choice' if needs_owner else 'ready' if ready else 'blocked'
        identity = [dict(entry_id=k, plan=v.get('review', {}).get('plan_token'),
                         output=v.get('review', {}).get('output_sha256'), error=v.get('error'))
                    for k, v in prepared.items()]
        review = dict(schema=1, status=status, complete=status == 'ready', selected_only=False,
                      plan_token=_batch_digest([payload, identity, overrides]),
                      output_sha256=_batch_digest(identity), losses=losses,
                      approved_loss_tokens=sorted(tokens), requires_loss_approval=bool(tokens),
                      duplicate_groups=owners, retained_sections=retained, conflicts=conflicts,
                      files=files, report=dict(scope='batch', files=files,
                          total_count=len(files), ready_count=ready,
                          failed_count=sum('error' in v for v in prepared.values())))
        self.prepared, self.review = prepared, deepcopy(review)
        self.payload_digest = _batch_digest(payload)
        return review

    def publish(self, payload, overrides):
        from .blender_re_mesh import exportREMeshFile
        if self.review is None or self.review['status'] != 'ready' or _batch_digest(payload) != self.payload_digest:
            raise ValueError('BATCH_REVIEW_REQUIRED')
        if (overrides.get('_sf6UnifiedExpectedPlan') != self.review['plan_token'] or
                overrides.get('_sf6UnifiedExpectedOutputSHA256') != self.review['output_sha256'] or
                set(overrides.get('_sf6ApprovedCorrectiveLosses', [])) != set(self.review['approved_loss_tokens'])):
            raise ValueError('UNIFIED_STALE_PLAN')
        files = []
        for row in payload['entries']:
            prepared = self.prepared[row['entry_id']]
            if 'error' in prepared:
                files.append(_batch_file(row, prepared['error']))
                continue
            options = deepcopy(prepared['options'])
            started = False
            try:
                os.makedirs(os.path.dirname(os.path.abspath(row['path'])), exist_ok=True)
                started = True
                if prepared['sf6']:
                    review = prepared['review']
                    options['_sf6UnifiedExpectedPlan'] = review['plan_token']
                    options['_sf6UnifiedExpectedOutputSHA256'] = review['output_sha256']
                    prefix = row['entry_id'] + ':'
                    options['_sf6ApprovedCorrectiveLosses'] = [token[len(prefix):] for token in
                        overrides['_sf6ApprovedCorrectiveLosses'] if token.startswith(prefix)]
                    if not exportREMeshFile(row['path'], options):
                        raise ValueError('SF6_BACKEND_FAILED')
                    report = deepcopy(options.get('_sf6UnifiedReport', {}))
                    if report.get('status') != 'committed':
                        raise ValueError('SF6_PUBLICATION_UNCONFIRMED')
                else:
                    names = dict(MESH='re_mesh', MDF='re_mdf', FBXSKEL='re_fbxskel',
                                 CHAIN='re_chain', CHAIN2='re_chain2', CLSP='re_clsp', SFUR='re_sfur')
                    arguments = dict(filepath=row['path'])
                    arguments['targetArmature' if row['export_type'] == 'FBXSKEL' else 'targetCollection'] = row['name']
                    if row['export_type'] == 'MESH':
                        arguments.update({key: options[key] for key in _BATCH_OPTION_DEFAULTS if key != 'selectedOnly'})
                        arguments['sf6SettingsScope'] = 'batch'
                    result = getattr(bpy.ops, names[row['export_type']]).exportfile(**arguments)
                    if 'FINISHED' in result:
                        report = dict(status='committed', publication_confirmation='operator_finished')
                    elif result & {'INTERFACE', 'RUNNING_MODAL'}:
                        report = dict(status='pending', reason='operator_not_completed', operator_result=sorted(result))
                    else:
                        report = dict(status='not_committed', reason='operator_cancelled', operator_result=sorted(result))
                report.update(publication_started=started, destination=row['path'])
                if report.get('dropped_source_lods'):
                    report.setdefault('warnings', []).append('Lower source LODs were not exported.')
                backend = report.get('backend_report') or {}
                added = backend.get('added_materials', []) if isinstance(backend, dict) else []
                if added:
                    report.setdefault('warnings', []).append('New materials need matching MDF entries: ' + ', '.join(map(str, added)) + '.')
                if report['status'] == 'committed':
                    try:
                        _batch_commit(row, options)
                    except Exception as error:
                        report.setdefault('warnings', []).append('File was exported, but its batch settings could not be saved: ' + str(error))
                files.append(_batch_file(row, report))
            except Exception as error:
                report = deepcopy(options.get('_sf6UnifiedReport', {})) if prepared['sf6'] else {}
                report.update(_batch_failure(error, publication_started=started))
                report['destination'] = row['path']
                files.append(_batch_file(row, report))
        return _batch_summary(files)


def _batch_result_text(report):
    from .sf6_unified_messages import format_batch
    return ' '.join(format_batch(report)['lines'])


def begin_batch_review(operator, context):
    global _LAST_BATCH_REVIEW, _LAST_BATCH_RESULT
    from .sf6_unified_dialogs import begin_review, cancel_active_review, notify
    payload = _batch_payload(operator)
    if not payload['entries']:
        cancel_active_review(context)
        report = dict(schema=1, scope='batch', status='not_committed', total_count=0,
                      succeeded_count=0, failed_count=0, pending_count=0, files=[], reason='no_files_enabled')
        operator.sf6UnifiedResult = json.dumps(report, sort_keys=True)
        _LAST_BATCH_RESULT = report
        operator.report({'ERROR'}, 'No files are enabled. Enable at least one batch entry.')
        return {'CANCELLED'}
    engine = BatchExportReview()
    if (getattr(operator, 'sf6ReviewOnly', False) or
            any(getattr(item, 'sf6ReviewOnly', False) for item in operator.itemList_items if item.enabled)):
        cancel_active_review(context)
        review = engine.prepare(payload, {})
        report = dict(review, status='planned' if review['status'] == 'ready' else review['status'],
                      scope='batch', publication_started=False, completed_count=0,
                      succeeded_count=0, failed_count=review['report']['failed_count'],
                      pending_count=review['report']['ready_count'], total_count=len(payload['entries']))
        operator.sf6UnifiedResult = json.dumps(report, sort_keys=True)
        _LAST_BATCH_RESULT = deepcopy(report)
        _LAST_BATCH_REVIEW = None
        level = 'INFO' if report['status'] == 'planned' and not report['failed_count'] else 'WARNING' if report['status'] == 'planned' else 'ERROR'
        message = 'Export plan prepared. No file was written or replaced.' if report['status'] == 'planned' else 'Export stopped. Review the specific errors in Details before retrying.'
        operator.report({level}, message)
        return {'FINISHED'} if report['status'] == 'planned' else {'CANCELLED'}
    def current_payload():
        try:
            return _batch_payload(operator)
        except ReferenceError:
            # The settings operator has closed. Its detached choices remain
            # fixed; scene/source changes are still checked by every backend.
            value = deepcopy(payload)
            for row in value['entries']:
                owner = bpy.data.objects.get(row['name']) if row['export_type'] == 'FBXSKEL' else bpy.data.collections.get(row['name'])
                row['owner_pointer'] = owner.as_pointer() if owner is not None else None
                row['saved_profile'] = _batch_profile(owner)
            return value
    def finished(report):
        global _LAST_BATCH_RESULT
        _LAST_BATCH_RESULT = deepcopy(report)
        from .sf6_unified_messages import format_batch
        model = format_batch(report)
        has_warnings = any(row.get('result', {}).get('warnings') for row in report.get('files', []))
        level = ('ERROR' if report.get('failed_count') and not report.get('succeeded_count')
                 else 'WARNING' if report.get('failed_count') or report.get('pending_count') or has_warnings
                 else model['severity'])
        try:
            operator.sf6UnifiedResult = json.dumps(report, sort_keys=True)
        except ReferenceError:
            pass
        message = ' '.join(model['lines'])
        try:
            notify(message, severity=level)
        except (RuntimeError, AttributeError):
            try:
                operator.report({level}, message)
            except ReferenceError:
                print(message)
    result = begin_review(context, operation='batch', payload=payload, prepare=engine.prepare,
                          publish=engine.publish, on_result=finished, current_payload=current_payload)
    if isinstance(result, set):
        return result
    _LAST_BATCH_REVIEW = result
    operator.sf6UnifiedResult = json.dumps(dict(result.review, status='review_pending'), sort_keys=True)
    return {'INTERFACE'}

class WM_OT_DeleteLoose(Operator):
	bl_label = "Delete Loose Geometry"
	bl_idname = "re_mesh.delete_loose"
	bl_description = "Deletes loose vertices and edges with no faces on selected meshes"
	def execute(self, context):
		if context.selected_objects != []:
			selection = context.selected_objects	
		else:
			selection = bpy.context.scene.objects
		for selectedObj in selection:
			if selectedObj.type == "MESH":
				context.view_layer.objects.active  = selectedObj
				bpy.ops.object.mode_set(mode='EDIT')
				bpy.ops.mesh.select_all(action='SELECT')
				print(f"Deleted loose geometry on {selectedObj.name}")
				bpy.ops.mesh.delete_loose()
				bpy.ops.object.mode_set(mode='OBJECT')
		if context.selected_objects == []: 
			self.report({"INFO"},"Deleted loose geometry on all objects")
		else:
			self.report({"INFO"},"Deleted loose geometry on selected objects")
		return {'FINISHED'}
class WM_OT_RenameMeshToREFormat(Operator):
	bl_label = "Rename Meshes"
	bl_idname = "re_mesh.rename_meshes"
	bl_description = "Renames selected meshes to RE mesh naming scheme (Example: Group_0_Sub_0__Shirts_Mat)"
	def execute(self, context):
		groupIndexDict = dict()
		if context.selected_objects != []:
			selection = context.selected_objects	
		else:
			selection = bpy.context.scene.objects
		for selectedObj in selection:
			if selectedObj.type == "MESH":
				if "Group_" in selectedObj.name:
					try:
						groupID = int(selectedObj.name.split("Group_")[1].split("_")[0])
					except:
						pass
				else:
					print("Could not parse group ID in {selectedObj.name}, setting to 0")
					groupID = 0
				if groupID not in groupIndexDict:
					groupIndexDict[groupID] = 0
				if len(selectedObj.data.materials) > 0:
					materialName = selectedObj.data.materials[0].name.split(".",1)[0].strip()
				else:
					materialName = "NO_MATERIAL"
				selectedObj.name = f"Group_{str(groupID)}_Sub_{str(groupIndexDict[groupID])}__{materialName}"
				groupIndexDict[groupID] += 1
				
		if context.selected_objects == []: 
			self.report({"INFO"},"Renamed all objects to RE Mesh format")
		else:
			self.report({"INFO"},"Renamed selected objects to RE Mesh format")
		return {'FINISHED'}


#Weights

class WM_OT_RemoveZeroWeightVertexGroups(Operator):
	"""Remove all vertex groups that have no weight assigned to them"""
	bl_label = "Remove Empty Vertex Groups"
	bl_idname = "re_mesh.remove_zero_weight_vertex_groups"

	def execute(self, context):
		if context.selected_objects != []:
			selection = context.selected_objects	
		else:
			selection = bpy.context.scene.objects
		for obj in selection:
			emptyGroupList = []
			for vertexGroup in obj.vertex_groups:
				if not any(vertexGroup.index in [g.group for g in v.groups] for v in obj.data.vertices):
					emptyGroupList.append(vertexGroup)
			for group in emptyGroupList:
				obj.vertex_groups.remove(group)
		if context.selected_objects == []: 
			self.report({"INFO"},"Removed empty vertex groups on all objects.")
		else:
			self.report({"INFO"},"Removed empty vertex groups on selected objects.")		
		return {'FINISHED'}
	
class WM_OT_LimitTotalNormalizeAll(Operator):
	"""Limits the amount of bones influences per vertex and normalizes the weights of all vertex groups for all selected meshes"""
	bl_label = "Limit Total and Normalize All"
	bl_idname = "re_mesh.limit_total_normalize"
	maxWeights: EnumProperty(
		name="Weight Limit",
		description="Apply Data to attribute.",
		items=[ ("4", "4 Weights", "Safest option but potentially lower weight quality"),
				("6", "6 Weights (SF6)", "Maximum amount of weights for SF6"),
				("8", "8 Weights", "Note that certain materials may not support 8 weights"),
				("12", "12 Weights (MH Wilds or Newer)", "This is only supported in MH Wilds (and newer potentially)\nNote that some materials may not support 12 weights"),
			   ],
		default = "4"
		
		)
	def invoke(self,context,event):
		return context.window_manager.invoke_props_dialog(self)
	def execute(self, context):
		weightLimit = int(self.maxWeights)
		if context.selected_objects != []:
			selection = context.selected_objects	
		else:
			selection = bpy.context.scene.objects
		for selectedObj in selection:
			if selectedObj.type == "MESH":
				context.view_layer.objects.active  = selectedObj
				bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
				try:
					bpy.ops.object.vertex_group_limit_total(limit = weightLimit)
					bpy.ops.object.vertex_group_normalize_all(lock_active = False)
				except:
					pass
				print(f"Limited total weights to {weightLimit} and normalized {selectedObj.name}")
				bpy.ops.object.mode_set(mode='OBJECT')
		if context.selected_objects == []:
			self.report({"INFO"},f"Limited total weights to {weightLimit} and normalized on all objects")
		else:
			self.report({"INFO"},f"Limited total weights to {weightLimit} and normalized on selected objects")
		return {'FINISHED'}
class WM_OT_CreateMeshCollection(Operator):
	bl_label = "Create Mesh Collection"
	bl_idname = "re_mesh.create_mesh_collection"
	bl_description = "Creates a collection for RE Engine meshes"
	bl_options = {'UNDO'}
	collectionName : bpy.props.StringProperty(name = "Mesh Name",
										 description = "The name of the newly created mesh collection",
										 default = "newMesh"
										)
	lodCount : bpy.props.IntProperty(name = "LOD Amount",
									  description = "The amount of lower quality model levels to switch between.\nLeave this at 1 unless you have a set of lower quality models",
									  default = 1,
									  min = 1,
									  max = 8)
	def execute(self, context):
		if self.collectionName.strip() != "":
			collection = bpy.data.collections.new(self.collectionName+".mesh")
			bpy.context.scene["REMeshLastImportedCollection"] = collection.name
			bpy.context.scene.collection.children.link(collection)
			collection.color_tag = "COLOR_01"
			collection["~TYPE"] = "RE_MESH_COLLECTION"
			bpy.context.scene.re_mdf_toolpanel.meshCollection = collection
			if self.lodCount > 1:
				for i in range(self.lodCount):
					lodCollection = bpy.data.collections.new(f"Main Mesh LOD{str(i)} - {collection.name}")
					lodCollection["LOD Distance"] = 0.167932*(i+1)
					collection.children.link(lodCollection)
			self.report({"INFO"},"Created new RE mesh collection.")
			return {'FINISHED'}
		else:
			self.report({"ERROR"},"Invalid mesh collection name.")
			return {'CANCELLED'}
	
	def invoke(self,context,event):
		return context.window_manager.invoke_props_dialog(self)

EXPORTER_WINDOW_SIZE = 800
SPLIT_FACTOR = .4
BATCH_EXPORT_BUILD = "0.68"

def update_checkAllItems(self, context):
	if self.checkAllItems == True:
		for item in self.itemList_items:
			item.enabled = True
		self.checkAllItems = False
def update_uncheckAllItems(self, context):
	if self.uncheckAllItems == True:
		for item in self.itemList_items:
			item.enabled = False
		self.uncheckAllItems = False

COLLECTION_TYPES = frozenset([
   "RE_MESH_COLLECTION",
   "RE_MDF_COLLECTION",
   "RE_CHAIN_COLLECTION",
   "RE_CLSP_COLLECTION",
   "RE_SFUR_COLLECTION"
])
def checkForChildRECollectionsRecursive(collection):#For checking if a collection should be included in the export list
	if collection.get("~TYPE") in COLLECTION_TYPES:
		return True
	else:
		for child in collection.children:
			if checkForChildRECollectionsRecursive(child):
				return True
	return False
def determineExportPath(modDirectory,exportType,assetPath,scene):
	filePath = ""
	fileVersion = ""
	if exportType == "MESH":
		if "REMeshLastExportedMeshVersion" in scene:
			fileVersion = "." + str(scene["REMeshLastExportedMeshVersion"])
		elif "REMeshLastImportedMeshVersion" in scene:
			fileVersion = "." + str(scene["REMeshLastImportedMeshVersion"])
	
	elif exportType == "MDF":
		if "REMeshLastExportedMDFVersion" in scene:
			fileVersion = "." + str(scene["REMeshLastExportedMDFVersion"])
		elif "REMeshLastImportedMDFVersion" in scene:
			fileVersion = "." + str(scene["REMeshLastImportedMDFVersion"])
	
	elif exportType == "FBXSKEL":
		if "REMeshLastExportedFBXSkelVersion" in scene:
			fileVersion = "." + str(scene["REMeshLastExportedFBXSkelVersion"])
		elif "REMeshLastImportedFBXSkelVersion" in scene:
			fileVersion = "." + str(scene["REMeshLastImportedFBXSkelVersion"])
	
	elif exportType == "SFUR":
		if "REMeshLastExportedSFURVersion" in scene:
			fileVersion = "." + str(scene["REMeshLastExportedSFURVersion"])
		elif "REMeshLastImportedSFURVersion" in scene:
			fileVersion = "." + str(scene["REMeshLastImportedSFURVersion"])
	
	elif exportType == "CHAIN":
		if "REChainLastExportedChainVersion" in scene:
			fileVersion = "." + str(scene["REChainLastExportedChainVersion"])
		elif "REChainLastImportedChainVersion" in scene:
			fileVersion = "." + str(scene["REChainLastImportedChainVersion"])
	
	elif exportType == "CHAIN2":
		if "REChainLastExportedChain2Version" in scene:
			fileVersion = "." + str(scene["REChainLastExportedChain2Version"])
		elif "REChainLastImportedChain2Version" in scene:
			fileVersion = "." + str(scene["REChainLastImportedChain2Version"])
			
	elif exportType == "CLSP":
		if "REChainLastExportedCLSPVersion" in scene:
			fileVersion = "." + str(scene["REChainLastExportedCLSPVersion"])
		elif "REChainLastImportedChain2Version" in scene:
			fileVersion = "." + str(scene["REChainLastImportedCLSPVersion"])
	filePath = os.path.join(modDirectory,assetPath+fileVersion)
	return filePath
	
def populateCollectionList(itemList,collection,recursionLevel,parentName):
	
	item = itemList.add()
	item.sf6BatchInitializing = True
	item.sf6BatchExplicitFields = "[]"
	item.name = collection.name
	if collection.color_tag == "NONE":
		item.icon = "OUTLINER_COLLECTION"
	else:
		item.icon = f"COLLECTION_{collection.color_tag}"
	item.hierarchyLevel = recursionLevel
	item.parentName = parentName
	
	recursionLevel += 1
	if collection.get("~TYPE") not in COLLECTION_TYPES:
		for child in collection.children:
			if checkForChildRECollectionsRecursive(child):
				item.hasChild = True
				populateCollectionList(itemList,child,recursionLevel,parentName = collection.name)
	else:
		item.invalid = True#Will be set to valid once a usable path is entered
		if "BatchExport_enabled" in collection:
			item.enabled = bool(collection["BatchExport_enabled"])
		if "BatchExport_path" in collection:
			item.path = collection["BatchExport_path"]
			#print(f"Batch Export: Loaded previous values for {item.name}")
			
		
		if collection["~TYPE"] == "RE_MESH_COLLECTION":
			item.exportType = "MESH"
			item.exportBlendShapes = batch_preserve_source(collection)
			load_batch_workflow(item, collection)
			# Older collections may have only some of the new saved settings.
			for name, default in _BATCH_MESH_FIX_DEFAULTS.items():
				if name == "limitTotalCount":
					default = getBatchMeshWeightLimitDefault(item.path)
				try:
					value = collection.get("BatchExport_" + name, default)
					if name == "limitTotalCount":
						setBatchMeshWeightLimit(item, value, "BatchExport_" + name in collection)
					else:
						setattr(item, name, value)
				except (TypeError, ValueError) as err:
					setattr(item, name, default)
					print(f"Invalid saved batch setting {name} for {item.name}: {err}")
			
			# Partial saved profiles are valid. A missing sibling must not hide
			# a field the user already chose, or prevent later fields loading.
			for name in ('exportAllLODs', 'preserveSharpEdges', 'rotate90',
			             'useBlenderMaterialName', 'preserveBoneMatrices', 'exportBoundingBoxes'):
				key = 'BatchExport_' + name
				if key in collection:
					try:
						setattr(item, name, collection[key])
					except (TypeError, ValueError) as err:
						print(f"Invalid saved batch setting {name} for {item.name}: {err}")
					
		elif collection["~TYPE"] == "RE_MDF_COLLECTION":
			item.exportType = "MDF"
		elif collection["~TYPE"] == "RE_CHAIN_COLLECTION":
			if ".chain2" in item.name:
				item.exportType = "CHAIN2"
			else:
				item.exportType = "CHAIN"
		elif collection["~TYPE"] == "RE_CLSP_COLLECTION":
			item.exportType = "CLSP"
		elif collection["~TYPE"] == "RE_SFUR_COLLECTION":
			item.exportType = "SFUR"
		
		if item.path == "":
			if bpy.context.scene.re_mdf_toolpanel.modDirectory != "":
				try:
					split = splitNativesPath(bpy.context.scene.re_mdf_toolpanel.modDirectory)
					if split != None:
						assetPath = collection.get("~ASSETPATH",None)
						if assetPath != None:
							item.path = determineExportPath(split[0],item.exportType,assetPath.replace("/",os.sep),bpy.context.scene)
				except Exception as err:
					print(f"Batch Export: Cannot auto determine path for {item.name}: {str(err)}")
		if item.exportType == "MESH" and "BatchExport_limitTotalCount" not in collection:
			setBatchMeshWeightLimit(item, getBatchMeshWeightLimitDefault(item.path))
	if item.exportType == 'MESH':
		policy = item.sf6LodPolicy
		item.sf6LodChoice = policy if policy in ('retain_if_compatible', 'lod0') else ('retain_if_compatible' if item.exportBlendShapes or item.exportAllLODs else 'lod0')
		# Bind the displayed choice to the runtime value before the preservation
		# checkbox can change. A malformed saved policy still requires review.
		if item.path.endswith('.mesh.230110883') and BATCH_LOD_POLICY not in collection:
			item.sf6LodPolicy = item.sf6LodChoice
	item.sf6BatchInitializing = False
	item.sf6BatchExplicitFields = "[]"
class WM_OT_REBatchExporter(Operator):
	bl_label = "RE Batch Exporter"
	bl_idname = "re_mesh.batch_exporter"
	bl_description = "Export all selected RE Engine files quickly"
	bl_options = {'INTERNAL'}
	
	itemList_items: bpy.props.CollectionProperty(type = ExporterNodePropertyGroup)
	itemList_index: bpy.props.IntProperty(name="")
	
	checkAllItems : bpy.props.BoolProperty(
	   name = "Check All Items",
	   description = "Select all files to be exported",
	   default = False,
	   update = update_checkAllItems
	   )
	uncheckAllItems : bpy.props.BoolProperty(
	   name = "Uncheck All Items",
	   description = "Deselect all files to be exported",
	   default = False,
	   update = update_uncheckAllItems
	   )
	
	sf6ReviewOnly: BoolProperty(name="sf6ReviewOnly", default=False,
		options={'HIDDEN', 'SKIP_SAVE'})
	sf6UnifiedOptIn: BoolProperty(name="sf6UnifiedOptIn", default=False,
		options={'HIDDEN', 'SKIP_SAVE'})
	sf6UnifiedResult: StringProperty(name="sf6UnifiedResult", default="",
		options={'HIDDEN', 'SKIP_SAVE'})

	skipPrompt : bpy.props.BoolProperty(#Not exposed to user
	   name = "Skip Conversion Prompt",
	   description = "",
	   options = {'HIDDEN', 'SKIP_SAVE'},
	   default = False)
	def execute(self, context):
		if self.skipPrompt:
			self.populate(context)
		for item in self.itemList_items:
			if item.exportType == 'MESH' and item.path.endswith('.mesh.230110883'):
				apply_batch_new_profile_defaults(item, bpy.data.collections.get(item.name), enable_unified=True)
		return begin_batch_review(self, context)

	def populate(self, context):
		parentDict = {None:None}
		for collection in bpy.data.collections:
			parentDict[collection] = None
			
		for collection in bpy.data.collections:
			for child in collection.children:
				parentDict[child] = collection
		collectionRoots = set()
		for collection in bpy.data.collections:
			if collection.get("~TYPE") in COLLECTION_TYPES:
				parentCol = parentDict[collection]
				#print(f"Found supported collection: {collection.name},Parent:{parentDict[collection]}")
				
				while parentDict[parentCol] != None:
					parentCol = parentDict[parentCol]
				else:
					#print(f"Root collection:{parentCol.name}")
					if parentCol == None:
						collectionRoots.add(collection)
					else:
						collectionRoots.add(parentCol)
		
		#Populate list
		self.itemList_items.clear()
		for collection in collectionRoots:
			populateCollectionList(self.itemList_items, collection, recursionLevel = 0,parentName = "")
		for item in self.itemList_items:
			if item.exportType == "MESH" and item.path.endswith('.mesh.230110883'):
				apply_batch_new_profile_defaults(item, bpy.data.collections.get(item.name), enable_unified=True)
		#print(f"Item Count: {len(self.itemList_items)}")
		#Add fbxskel armatures for export
		for armatureObj in [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (".fbxskel" in obj.name.lower() or (".skeleton" in obj.name.lower()))]:
			item = self.itemList_items.add()
			item.name = armatureObj.name
			item.icon = "ARMATURE_DATA"
			item.exportType = "FBXSKEL"
			item.invalid = True
			if "BatchExport_enabled" in armatureObj:
				item.enabled = bool(armatureObj["BatchExport_enabled"])
			if "BatchExport_path" in armatureObj:
				item.path = armatureObj["BatchExport_path"]
			if item.path == "":
				if bpy.context.scene.re_mdf_toolpanel.modDirectory != "":
					try:
						split = splitNativesPath(bpy.context.scene.re_mdf_toolpanel.modDirectory)
						if split != None:
							assetPath = armatureObj.get("~ASSETPATH",None)
							if assetPath != None:
								item.path = determineExportPath(split[0],item.exportType,assetPath.replace("/",os.sep),bpy.context.scene)
					except Exception as err:
						print(f"Batch Export: Cannot auto determine path for {item.name}: {str(err)}")
	def invoke(self, context, event):
		if self.skipPrompt:
			return self.execute(context)
		else:
			self.populate(context)
			#Move cursor to center so extract window is at the center of the window
			if context.region and context.window:
				context.window.cursor_warp(context.region.width // 2, context.region.height)
		
			return context.window_manager.invoke_props_dialog(self,width = EXPORTER_WINDOW_SIZE,confirm_text = "Review and export")

	
	def draw(self,context):
		layout = self.layout
		rowCount = 13
		uifontscale = 9 * context.preferences.view.ui_scale
		row = layout.row().separator()
		split = layout.split(factor = SPLIT_FACTOR)#Indent list slightly to make it more clear it's a part of a sub panel
		col1 = split.column()
		split2 = col1.split()
		col1sub1 = split2.column()
		col1sub1.alignment = "LEFT"
		col1sub1.label(text=f"Files ({sum(1 for item in self.itemList_items if (not item.hasChild and item.enabled and item.exportType))} selected)")
		col1sub2 = col1.column()
		row = split2.row()
		row.alignment = "RIGHT"
		row.prop(self,"checkAllItems",icon="CHECKMARK", icon_only=True)
		row.prop(self,"uncheckAllItems",icon="X", icon_only=True)
		col1.template_list(
			listtype_name = "MESH_UL_REExporterList", 
			list_id = "itemList",
			dataptr = self,
			propname = "itemList_items",
			active_dataptr = self, 
			active_propname = "itemList_index",
			rows = rowCount,
			type='DEFAULT'
			)
		col2 = split.column()
		col2.label(text="Export Settings")
		from .sf6_unified_dialogs import draw_details_button
		draw_details_button(col2)
		box = col2.box()
		if 0 <= self.itemList_index < len(self.itemList_items):
			item = self.itemList_items[self.itemList_index]
			if not item.hasChild and item.exportType != "":
				box.label(text=f"Type: {item.exportType}")
				box.label(text="Export Path")
				
				box.prop(item,"path")
				if item.invalid:
					row = box.row()
					row.alert = True
					row.label(text="Path is empty or missing the file version number on the end. ",icon = "ERROR")
				if item.exportType == "MESH":
					if item.path.endswith('.mesh.230110883'):
						box.prop(item, 'exportBlendShapes')
						box.prop(item, 'sf6LodChoice')
						if item.exportBlendShapes:
							box.label(text='Required shape-key losses are reviewed before export.', icon='INFO')
						else:
							box.label(text='Source corrective shapes will not be preserved.', icon='INFO')
					else:
						box.prop(item, 'exportAllLODs')
						if item.path.endswith('.241111606'):
							box.prop(item, 'shapeKeyExportMode')
					box.prop(item,"autoSolveRepeatedUVs")
					box.prop(item,"preserveSharpEdges")
					box.prop(item,"splitLoopVertices")
					row = box.row(align=True)
					row.prop(item,"limitTotal")
					limitRow = row.row(align=True)
					limitRow.enabled = item.limitTotal
					limitRow.prop(item,"limitTotalCount")
					box.prop(item,"normalizeWeights")
					box.prop(item, "rotate90")
					box.prop(item, "useBlenderMaterialName")
					box.prop(item, "preserveBoneMatrices")
					box.prop(item, "exportBoundingBoxes")
			else:
				box.label(text=f"Select a file from the list to configure export settings.")
		else:
			box.label(text='No export entries are available.')
				

class WM_OT_SolveRepeatedUVs(Operator):
	bl_label = "Solve Repeated UVs"
	bl_idname = "re_mesh.solve_repeated_uvs"
	bl_description = "Splits connected UV islands"
	def execute(self, context):

		if context.selected_objects != []:
			selection = context.selected_objects	
		else:
			selection = bpy.context.scene.objects
		
		solveRepeatedUVs(selection)
		
		if context.selected_objects == []: 
			self.report({"INFO"},"Solved repeated UVs on all objects.")
		else:
			self.report({"INFO"},"Solved repeated UVs on selected objects.")
		return {'FINISHED'}
	
class WM_OT_QuickBatchExport(Operator):
	bl_label = "Quick Batch Export"
	bl_idname = "re_mesh.quick_batch_export"
	bl_description = "Use the previous batch settings and review any required shape-key losses before export"
	sf6ReviewOnly: BoolProperty(name="sf6ReviewOnly", default=False,
		options={'HIDDEN', 'SKIP_SAVE'})
	sf6UnifiedOptIn: BoolProperty(name="sf6UnifiedOptIn", default=False,
		options={'HIDDEN', 'SKIP_SAVE'})
	def execute(self, context):
		return bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', skipPrompt=True,
			sf6UnifiedOptIn=getattr(self, "sf6UnifiedOptIn", False), sf6ReviewOnly=getattr(self, "sf6ReviewOnly", False))
