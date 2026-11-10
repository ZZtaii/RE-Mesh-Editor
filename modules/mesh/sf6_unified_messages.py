"""Pure presentation helpers. Never grant consent or change export decisions.
Approved copy: consolidated 37-card review, 8 November 2026.
Rig, LOD and settings additions use the later authorized editorial judgment.
"""
from copy import deepcopy
import json

APPROVED_CARDS = {1: {'id': 1, 'section': '1. Loss review and choices', 'title': 'A section needs fallback', 'current': 'PROTOTYPE_CORRECTIVE_LOSS: {reason}: {object}', 'proposed': 'Export this section without shape keys?\n{object} / {material}: {plain_reason}.\nIts current evaluated shape will be exported, but these shape keys will not: {key_names}. Compatible sections will keep their verified corrective shapes.', 'conditions': 'Show the actual affected section and all known names. Say “compatible sections” only when the plan retains some. “Current evaluated shape” includes the current key values and enabled modifier evaluation, not a promise of identical in-game appearance.'}, 2: {'id': 2, 'section': '1. Loss review and choices', 'title': 'The whole joined object is affected', 'current': 'Evaluate whole fallback object using Ordinary geometry before partitioning', 'proposed': 'Export this whole object without shape keys?\n{object}: its modifiers must be evaluated before the material sections can be separated. All affected sections of this object will use Ordinary geometry. These shape keys will not be exported: {key_names}.', 'conditions': 'Whole-object loss must replace any earlier section-only approval. Do not silently broaden consent.'}, 3: {'id': 3, 'section': '1. Loss review and choices', 'title': 'The entire selection needs fallback', 'current': 'Missing embedded source / Embedded source hash mismatch / source discovery unavailable or ambiguous', 'proposed': 'Export the selection without blend shapes?\nThe original source cannot be verified: {plain_reason}. This export will use the selected Blender geometry without blend shapes. Known keys that will not be exported: {key_names}. Original corrective shapes may be unavailable to identify.', 'conditions': 'For full-collection export, replace “selection” with “collection.” Do not say no correctives are lost merely because none can be identified. Do not reuse corrupt source bytes. If the source is verified but its normal-recalculation table is unsupported, say “The source is verified, but its normal-recalculation data cannot be preserved. This export must use Ordinary geometry without blend shapes.”'}, 4: {'id': 4, 'section': '1. Loss review and choices', 'title': 'Buttons and dismissal', 'current': 'No finalized production dialog', 'proposed': 'Cancel export\nExport without these shape keys', 'conditions': 'For whole-output fallback, use “Export without blend shapes.” Close/Esc means Cancel. The export option approves only the listed losses for this plan; it does not bypass rig or output validation.'}, 5: {'id': 5, 'section': '1. Loss review and choices', 'title': 'Cancellation', 'current': 'No finalized cancellation message', 'proposed': 'Export cancelled. No file was written or replaced.', 'conditions': 'Use only when cancellation occurs before publishing the output. For mod-folder export, state “The mod folder was not updated” only when its packaging transaction confirms that.'}, 6: {'id': 6, 'section': '2. Why shapes cannot be retained', 'title': 'Changed material', 'current': 'Material changed on source section', 'proposed': 'This section uses a different material from its original source. Its corrective shapes cannot be matched safely.', 'conditions': 'Offer cancellation to restore the original material, or the scoped fallback choice. Replace the earlier blanket “restore material or switch the entire mesh to ordinary export.”'}, 7: {'id': 7, 'section': '2. Why shapes cannot be retained', 'title': 'Extra, renamed or foreign keys', 'current': 'Additional shape keys have no verified source corrective identity / Foreign or unproven vertex origin', 'proposed': 'Some shape keys or vertices cannot be matched to the original source. This section must use Ordinary geometry without live shape keys.', 'conditions': 'List the union of known source keys and actual remaining Blender keys. Never claim unknown or previously deleted names were recovered.'}, 8: {'id': 8, 'section': '2. Why shapes cannot be retained', 'title': 'Unsupported key setup or missing keys', 'current': 'Non-Basis-relative or masked target keys / Missing compatible target shape keys', 'proposed': 'The shape-key setup does not match the original source, or required keys are missing. This section cannot retain its original corrective shapes.', 'conditions': 'Details should distinguish missing keys from absolute, masked or non-Basis-relative keys. Cancel lets the user repair the setup.'}, 9: {'id': 9, 'section': '2. Why shapes cannot be retained', 'title': 'Ambiguous joined identity', 'current': 'Ambiguous or missing joined source row identities', 'proposed': 'The original vertices or faces cannot be identified reliably in this joined section. Its corrective shapes cannot be retained safely.', 'conditions': 'Do not apply to verified compatible joins; those can retain their correctives.'}, 10: {'id': 10, 'section': '2. Why shapes cannot be retained', 'title': 'World shear', 'current': 'World shear requires original-scene Ordinary evaluation', 'proposed': 'This object’s transform includes shear. Ordinary evaluation is needed to preserve its geometry, so its live shape keys will not be exported.', 'conditions': 'Show the real loss scope. Do not claim applying scale or removing a parent is always a safe fix.'}, 11: {'id': 11, 'section': '2. Why shapes cannot be retained', 'title': 'Duplicate source claims', 'current': 'Duplicate source claim; ordinary fallback loses its corrective links / PROTOTYPE_AMBIGUOUS_OWNER', 'proposed': 'These objects claim the same original corrective shapes: {objects}. Choose at most one compatible copy to retain them. Other copies need approval to export without those shapes.', 'conditions': 'Proposed picker label: “Keep source correctives on”. Choices: a named eligible object, or “None”. Choosing an owner is not approval for losses on the other copies. Do not promise every duplicated section has an eligible owner.'}, 12: {'id': 12, 'section': '3. Failures after or before approval', 'title': 'Scene or options changed', 'current': 'PROTOTYPE_EXPORT_PLAN_CHANGED; stale loss token requests new consent', 'proposed': 'Export stopped: the scene or export settings changed after review. Review the updated export plan and try again. The previous output was not replaced.', 'conditions': 'Guarded paths check before os.replace. Do not claim every external file, driver or simulation cache change is detected.'}, 13: {'id': 13, 'section': '3. Failures after or before approval', 'title': 'No valid bone weights', 'current': 'PROTOTYPE_ZERO_SKIN_WEIGHTS', 'proposed': 'Export stopped: some exported vertices have no positive bone weights. Check the rig and any modifier that creates geometry, then export again. The previous output was not replaced.', 'conditions': 'Actual Ordinary export can return success while producing these invalid weights. Never offer “Export anyway” for this validation failure.'}, 14: {'id': 14, 'section': '3. Failures after or before approval', 'title': 'Broken shading or transform payload', 'current': 'PROTOTYPE_INVALID_ORDINARY_SHADING / PROTOTYPE_ORDINARY_SHADING_PROVENANCE_LOST / PROTOTYPE_INVALID_ORDINARY_WORLD_MATRIX', 'proposed': 'Export stopped: the exporter could not preserve the evaluated shading or transform data. The previous output was not replaced. Save a copy of the project and include this error in a bug report.', 'conditions': 'Show the precise cause and object in Details. Do not advise disabling validation or imply a user weighting fix will repair an internal payload failure.'}, 15: {'id': 15, 'section': '3. Failures after or before approval', 'title': 'Invalid staged output', 'current': 'PROTOTYPE_INVALID_ORDINARY_LAYOUT / EMPTY_ORDINARY_OUTPUT / INVALID_POSITION / INVALID_FACE_REFERENCE / INVALID_MATERIAL_REFERENCE / INVALID_VERTEX_STREAM / UNSUPPORTED_SKIN_WEIGHT_LAYOUT', 'proposed': 'Export stopped: the generated mesh failed validation ({plain_reason}). The previous output was not replaced. Review the affected object and export details before retrying.', 'conditions': 'Translate the actual reason: empty output, invalid coordinates, invalid face/material reference, invalid vertex data or unsupported weight layout. Keep the original code in Details; avoid a generic “corrupt source” diagnosis.'}, 16: {'id': 16, 'section': '3. Failures after or before approval', 'title': 'Ordinary export failed or folder missing', 'current': 'PROTOTYPE_ORDINARY_EXPORT_FAILED / PROTOTYPE_OUTPUT_DIRECTORY_MISSING', 'proposed': 'Export stopped: Ordinary fallback failed. Fix the mesh error shown in Details, then retry.\nOR: Choose an existing output folder, then export again.', 'conditions': 'No successful export has occurred. For the guarded fallback failure, the existing destination is retained. For OS write/replace failures, show the actual OS reason and determine outcome before promising preservation.'}, 17: {'id': 17, 'section': '4. Successful exports and visible limitations', 'title': 'Mixed Hybrid result', 'current': 'Generic “SF6 hybrid LOD0” summary and per-part console details', 'proposed': 'Exported LOD0: {retained} sections kept verified corrective shapes; {fallback} sections used Ordinary geometry. Approved shape-key losses are listed in Details. Lower LODs were not exported.', 'conditions': 'Use section counts, not object counts. Omit loss wording when no known keys were lost. Do not double-count whole-object and per-section reports.'}, 18: {'id': 18, 'section': '4. Successful exports and visible limitations', 'title': 'All-Ordinary result', 'current': 'Report mode = ordinary_fallback; output_shape_links = 0', 'proposed': 'Exported LOD0 using Ordinary fallback. This file has no blend shapes. Lower LODs were not exported.', 'conditions': 'Do not label this “shapes preserved” or imply source preservation. If source could not be verified, retain that warning in Details.'}, 19: {'id': 19, 'section': '4. Successful exports and visible limitations', 'title': 'No loss decision was needed', 'current': 'No known shape names on fallback section; no consent event', 'proposed': 'Exported {count} sections as Ordinary geometry. No known shape keys were identified on those sections.', 'conditions': 'Do not say “no corrective loss” when source data is unavailable. This is an informational result, not an invented mandatory backend prompt.'}, 20: {'id': 20, 'section': '4. Successful exports and visible limitations', 'title': 'Compatible joined sections', 'current': 'Verified joined-part cases preserve correctives', 'proposed': 'Joined sections kept their verified source corrective shapes.', 'conditions': 'Only show after that outcome is verified. Joining alone is not a warning. Details may show each retained section and its key names.'}, 21: {'id': 21, 'section': '4. Successful exports and visible limitations', 'title': 'New materials and missing details', 'current': 'Added materials need a matching MDF update / no per-part report returned', 'proposed': 'Exported with new materials: {names}. Add matching entries to the MDF before using this mesh.\nOR: Export completed, but preservation details are unavailable. Review the output before using it.', 'conditions': 'Both deserve visible warnings. Do not imply the MDF was updated, or that a missing report proves corruption.'}, 22: {'id': 22, 'section': '4. Successful exports and visible limitations', 'title': 'Readable details', 'current': 'NO SHAPES / zero deltas / console-only technical report', 'proposed': 'Exported without live shape keys: {object} / {material}. Reason: {reason}. Keys not exported: {names}.\nFor retained linked shapes with zero movement: “These linked shapes produce no movement on the listed vertices.”', 'conditions': 'Keep no links, zero movement and approved removal distinct. Include destination, actual scope, counts, retained sections and reasons. A persistent Details panel remains a proposed UI feature.'}, 23: {'id': 23, 'section': '5. Retained proposals: strict source mode', 'title': 'Mode and format requirements', 'current': 'Hybrid requires Preserve Source Data / can only write mesh.230110883', 'proposed': 'Enable Preserve Source Data to use SF6 Hybrid Shape Export.\nUse an SF6 .mesh.230110883 filename for Hybrid export.', 'conditions': 'Keep the existing checks. A general mesh-export filename error should give a game-appropriate suffix, not force SF6 for every game.'}, 24: {'id': 24, 'section': '5. Retained proposals: strict source mode', 'title': 'Missing or damaged strict source', 'current': 'Missing embedded source / decode failure / checksum mismatch', 'proposed': 'Source-preserving export stopped: the stored original mesh is missing or unreadable. Re-import the original mesh into a new collection and transfer your edits carefully.', 'conditions': 'Use the specific reason, including checksum mismatch when applicable. In Hybrid, these now lead to the fallback review on page 2 instead. Never bypass a checksum.'}, 25: {'id': 25, 'section': '5. Retained proposals: strict source mode', 'title': 'UV, weights or topology changed', 'current': 'UV edits / weight edits / added or duplicated vertices and faces / rewired topology unsupported', 'proposed': '{object}: {specific_data} changed. Restore the original data for source-preserving export, or use Hybrid and review its preservation limits.', 'conditions': 'State whether UVs, weights, vertices or face connections changed. Hybrid still validates compatibility and may request scoped shape loss.'}, 26: {'id': 26, 'section': '5. Retained proposals: strict source mode', 'title': 'Transforms and axis conversion', 'current': 'Apply position edits in Edit Mode / use same axis conversion setting', 'proposed': '{object}: restore the original object transform and make position edits in Edit Mode.\nMatch export axis conversion to the setting used for the source import.', 'conditions': 'Strict source mode only. Show the required axis option value when available. Apply Transform is not automatically equivalent to restoring source transforms.'}, 27: {'id': 27, 'section': '5. Retained proposals: strict source mode', 'title': 'Shape-key list, setup or range', 'current': 'Shape keys added/renamed/removed / Basis-relative requirement / edit outside authored range', 'proposed': '{object}: restore the original shape-key names and set of keys. Use relative keys based on Basis, with no vertex-group masks.\nShape key “{key}” moves vertices outside its supported source range. Restore those vertices or reduce the edit.', 'conditions': 'Show only the relevant repair, not all clauses. Hybrid may offer fallback for incompatible setups; this wording must not hide that choice.'}, 28: {'id': 28, 'section': '5. Retained proposals: strict source mode', 'title': 'Unsupported selected-source layout', 'current': 'Subset rejects streamed, secondary, shadow or auxiliary source layouts', 'proposed': 'Selected-part source export does not support this source layout ({reason}). Try the full source collection if appropriate, or use another export mode and review its preservation limits.', 'conditions': 'A full export is an option, not a guaranteed fix. Hybrid missing/invalid normal-recalculation support can instead require whole-selection Ordinary fallback; explain that scope.'}, 29: {'id': 29, 'section': '6. Retained proposals: actionable input errors', 'title': 'Selection and target rig', 'current': 'No selected meshes / one root armature required / bind mixed geometry', 'proposed': 'Select meshes in the target collection or turn off Selected Objects Only.\nThis collection needs exactly one target armature at its root.\n{object}: use exactly one enabled Armature modifier linked to that armature.', 'conditions': 'Show the specific failed condition. Do not instruct the user to delete extra armatures when moving them out is sufficient.'}, 30: {'id': 30, 'section': '6. Retained proposals: actionable input errors', 'title': 'Weights and materials', 'current': 'Invalid or unweighted vertices / missing bones / missing material', 'proposed': '{object}: assign valid positive bone weights to every vertex. Weighted groups must match bones on the target armature.\n{object}: assign a material to every used section.', 'conditions': 'Normalize All alone does not fix unweighted vertices. For excessive weights, show the validated target-specific limit, not a hard-coded 6, 8 or 16.'}, 31: {'id': 31, 'section': '6. Retained proposals: actionable input errors', 'title': 'UVs, empty geometry and excessive faces', 'current': 'No UV map / no vertices or faces / excessive-face joke', 'proposed': '{object}: add a UV map and unwrap the mesh.\n{object}: add valid geometry, exclude it from export, or remove it if unneeded.\nToo many faces for this format. Split the object or reduce its face count.', 'conditions': 'Use the applicable message and actual validated limit. Remove jokes and the contradictory “4 million” claim. Material splitting advice belongs only to paths that require one material per submesh.'}, 32: {'id': 32, 'section': '6. Retained proposals: actionable input errors', 'title': 'Multires and Data Transfer', 'current': 'Missing/invalid displacement file / invalid normal source or corner mapping', 'proposed': '{object}: restore the Multires displacement file or pack valid displacement data before exporting.\n{object}: choose a separate mesh as the Data Transfer normal source.\n{modifier}: use spatial face mapping or matching evaluated topology.', 'conditions': 'A corrupt file is not repaired simply by packing it. Explain the actual failure; do not combine unrelated fixes.'}, 33: {'id': 33, 'section': '6. Retained proposals: actionable input errors', 'title': 'Evaluation and isolation failures', 'current': 'Nonfinite coordinates / Basis-key topology mismatch / private copy shares keys / lost row mapping', 'proposed': '{object}: modifier evaluation produced invalid coordinates or incompatible shape-key topology. Adjust the responsible modifier and retry.\nFor internal failures: “Export stopped: {specific export operation} failed. Save a copy of the project and report this error with your Blender and add-on versions.”', 'conditions': 'Hybrid fallback is not a promise that every modifier works. Do not tell users to delete shape keys or bypass checks to solve an internal isolation error.'}, 34: {'id': 34, 'section': '7. Retained proposals: outcomes and packaging', 'title': 'Failures, warnings and success', 'current': 'Failed imports/exports reported as INFO / “Warnings occured” with error icon', 'proposed': '{format} import/export failed. Review the specific error, correct it, then retry.\nExport completed with warnings. Review them before using the file.', 'conditions': 'Use ERROR for blocked operations, WARNING for completed operations needing attention, INFO for ordinary success. Show the actual error, not just “check console.”'}, 35: {'id': 35, 'section': '7. Retained proposals: outcomes and packaging', 'title': 'Partial batch and multi-import results', 'current': 'All-files success derived from last result / failures hidden by unchecked operator status', 'proposed': 'Completed: {succeeded}/{total} files; {failed} failed. First failure: {file}: {reason}. Review failed files and retry them.\nNo files are enabled. Enable at least one batch entry.', 'conditions': 'Correct accounting requires checking every operation’s result, including CANCELLED. These are reporting logic fixes, not string replacements.'}, 36: {'id': 36, 'section': '7. Retained proposals: outcomes and packaging', 'title': 'Mod-folder destination and existing content', 'current': 'Choose parent directory / conflicting folder / mesh export failed', 'proposed': 'Choose an existing Parent Directory; the mod folder will be created inside it.\nFolder already contains {mods}. Exporting adds or replaces this mesh.\nMesh export failed. The mod folder was not updated.', 'conditions': 'Use the last guarantee only before publication or after verified rollback. Keep current checks for invalid names, Windows-reserved names, previews and menu-only folders; name the specific field and remedy.'}, 37: {'id': 37, 'section': '7. Retained proposals: outcomes and packaging', 'title': 'Other retained improvements', 'current': 'Missing MDF collection / appended materials / forced bone binding / repeated warnings', 'proposed': 'Select an MDF collection, then export again.\nNew materials need matching MDF entries: {names}.\nNo bones have assigned weights. Export will bind all vertices to “{bone}”. Check weights before use.', 'conditions': 'Make consequential automatic repairs visible. Deduplicate codec warnings, explain normalization unless intentional, and fix the workspace conversion popup that can become blank. Do not imply import/export success validates game behavior.'}}

CANCEL_EXPORT = 'Cancel export'
EXPORT_WITHOUT_KEYS = 'Export without these shape keys'
EXPORT_WITHOUT_BLEND_SHAPES = 'Export without blend shapes'
WORKFLOW_LABEL = 'Preserve Compatible Source Data'
LOD_POLICY_LABEL = 'LOD Export'
RIG_CHOICE_LABEL = 'Export the edited scene rest rig'

REASONS = {
 'Material changed on source section': (6, 'This section uses a different material from its original source. Its corrective shapes cannot be matched safely.'),
 'Additional shape keys have no verified source corrective identity': (7, 'Some shape keys or vertices cannot be matched to the original source. This section must use Ordinary geometry without live shape keys.'),
 'Foreign or unproven vertex origin': (7, 'Some shape keys or vertices cannot be matched to the original source. This section must use Ordinary geometry without live shape keys.'),
 'Non-Basis-relative or masked target keys': (8, 'The shape-key setup does not match the original source. This section cannot retain its original corrective shapes.'),
 'Missing compatible target shape keys': (8, 'Required shape keys are missing. This section cannot retain its original corrective shapes.'),
 'Ambiguous or missing joined source row identities': (9, 'The original vertices or faces cannot be identified reliably in this joined section. Its corrective shapes cannot be retained safely.'),
 'World shear requires original-scene Ordinary evaluation': (10, 'This object’s transform includes shear. Ordinary evaluation is needed to preserve its geometry, so its live shape keys will not be exported.'),
 'Evaluate whole fallback object using Ordinary geometry before partitioning': (2, 'Its modifiers must be evaluated before the material sections can be separated.'),
 'Duplicate source claim; ordinary fallback loses its corrective links': (11, 'Another copy retains these original corrective shapes. This copy needs approval to export without them.'),
 'explicit_ordinary_policy': (19, 'Ordinary export was selected. Live shape keys will not be exported.'),
 'rest_rig_changed_corrective_compatibility_unproven': (None, 'The scene rest rig changed. Its compatibility with the original corrective shapes cannot be verified.'),
 'cached_rig_metadata_changed': (None, 'Stored bone-matrix data changed. Original corrective compatibility cannot be verified.'),
 'axis_conversion_changed': (None, 'Axis conversion changed from the source import. Original corrective compatibility cannot be verified.'),
}
ERRORS = {
 'UNIFIED_OBJECT_MODE_REQUIRED': (None, 'Switch to Object Mode, then review and export again. Preview does not commit pending edits or change modes for you.'),
 'PROTOTYPE_EXPORT_PLAN_CHANGED': (12, 'The scene or export settings changed after review. Review the updated export plan and try again.'),
 'UNIFIED_EXPORT_PLAN_CHANGED': (12, 'The scene or export settings changed after review. Review the updated export plan and try again.'),
 'UNIFIED_REVIEWED_OUTPUT_CHANGED': (12, 'The generated mesh changed after review. Review the updated export plan and try again.'),
 'PROTOTYPE_ZERO_SKIN_WEIGHTS': (13, 'Some exported vertices have no positive bone weights. Check the rig and any modifier that creates geometry, then export again.'),
 'PROTOTYPE_OUTPUT_DIRECTORY_MISSING': (16, 'Choose an existing output folder, then export again.'),
 'PROTOTYPE_ORDINARY_EXPORT_FAILED': (16, 'Ordinary fallback failed. Fix the mesh error shown in Details, then retry.'),
}
# Mode-dependency copy added under the user's later editorial judgment.
ERRORS.update({
 'UNIFIED_EDITOR_SYNC_UNSAFE_EDITORS': (None, 'This export includes live sculpt data. Finish the Edit sessions listed in Details first so Blender can synchronize it without changing their shape keys or rig.'),
 'UNIFIED_EDITOR_SYNC_UNAVAILABLE': (None, 'Blender cannot synchronize the current live edits from this context. Finish the active tool and try exporting again; see Details if the problem continues.'),
 'UNIFIED_EDITOR_SYNC_FAILED': (None, 'Blender could not finish synchronizing the current edits. Export stopped. Check the editing state and the error in Details before retrying.'),
 'UNIFIED_EDITOR_SYNC_CONTEXT_CHANGED': (None, 'The editing context changed during synchronization. Export stopped. Check the current mode and selection before retrying.'),
 'UNIFIED_CURVE_SHAPE_KEY_EDIT_UNSUPPORTED': (None, 'Finish editing the shape-keyed Curve or Surface listed in Details, then export again. Blender cannot safely copy its pending point-to-key mapping.'),
 'UNIFIED_SURFACE_TOPOLOGY_EDIT_UNSUPPORTED': (None, 'Finish changing the control-point grid of the Surface listed in Details, then export again. Its pending topology cannot be copied safely.'),
 'UNIFIED_FONT_EDIT_UNSUPPORTED': (None, "Finish editing the Text object listed in Details, then export again. Blender's private copy does not include its pending text."),
})

for _code in ('PROTOTYPE_INVALID_ORDINARY_SHADING','PROTOTYPE_ORDINARY_SHADING_PROVENANCE_LOST','PROTOTYPE_INVALID_ORDINARY_WORLD_MATRIX'):
    ERRORS[_code]=(14, 'The exporter could not preserve the evaluated shading or transform data. Save a copy of the project and include this error in a bug report.')
for _code,_reason in {'PROTOTYPE_INVALID_ORDINARY_LAYOUT':'unsupported output layout','PROTOTYPE_EMPTY_ORDINARY_OUTPUT':'empty output','PROTOTYPE_INVALID_POSITION':'invalid coordinates','PROTOTYPE_INVALID_FACE_REFERENCE':'invalid face reference','PROTOTYPE_INVALID_MATERIAL_REFERENCE':'invalid material reference','PROTOTYPE_INVALID_VERTEX_STREAM':'invalid vertex data','PROTOTYPE_UNSUPPORTED_SKIN_WEIGHT_LAYOUT':'unsupported weight layout'}.items():
    ERRORS[_code]=(15, 'The generated mesh failed validation ('+_reason+'). Review the affected object and export details before retrying.')


def _list(value):
    if value is None: return []
    if isinstance(value,(list,tuple,set)): return list(value)
    return [value]


def _names(loss):
    return list(dict.fromkeys(str(x) for x in _list(loss.get('shape_names',loss.get('lost_corrective_names',loss.get('key_names',[]))))))


def _public(value):
    """Exclude execution grants/tokens from presentation metadata."""
    if isinstance(value,dict):
        return {k:_public(v) for k,v in value.items() if 'token' not in k.lower() and not k.startswith('_sf6')}
    if isinstance(value,(list,tuple)): return [_public(v) for v in value]
    return value


def _model(title,lines,**kwargs):
    return dict(title=title,lines=lines,severity=kwargs.pop('severity','INFO'),details=kwargs.pop('details',{}),card_ids=kwargs.pop('card_ids',[]),unmapped_codes=kwargs.pop('unmapped_codes',[]),**kwargs)


def review_card(loss, retained_sections=0, selected_only=True):
    loss=deepcopy(dict(loss)); scope=loss.get('scope'); names=_names(loss)
    raw=str(loss.get('reason',loss.get('source_reason','')))
    known=REASONS.get(raw); reason=known[1] if known else raw
    unknown=[] if known or not raw else [raw]
    obj=str(loss.get('object','Unnamed object')); material=str(loss.get('material') or ', '.join(map(str,_list(loss.get('materials')))) or 'material not identified')
    keys=', '.join(names) if names else 'No known shape-key names could be identified.'
    ids=[]; lines=[]
    if scope in ('output','selection','collection'):
        ids=[3]; title='Export the '+('selection' if selected_only and scope!='collection' else 'collection')+' without blend shapes?'
        verified=loss.get('source_status')=='verified' or str(loss.get('source_status','')).startswith('verified_source_')
        if verified:
            lines=[reason or 'The original source data cannot be preserved for this export.', 'This export will use Ordinary geometry without blend shapes.']
        else:
            lines=['The original source cannot be verified: '+(reason or 'source data is unavailable')+'.', 'This export will use the selected Blender geometry without blend shapes.', 'Original corrective shapes may be unavailable to identify.']
    elif scope in ('object','whole_object','whole_object_before_modifiers'):
        ids=[2]; title='Export this whole object without shape keys?'
        lines=[obj+': '+(reason or 'This whole object requires Ordinary evaluation.'), 'All affected sections of this object will use Ordinary geometry.']
    elif scope=='section':
        ids=[1]; title='Export this section without shape keys?'
        lines=[obj+' / '+material+': '+(reason or 'Original corrective compatibility could not be verified.'), 'Its current evaluated shape will be exported, but its live shape keys will not.']
        if retained_sections: lines.append('Compatible sections will keep their verified corrective shapes.')
    else:
        title='Review shape-key loss'; lines=['Affected scope has not been identified. Do not approve this loss until the export plan identifies its scope.',obj+': '+reason]; unknown.append('missing_or_unknown_loss_scope:'+str(scope))
    lines.append('Keys not exported: '+keys)
    if scope not in ('output','selection','collection') and loss.get('source_status')=='unverified':
        lines.append('Original corrective shapes may be unavailable to identify.')
    if loss.get('file'): lines.insert(0,'File: '+str(loss['file']))
    if known and known[0] and known[0] not in ids: ids.append(known[0])
    return _model(title,lines,details=_public(loss),card_ids=ids,unmapped_codes=unknown,buttons={'cancel':CANCEL_EXPORT,'export':EXPORT_WITHOUT_BLEND_SHAPES if scope in ('output','selection','collection') else EXPORT_WITHOUT_KEYS})


def format_error(error, previous_output_preserved=False):
    raw=str(error)
    # The mesh dispatcher wraps backend errors once; lookup the original code
    # while retaining the complete original error in technical Details.
    prefix='SF6 hybrid export: '
    lookup=raw[len(prefix):] if raw.startswith(prefix) else raw
    code=lookup.split(':',1)[0].strip(); match=ERRORS.get(code)
    if not match:
        legacy=format_legacy_error(lookup)
        if legacy: legacy['details']['technical_error']=raw
        if legacy:
            if previous_output_preserved: legacy['lines'].append('The previous output was not replaced.')
            return legacy
    lines=[match[1] if match else 'Review the specific error in Details, correct it if possible, then retry.']
    if not match: lines.append('Error: '+raw)
    if previous_output_preserved: lines.append('The previous output was not replaced.')
    return _model('Export stopped',lines,severity='ERROR',details={'technical_error':raw},card_ids=[match[0]] if match and match[0] is not None else [],unmapped_codes=[] if match else [code])


def format_cancel(*, before_publication=False, folder_unchanged=False):
    lines=['Export cancelled.']
    if before_publication: lines=['Export cancelled. No file was written or replaced.']
    if folder_unchanged: lines.append('The mod folder was not updated.')
    return _model('Export cancelled',lines,card_ids=[5])


def view_model(report):
    review=report.get('review',report); retained=review.get('retained_sections',[])
    cards=[review_card(x,retained_sections=retained,selected_only=review.get('selected_only',True)) for x in review.get('losses',review.get('loss_events',[]))]
    lines=[]
    for conflict in review.get('conflicts',[]):
        if conflict.get('code')=='edited_rest_rig_conflicts_with_preserved_bone_matrices':
            lines += ['The scene rest rig changed, but Preserve Bone Matrices would keep the original rig data. Choose whether to export the edited scene rest rig, or cancel and restore the original rig.', 'Exporting the edited rig may require exporting without corrective shapes. Review the updated loss list before exporting.']
        else: lines.append('Unresolved setting conflict: '+str(conflict.get('code','unknown')))
    for group in review.get('duplicate_groups',[]):
        lines += ['These objects claim the same original corrective shapes: '+', '.join(str(x.get('object',x.get('name',''))) if isinstance(x,dict) else str(x) for x in group.get('claimants',group.get('candidates',group.get('objects',[]))))+'.', 'Choose at most one compatible copy to retain them. Other copies need approval to export without those shapes.', 'Keep source correctives on: a named eligible object, or None.']
    if review.get('dropped_source_lods'): lines.append('Lower source LODs will not be exported because their compatibility has not been verified.')
    if not lines and not cards: lines.append('No shape-key loss approval is required by this plan.' if review.get('status')=='ready' else 'Review the export details before continuing.')
    unknown=[x for c in cards for x in c['unmapped_codes']]
    return _model('Review export',lines,cards=cards,details=_public(review),unmapped_codes=unknown,buttons={'cancel':CANCEL_EXPORT,'export':EXPORT_WITHOUT_BLEND_SHAPES if any(c['buttons']['export']==EXPORT_WITHOUT_BLEND_SHAPES for c in cards) else EXPORT_WITHOUT_KEYS if cards else 'Export'})


def format_result(report):
    """Only claim success after publication; report actual section and LOD scope."""
    if not (report.get('published') is True or report.get('status')=='committed'):
        return _model('Export not confirmed', ['The output has not been confirmed as published. Review Details before using it.'],severity='WARNING',details=_public(report))
    reviewed=report.get('review',{}); actual=dict(reviewed.get('report',{}))
    actual.update(report.get('report',{})); actual.update(report)
    sections=actual.get('output_sections',reviewed.get('output_sections'))
    lines=[]; ids=[]; warnings=list(map(str,_list(actual.get('warnings'))))
    if isinstance(sections,list) and sections:
        retained=sum(x.get('status')=='retained' for x in sections)
        ordinary=sum(x.get('status')=='ordinary_geometry' for x in sections)
        compatible=sum(x.get('status')=='compatible_geometry' for x in sections)
        lines.append(f'Exported: {retained} sections kept verified corrective shapes; {ordinary} sections used Ordinary geometry.'); ids.append(17)
        if compatible: lines.append(str(compatible)+' verified compatible sections had no linked corrective shapes.')
        if retained+ordinary+compatible!=len(sections): warnings.append('Some section preservation results are unavailable. Review Details before using the output.')
    elif actual.get('output_shape_links')==0:
        lines.append('This file has no blend shapes.'); ids.append(18)
    else:
        warnings.append('Export completed, but preservation details are unavailable. Review the output before using it.'); ids.append(21)
    lods=actual.get('output_lods'); dropped=actual.get('dropped_source_lods',[])
    if lods==1: lines.append('Exported LOD0.')
    elif lods is not None: lines.append('Output LODs: '+str(lods)+'.')
    if dropped: warnings.append('Lower source LODs were not exported: '+', '.join(map(str,dropped))+'.')
    losses=actual.get('loss_events',actual.get('losses',reviewed.get('losses',[])))
    if losses: warnings.append('Approved shape-key losses are listed in Details.')
    source_status=str(actual.get('source_status',''))
    if source_status and source_status!='verified' and not source_status.startswith('verified_source_') and source_status not in ('not_applicable','valid'):
        warnings.append('The original source could not be verified. Original corrective shapes may be unavailable to identify.')
    new_materials=actual.get('new_materials',actual.get('added_materials',[]))
    if new_materials: warnings.append('New materials need matching MDF entries: '+', '.join(map(str,_list(new_materials)))+'.')
    if warnings: lines.append('Export completed with warnings. Review them before using the file.'); lines.extend(dict.fromkeys(warnings))
    return _model('Export completed',lines,details=_public(report),card_ids=ids,severity='WARNING' if warnings else 'INFO')


def format_details(report):
    lines=['Status: '+str(report.get('status','unverified')),'Destination: '+str(report.get('destination',report.get('output_path','Not provided')))]
    vm=view_model(report)
    completed=report.get('published') is True or report.get('status')=='committed'
    if completed: lines += format_result(report)['lines']
    else: lines += vm['lines']
    for card in vm['cards']: lines += ['', 'Reviewed shape-key loss' if completed else card['title']]+card['lines']
    for section in report.get('output_sections',[]):
        lines.append('Section: '+str(section.get('object',''))+' / '+str(section.get('material',''))+'; LOD '+str(section.get('lod','?'))+'; '+str(section.get('status','unverified')))
        if section.get('shape_names'): lines.append('Retained shapes: '+', '.join(map(str,section['shape_names'])))
    lines += ['', 'Technical details',json.dumps(_public(report),ensure_ascii=False,indent=2,default=str)]
    return lines


def format_batch(report):
    if report.get('status')=='cancelled': return format_cancel(before_publication=report.get('publication_started') is False)
    files=report.get('files',[]); total=len(files)
    if not total: return _model('No files enabled',['No files are enabled. Enable at least one batch entry.'],severity='ERROR',card_ids=[35])
    succeeded=sum(x.get('status')=='committed' or x.get('published') is True for x in files)
    failed=sum(x.get('status') in ('failed','error','cancelled','not_committed','blocked') for x in files)
    lines=[f'Completed: {succeeded}/{total} files; {failed} failed.']
    if succeeded+failed<total: lines.append(str(total-succeeded-failed)+' files are pending, skipped or have an unconfirmed outcome.')
    first=next((x for x in files if x.get('status') in ('failed','error','cancelled','not_committed','blocked')),None)
    if first: lines.append('First failure: '+str(first.get('file',first.get('path','Unknown file')))+': '+' '.join(format_error(first.get('error',first.get('error_detail',first.get('result',{}).get('error_detail',first.get('result',{}).get('reason','See Details')))))['lines'])+'. Review failed files and retry them.')
    for item in files:
        for warning in _list(item.get('warnings',item.get('result',{}).get('warnings'))):
            lines.append(str(item.get('file',item.get('path','File')))+': '+str(warning))
    return _model('Batch export results',lines,severity='ERROR' if failed and not succeeded else 'WARNING' if succeeded<total or any(_list(item.get('warnings',item.get('result',{}).get('warnings'))) for item in files) else 'INFO',card_ids=[35],details=_public(report))


def format_folder(*, existing_mods=None, outcome=None, before_publication=False, rollback_confirmed=False, mesh_export=True):
    lines=[]
    if existing_mods: lines.append('Folder already contains '+', '.join(map(str,_list(existing_mods)))+'. Exporting adds or replaces this mesh.')
    if outcome=='failed': lines.append('Mesh export failed. The mod folder was not updated.' if before_publication or rollback_confirmed else 'Mod-folder export failed. Some files may have changed. Review recovery details before retrying.')
    elif outcome=='committed': lines.append('Mod folder updated. Review the mesh export details before using it.')
    elif outcome=='recovery_required': lines.append('Mod-folder recovery is required. Some files may have changed. Keep the recovery files and follow the recovery details before retrying.')
    if not mesh_export:
        lines=[line.replace('Mesh export failed.', 'Mod-menu export failed.').replace('Mod-folder export failed.', 'Mod-menu export failed.').replace('Mod folder updated. Review the mesh export details before using it.', 'Mod menu updated.') for line in lines]
    return _model('Mod-folder export' if mesh_export else 'Mod-menu export',lines,severity='ERROR' if outcome in ('failed','recovery_required') else 'WARNING' if existing_mods else 'INFO',card_ids=[36])

# Judgment additions for the unified backend and batch routes; preserve codes.
REASONS.update({
 'Corrective edit extends outside source-authored ranges': (27, 'A corrective shape moves vertices outside its supported source range. This section must use Ordinary geometry without live shape keys.'),
 'ordinary_rig': (None, 'The scene rest rig changed. Original corrective compatibility cannot be verified.'),
 'ordinary_cached_rig': (None, 'Stored bone-matrix data changed. Original corrective compatibility cannot be verified.'),
 'ordinary_axis': (None, 'Axis conversion changed from the source import. Original corrective compatibility cannot be verified.'),
})
for _new,_old in {'UNIFIED_ZERO_SKIN_WEIGHTS':'PROTOTYPE_ZERO_SKIN_WEIGHTS','UNIFIED_OUTPUT_DIRECTORY_MISSING':'PROTOTYPE_OUTPUT_DIRECTORY_MISSING','UNIFIED_EMPTY_OUTPUT':'PROTOTYPE_EMPTY_ORDINARY_OUTPUT','UNIFIED_INVALID_POSITION':'PROTOTYPE_INVALID_POSITION','UNIFIED_INVALID_FACE_REFERENCE':'PROTOTYPE_INVALID_FACE_REFERENCE','UNIFIED_INVALID_MATERIAL_REFERENCE':'PROTOTYPE_INVALID_MATERIAL_REFERENCE','UNIFIED_INVALID_VERTEX_STREAM':'PROTOTYPE_INVALID_VERTEX_STREAM','UNIFIED_UNSUPPORTED_SKIN_LAYOUT':'PROTOTYPE_UNSUPPORTED_SKIN_WEIGHT_LAYOUT','UNIFIED_STALE_PLAN':'PROTOTYPE_EXPORT_PLAN_CHANGED','UNIFIED_PLAN_CHANGED':'PROTOTYPE_EXPORT_PLAN_CHANGED','UNIFIED_LOSS_APPROVAL_CHANGED':'PROTOTYPE_EXPORT_PLAN_CHANGED'}.items():
    ERRORS[_new]=ERRORS[_old]
ERRORS.update({
 'UNIFIED_SF6_ONLY':(23,'Use an SF6 .mesh.230110883 filename for this export workflow.'),
 'UNIFIED_COLLECTION_MISSING':(29,'Choose an export mesh collection, then export again.'),
 'UNIFIED_BACKEND_FAILED':(16,'Mesh export failed. Review the specific mesh errors in Details, correct them, then retry.'),
 'UNIFIED_SETTINGS_CONFLICT':(None,'Review the conflicting export settings before exporting. The edited scene rest rig and preserved bone matrices require an explicit choice.'),
 'UNIFIED_INVALID_LOD_POLICY':(None,'Choose a supported LOD export option, then review the export again.'),
 'UNIFIED_INVALID_CORRECTIVE_POLICY':(None,'Choose whether to preserve compatible source data or use Ordinary export, then review again.'),
 'UNIFIED_INVALID_RIG_POLICY':(None,'Choose a supported rest-rig option, then review the export again.'),
 'BATCH_INVALID_EXPORT_PATH':(None,'Choose a valid output path for this batch entry.'),
 'BATCH_DUPLICATE_DESTINATION':(None,'Multiple batch entries target the same file. Give each entry a distinct output path.'),
 'BATCH_EXPORT_TARGET_CHANGED':(12,'A batch export destination changed after review. Review the updated batch before exporting.'),
 'BATCH_OUTPUT_DIRECTORY_UNAVAILABLE':(None,'Choose an existing, available output directory for this batch entry.'),
 'BATCH_CHAIN_EDITOR_UNAVAILABLE':(None,'This batch entry requires the RE Chain Editor. Make it available before retrying the entry.'),
 'BATCH_UNSUPPORTED_FORMAT':(None,'Choose a supported export format for this batch entry.'),
})
for _code in ('UNIFIED_INVALID_SKELETON_HEADER','UNIFIED_EMPTY_SKELETON','UNIFIED_INVALID_BONE_MATRIX_RANGE','UNIFIED_INVALID_BONE_MATRIX','UNIFIED_EXACT_SOURCE_MISMATCH','UNIFIED_UNEXPECTED_CORRECTIVES','UNIFIED_PREVIEW_CONTEXT_CANNOT_PUBLISH','UNIFIED_NESTED_PREVIEW'):
    ERRORS[_code]=(None,'Generated mesh data or export state failed validation. Save a copy of the project and include the technical error in a bug report.')

ERRORS['UNIFIED_REQUEST_CHANGED']=ERRORS['PROTOTYPE_EXPORT_PLAN_CHANGED']

LOD_POLICY_ITEMS = (
    ('retain_if_compatible','Keep Compatible Source LODs','Keep source LODs only when their compatibility is verified; otherwise review the LOD0 export'),
    ('lod0','LOD0 Only','Export only LOD0; do not include lower source LODs'),
)
for _code in ('missing_normal_recalculation_table','normal_recalculation_range_out_of_bounds','invalid_normal_recalculation_table_structure'):
    _text='The source is verified, but its normal-recalculation data cannot be preserved. This export must use Ordinary geometry without blend shapes.'
    REASONS[_code]=(3,_text)
    REASONS['Ordinary fallback: '+_code]=(3,_text)
REASONS.update({
 'source_unavailable':(3,'The stored original mesh is unavailable'),
 'source_hash_mismatch':(3,'The stored original mesh failed its checksum check'),
 'source_unreadable':(3,'The stored original mesh cannot be read'),
})

# Legacy backend strings remain untouched: these are presentation-only matches.
LEGACY_ERRORS = (
 ('Missing embedded SF6 source',24,'Source-preserving export stopped: the stored original mesh is missing. Re-import the original mesh into a new collection and transfer your edits carefully.'),
 ('Embedded SF6 source hash mismatch',24,'Source-preserving export stopped: the stored original mesh failed its checksum check. Re-import the original mesh into a new collection and transfer your edits carefully.'),
 ('UV edits are unsupported in source mode',25,'UV data changed. Restore the original UV data for source-preserving export, or use the compatible-source workflow and review its preservation limits.'),
 ('UV layers were added or removed',25,'UV layers changed. Restore the original UV layers for source-preserving export, or use the compatible-source workflow and review its preservation limits.'),
 ('Weight edits are unsupported in source mode',25,'Bone weights changed. Restore the original weights for source-preserving export, or use the compatible-source workflow and review its preservation limits.'),
 ('Added/duplicated vertices are unsupported in source mode',25,'Vertices were added or duplicated. Restore the original topology for source-preserving export, or use the compatible-source workflow and review its preservation limits.'),
 ('Added/duplicated faces are unsupported in source mode',25,'Faces were added or duplicated. Restore the original topology for source-preserving export, or use the compatible-source workflow and review its preservation limits.'),
 ('Rewired topology is unsupported in source mode',25,'Face connections changed. Restore the original topology for source-preserving export, or use the compatible-source workflow and review its preservation limits.'),
 ('Apply position edits in Edit Mode; object transforms are unsupported',26,'Restore the original object transform and make position edits in Edit Mode.'),
 ('Use the same axis conversion setting as the source import',26,'Match export axis conversion to the setting used for the source import.'),
 ('Shape keys were added/renamed/removed',27,'Restore the original shape-key names and set of keys for source-preserving export, or review the compatible-source workflow’s shape-key losses.'),
 ('Source shapes must be relative to Basis, without vertex-group masks',27,'Use relative keys based on Basis, with no vertex-group masks.'),
 ('Shape edit extends outside its authored range',27,'This shape key moves vertices outside its supported source range. Restore those vertices or reduce the edit.'),
 ('No selected source mesh objects',29,'Select meshes in the target collection or turn off Selected Objects Only.'),
 ('Non-finite vertex positions',33,'Vertex coordinates are invalid. Review the affected object and its modifiers, then export again.'),
 ('Invalid shape coordinates',33,'Shape-key coordinates are invalid. Review the affected shape key, then export again.'),
)


def format_legacy_error(error):
    """Return None for unknown errors, preserving the exact original in Details."""
    raw=str(error); matched=next(((card,text) for prefix,card,text in LEGACY_ERRORS if raw.startswith(prefix)),None)
    if matched:
        card,text=matched
    elif raw.startswith('SF6 subset:') and any(x in raw for x in ('not supported','unsupported','unknown')):
        card=28; text='Selected-part source export does not support this source layout. Try the full source collection if appropriate, or use another export mode and review its preservation limits.'
    elif 'Multires' in raw and any(x in raw.lower() for x in ('invalid','truncated','cannot read','missing','do not match')):
        card=32; text='Restore a valid Multires displacement file or pack valid displacement data before exporting. Packing a damaged file does not repair it.'
    elif 'Data Transfer' in raw and 'separate mesh source' in raw:
        card=32; text='Choose a separate mesh as the Data Transfer normal source.'
    elif 'Data Transfer' in raw and 'source' in raw and any(x in raw for x in ('unavailable','no faces')):
        card=32; text='Choose an available mesh with faces as the Data Transfer normal source.'
    elif 'Data Transfer' in raw and any(x in raw for x in ('mapping','corner counts')):
        card=32; text='Use spatial face mapping or matching evaluated topology for the Data Transfer modifier.'
    elif raw.startswith('SF6 hybrid evaluation:'):
        card=33; text='Modifier evaluation could not preserve valid geometry or shape-key data. Review the specific operation in Details. For an internal isolation failure, save a copy of the project and report this error with your Blender and add-on versions.'
    else: return None
    return _model('Export stopped',[text,'Affected data: '+raw],severity='ERROR',details={'technical_error':raw},card_ids=[card])


def format_ordinary_errors(errors):
    """Readable actual error records from the Ordinary exporter, no inferred fixes."""
    result=[]
    for error in errors:
        code=error.get('code','unknown'); objects=', '.join(map(str,error.get('objects',[])))
        line=code+(': '+objects if objects else '')
        if code=='MaxWeightsPerVertexExceeded': line+='; primary weight limit: '+str(error.get('primary_weight_limit','unavailable'))
        if code=='ExtendedMaxWeightsPerVertexExceeded': line+='; extended weight limit: '+str(error.get('extended_weight_limit','unavailable'))
        if code=='MaxFacesExceeded': line+='; face limit: '+str(error.get('face_limit','unavailable'))
        result.append(line)
    return result
