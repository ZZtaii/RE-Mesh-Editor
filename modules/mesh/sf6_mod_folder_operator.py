"""Blender UI for SF6 mod folders, shared bundles, and dummy add-on menus."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path
import textwrap

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import sf6_mod_folder as package
from .sf6_hybrid_report import report_hybrid_export


SCENE_DEFAULTS_PENDING = '_SF6ModFolderDefaultsPending'
SCENE_UNIFIED_RESULT = 'SF6ModFolderUnifiedResultV1'
COLLECTION_UNIFIED_REPORT = 'SF6ModFolderUnifiedReportV1'
EXPORT_PROFILE_FIELDS = ('exportBlendShapes', 'selectedOnly', 'sf6HybridPreserve',
                         'targetCollection', 'rotate90') + package.UNIFIED_DEFAULT_FIELDS + package.PROFILE_EVIDENCE_FIELDS + package.PROCESSING_DEFAULT_FIELDS


def _property_is_set(operator, name):
    properties = getattr(operator, 'properties', None)
    return bool(properties is not None and properties.is_property_set(name))


def _unified_requested(operator, profile):
    return (bool(getattr(operator, 'sf6ReviewOnly', False)) or
            bool(getattr(operator, 'sf6UnifiedOptIn', False)) or
            bool(getattr(operator, 'sf6ExportWorkflow', '')) or
            bool(getattr(operator, 'sf6LodPolicy', '')) or
            any(key in profile for key in package.UNIFIED_DEFAULT_FIELDS + package.PROCESSING_DEFAULT_FIELDS) or
            any(_property_is_set(operator, key) for key in package.PROCESSING_DEFAULT_FIELDS))



def _has_saved_profile(context, profile):
    # Even an empty/partial saved profile represents prior choices. A damaged
    # existing config is also not evidence of a genuinely new user profile.
    return bool(profile) or 'SF6ModFolderDefaults' in context.scene or settings_path().exists()


def _explicit_processing_fields(operator):
    loaded = getattr(operator, '_sf6FolderProcessingSnapshot', None)
    original = getattr(operator, '_sf6FolderExplicitProcessing', ())
    if loaded is None:
        return {key for key in package.PROCESSING_DEFAULT_FIELDS if _property_is_set(operator, key)}
    return set(original) | {key for key, value in loaded.items()
                            if getattr(operator, key) != value}


def apply_folder_processing(operator, context, profile, options, *, enable_unified):
    from .sf6_unified_ui import apply_new_profile_defaults, SettingsReviewRequired
    runtime = dict(options)
    explicit = _explicit_processing_fields(operator)
    for key in package.PROCESSING_DEFAULT_FIELDS:
        if key in explicit:
            runtime[key] = getattr(operator, key)
            continue
        if key in profile:
            if type(profile[key]) is not bool:
                raise SettingsReviewRequired(dict(schema=1, scope='mod_folder',
                    status='review_required', needs_review=True,
                    reason_codes=['invalid_processing_setting'], setting=key))
            runtime[key] = profile[key]
    has_saved = getattr(operator, '_sf6FolderHasSavedProfile', None)
    if has_saved is None:
        has_saved = _has_saved_profile(context, profile)
    return apply_new_profile_defaults(runtime, has_saved_profile=has_saved,
        explicit=explicit, enable_unified=enable_unified)


def resolve_folder_options(operator, profile, options):
    """Resolve the original folder profile before materializing defaults.

    New explicit opt-in must not masquerade as an already-unified profile:
    legacy Hybrid keeps LOD0, and ambiguous selected-only scope needs review.
    """
    from .sf6_unified_ui import (resolve_profile, resolve_options,
                                 SettingsReviewRequired)
    raw = dict(profile)
    for key in EXPORT_PROFILE_FIELDS:
        if key not in package.UNIFIED_DEFAULT_FIELDS + package.PROFILE_EVIDENCE_FIELDS + package.PROCESSING_DEFAULT_FIELDS and _property_is_set(operator, key):
            raw[key] = getattr(operator, key)
    workflow = getattr(operator, 'sf6ExportWorkflow', '')
    enable = bool(getattr(operator, 'sf6UnifiedOptIn', False))
    if 'sf6ExportWorkflow' not in raw and workflow:
        if workflow == 'unified_v1':
            enable = True
        else:
            raw['sf6ExportWorkflow'] = workflow
    policy = getattr(operator, 'sf6LodPolicy', '')
    if policy:
        raw['sf6LodPolicy'] = policy
    result = resolve_profile(raw, mesh_version=230110883, scope='mod_folder',
                             enable_unified=enable)
    if result.needs_review:
        raise SettingsReviewRequired(dict(schema=1, scope='mod_folder',
            mode=result.mode, legacy_mode=result.legacy_mode,
            reason_codes=list(result.reason_codes), needs_review=True))
    runtime = dict(options)
    # An opted-in EXEC call also respects the stored preservation choice. It
    # cannot turn a saved Ordinary profile on just by adding a workflow flag.
    for key in ('exportBlendShapes', 'selectedOnly', 'sf6HybridPreserve', 'rotate90'):
        if key in result.settings:
            runtime[key] = result.settings[key]
    for key in package.UNIFIED_DEFAULT_FIELDS:
        runtime.pop(key, None)
        if key in result.settings:
            runtime[key] = result.settings[key]
    if result.mode == 'ordinary':
        # Ordinary strips its workflow marker on commit, so its generic LOD
        # scope must survive independently. A current visible choice wins.
        if 'sf6LodPolicy' in result.settings:
            runtime['exportAllLODs'] = result.settings['sf6LodPolicy'] != 'lod0'
        elif 'exportAllLODs' in result.settings:
            if type(result.settings['exportAllLODs']) is not bool:
                raise SettingsReviewRequired(dict(schema=1, scope='mod_folder',
                    status='review_required', needs_review=True,
                    reason_codes=['invalid_ordinary_lod_scope']))
            runtime['exportAllLODs'] = result.settings['exportAllLODs']
    resolved = resolve_options(runtime, mesh_version=230110883, scope='mod_folder',
        enable_unified=enable or raw.get('sf6ExportWorkflow') == 'unified_v1')
    resolved['_sf6UnifiedSettingsReport'] = dict(schema=1, scope='mod_folder',
        mode=result.mode, legacy_mode=result.legacy_mode,
        reason_codes=list(result.reason_codes), needs_review=False,
        runtime_unified_wrapper=resolved.get('_sf6UnifiedSettingsReport', {}).get('runtime_unified_wrapper', False))
    return resolved


def _preserve_profile(values, previous):
    for key in EXPORT_PROFILE_FIELDS:
        values.pop(key, None)
        if key in previous:
            values[key] = previous[key]


def _record_unified_result(operator, context, options, error=None, *, committed=False, review_only=False):
    from .sf6_unified_ui import record_result
    options['_sf6SettingsScope'] = 'mod_folder'
    report = dict(options.get('_sf6UnifiedReport', {}), scope='mod_folder',
                  folder_committed=committed)
    if review_only:
        report['status'] = 'planned'
        report['review_only'] = True
    elif committed:
        report['status'] = 'committed'
    elif report.get('status') in ('committed', 'validated'):
        report['status'] = 'not_committed'
        report.setdefault('reason', 'folder_publication_failed' if error else 'not_committed')
    elif report.get('status') == 'planned':
        report['status'] = 'not_committed'
    options['_sf6UnifiedReport'] = report
    report = record_result(operator, options, error=error)
    encoded = json.dumps(report, sort_keys=True)
    operator.sf6UnifiedResult = encoded
    context.scene[SCENE_UNIFIED_RESULT] = encoded
    return encoded


def _restore_export_markers(scene, markers):
    for key, value in markers.items():
        if value is None:
            if key in scene:
                del scene[key]
        else:
            scene[key] = value



def settings_path():
    return Path(bpy.utils.user_resource('CONFIG')) / 're_mesh_editor' / 'sf6_mod_folder_export.json'


def choose_collection(context):
    active = context.active_object
    if active:
        for collection in bpy.data.collections:
            if active.name in collection.all_objects:
                try:
                    package.asset_from_collection(collection)
                    return collection
                except ValueError:
                    pass
    for key in ('REMeshLastImportedCollection', 'REMeshLastExportedCollection'):
        collection = bpy.data.collections.get(context.scene.get(key, ''))
        if collection:
            return collection
    return next((c for c in bpy.data.collections if c.get('SF6PreserveSource')), None)


def existing_mod_defaults(collection, context):
    candidates = [collection.get('BatchExport_path', '')] if collection else []
    candidates.append(context.scene.re_mdf_toolpanel.modDirectory)
    for candidate in candidates:
        parts = Path(bpy.path.abspath(candidate)).parts if candidate else ()
        native_index = next((i for i, part in enumerate(parts) if part.lower() == 'natives'), None)
        if native_index is None:
            continue
        root = Path(*parts[:native_index])
        try:
            info = package.read_modinfo(root / 'modinfo.ini')
        except (OSError, ValueError):
            continue
        preview = root / info.get('screenshot', '')
        if not preview.resolve().is_relative_to(root.resolve()) or not preview.is_file():
            preview = ''
        return dict(parent_directory=str(root.parent), folder_name=root.name,
                    mod_name=info.get('name', root.name), mod_version=info.get('version', 'v1'),
                    mod_author=info.get('author', ''), mod_category=info.get('category', ''),
                    mod_description=info.get('description', ''), preview_path=str(preview),
                    extra_categories='; '.join(info.get('categories', [])[1:]),
                    bundle_name=info.get('nameasbundle', ''), parent_mod_name=info.get('addonfor', ''),
                    create_dummy_parent=False)
    return {}


def load_operator_defaults(context, collection):
    values = {}
    if context.scene.get(SCENE_DEFAULTS_PENDING):
        try:
            values = json.loads(context.scene.get('SF6ModFolderDefaults', '{}'))
        except (ValueError, TypeError):
            pass
    if not isinstance(values, dict):
        values = {}
    if not values:
        values = package.load_defaults(settings_path())
    if not values:
        try:
            values = json.loads(context.scene.get('SF6ModFolderDefaults', '{}'))
        except (ValueError, TypeError):
            values = {}
    if not isinstance(values, dict):
        values = {}
    if not values:
        values = existing_mod_defaults(collection, context)
    return package.filter_defaults(values)


def remember_operator_defaults(operator, context, values):
    """Keep dialog choices independently of whether the mod export succeeds."""
    context.scene['SF6ModFolderDefaults'] = json.dumps(values)
    try:
        package.save_defaults(settings_path(), values)
    except OSError as error:
        context.scene[SCENE_DEFAULTS_PENDING] = True
        operator.report({'WARNING'}, 'Mod options could not be saved: ' + str(error))
    else:
        if SCENE_DEFAULTS_PENDING in context.scene:
            del context.scene[SCENE_DEFAULTS_PENDING]


def update_destination_notice(self, context):
    self.destination_notice = ''
    if self.export_content != 'MESH' or not self.parent_directory.strip() or not self.folder_name:
        return
    collection = bpy.data.collections.get(self.targetCollection)
    if collection is None:
        return
    try:
        asset = package.asset_from_collection(collection)
        package.validate_folder_name(self.folder_name)
    except ValueError:
        return
    try:
        parent = bpy.path.abspath(self.parent_directory)
        # Include the current character/costume too: replacing an earlier
        # variant is still an existing-content update, not a new empty folder.
        contents = package.destination_conflicts(parent, self.folder_name, package.SF6Asset('', '', ''))
        root = Path(parent) / self.folder_name
        names = [f'{package.CHARACTERS.get(character, "esf" + character)} C{int(costume)}'
                 for character, costume in contents]
        if not names and (root / 'modinfo.ini').is_file():
            info = package.read_modinfo(root / 'modinfo.ini')
            names = [info.get('name') or 'existing mod information']
        if not names and root.is_dir() and any(root.iterdir()):
            names = ['existing files']
    except (OSError, ValueError):
        self.destination_notice = 'Existing files could not be checked.\nCheck the destination before exporting.'
        return
    if names:
        from .sf6_unified_messages import format_folder
        self.destination_notice = '\n'.join(format_folder(existing_mods=names)['lines'])


def update_collection(self, context):
    update_destination_notice(self, context)
    collection = bpy.data.collections.get(self.targetCollection)
    if collection is None:
        return
    try:
        asset = package.asset_from_collection(collection)
    except ValueError:
        return
    previous_category = '!Characters > ' + package.CHARACTERS.get(self.last_character, 'esf' + self.last_character)
    known_categories = {'!Characters > ' + name for name in package.CHARACTERS.values()}
    if (not self.mod_category or self.mod_category == previous_category or
            (not self.last_character and self.mod_category in known_categories)):
        self.mod_category = asset.category
    self.last_character = asset.character
    self.rotate90 = collection.get('SF6SourceRotate', True)


def destination_preview(self):
    try:
        if self.export_content == 'MENU':
            if not self.parent_directory or not self.folder_name:
                return ''
            package.validate_folder_name(self.folder_name)
            return str(Path(bpy.path.abspath(self.parent_directory)) / self.folder_name / 'modinfo.ini')
        collection = bpy.data.collections.get(self.targetCollection)
        asset = package.asset_from_collection(collection) if collection else None
        if not asset or not self.parent_directory or not self.folder_name:
            return ''
        package.validate_folder_name(self.folder_name)
        return str(Path(bpy.path.abspath(self.parent_directory)) / self.folder_name / asset.relative_path)
    except ValueError:
        return ''


def parent_destination_preview(self):
    if not self.parent_directory or not self.parent_mod_name:
        return ''
    try:
        folder = package.dummy_folder_name(self.parent_mod_name, self.parent_folder_name)
        return str(Path(bpy.path.abspath(self.parent_directory)) / folder / 'modinfo.ini')
    except ValueError:
        return ''


VISIBLE_TRANSIENT_KEYS = ('_sf6UnifiedExpectedPlan', '_sf6UnifiedExpectedOutputSHA256',
                          '_sf6ApprovedCorrectiveLosses', '_sf6HybridReport', '_sf6UnifiedReport')


def _package_arguments(payload):
    folder = payload['folder']
    asset = package.parse_asset_name(folder['asset_filename']) if folder['asset_filename'] else None
    return (folder['parent_directory'], folder['folder_name'], asset,
            folder['metadata'], folder['preview_path']), dict(
        create_dummy_parent=folder['create_dummy_parent'],
        parent_folder_name=folder['parent_folder_name'], parent_categories=folder['parent_categories'])


def _check_package_snapshot(payload):
    arguments, keywords = _package_arguments(payload)
    planned = package.plan_mod_folder(*arguments, **keywords)
    if planned['snapshot'] != payload['package_snapshot']:
        raise ValueError('UNIFIED_REQUEST_CHANGED')
    return planned


def build_folder_payload(operator, context):
    """Capture one exact invocation, with no persistence or output writes."""
    from .sf6_unified_ui import apply_review_inputs
    collection = bpy.data.collections.get(operator.targetCollection)
    mesh_export = operator.export_content == 'MESH'
    if mesh_export and collection is None:
        raise ValueError('Select the mesh collection to export.')
    previous = getattr(operator, '_sf6FolderProfile', None)
    previous = dict(load_operator_defaults(context, collection) if previous is None else previous)
    values = {key: getattr(operator, key) for key in package.DEFAULT_FIELDS}
    for key in package.UNIFIED_DEFAULT_FIELDS + package.PROFILE_EVIDENCE_FIELDS + package.PROCESSING_DEFAULT_FIELDS:
        values.pop(key, None)
        if key in previous:
            values[key] = previous[key]
    if not mesh_export:
        _preserve_profile(values, previous)
    asset = package.asset_from_collection(collection) if mesh_export else None
    if not operator.parent_directory.strip():
        raise ValueError('Choose an existing Parent Directory; the mod folder will be created inside it.')
    parent = str(Path(bpy.path.abspath(operator.parent_directory)).expanduser().resolve())
    preview = str(Path(bpy.path.abspath(operator.preview_path)).expanduser().resolve()) if operator.preview_path else ''
    category = operator.mod_category or (asset.category if asset else '')
    metadata = dict(name=operator.mod_name, version=operator.mod_version, author=operator.mod_author,
        category=package.categories_from_text(category) + package.categories_from_text(operator.extra_categories),
        description=operator.mod_description, nameasbundle=operator.bundle_name,
        addonfor=operator.parent_mod_name, dummymod='' if mesh_export else 'True')
    folder = dict(parent_directory=parent, folder_name=operator.folder_name,
        asset_filename=asset.filename if asset else '', metadata=metadata, preview_path=preview,
        create_dummy_parent=operator.create_dummy_parent and bool(operator.parent_mod_name),
        parent_folder_name=operator.parent_folder_name, parent_categories=operator.parent_categories)
    plan = package.plan_mod_folder(parent, operator.folder_name, asset, metadata, preview,
        create_dummy_parent=folder['create_dummy_parent'], parent_folder_name=folder['parent_folder_name'],
        parent_categories=folder['parent_categories'])
    options = {}
    if mesh_export:
        preferences = context.preferences.addons[__package__.split('.')[0]].preferences
        options = dict(targetCollection=collection.name, selectedOnly=operator.selectedOnly,
            exportBlendShapes=operator.exportBlendShapes, sf6HybridPreserve=operator.sf6HybridPreserve,
            rotate90=operator.rotate90)
        for name in ('exportAllLODs', 'autoSolveRepeatedUVs', 'preserveSharpEdges',
                     'useBlenderMaterialName', 'preserveBoneMatrices', 'exportBoundingBoxes'):
            options[name] = getattr(preferences, 'default_' + name)
        options.update(limitTotal=getattr(preferences, 'default_limitTotal', False), limitTotalCount=6,
                       normalizeWeights=getattr(preferences, 'default_normalizeWeights', True))
        options = resolve_folder_options(operator, previous, options)
        options = apply_folder_processing(operator, context, previous, options,
            enable_unified=options.get('sf6ExportWorkflow') == 'unified_v1')
        # Visible review never imports grants from a previous operator, report,
        # preset or redo. Only the active controller can grant exact losses.
        options = apply_review_inputs(SimpleNamespace(sf6RigPolicy=operator.sf6RigPolicy), options)
        for key in VISIBLE_TRANSIENT_KEYS:
            options.pop(key, None)
        if options.get('sf6ExportWorkflow') != 'unified_v1':
            raise ValueError('UNIFIED_SETTINGS_REVIEW_REQUIRED')
    values.update(parent_directory=parent, preview_path=preview, mod_category=category,
                  last_character=asset.character if asset else operator.last_character)
    path = plan['destination'] / (asset.relative_path if asset else 'modinfo.ini')
    return dict(schema=1, path=str(path), options=options, folder=folder, values=values,
                mesh_export=mesh_export, package_snapshot=plan['snapshot'],
                source_filename=asset.filename if asset else '',
                scene_pointer=context.scene.as_pointer())


def prepare_folder_review(payload, overrides):
    from .sf6_review_plan import prepare_review
    from .blender_re_mesh import exportREMeshFile
    plan = _check_package_snapshot(payload)
    if not payload['mesh_export']:
        return dict(schema=1, status='ready', complete=True, destination=payload['path'],
            plan_token=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            losses=[], approved_loss_tokens=[], requires_loss_approval=False,
            report=dict(route='menu_only', status='planned', publication_started=False))
    options = deepcopy(payload['options'])
    for key in VISIBLE_TRANSIENT_KEYS:
        options.pop(key, None)
    options.update({key: deepcopy(value) for key, value in overrides.items()
                    if key not in VISIBLE_TRANSIENT_KEYS})
    review = prepare_review(payload['path'], options, exportREMeshFile,
                            staging_directory=str(plan['parent']))
    review['selected_only'] = bool(options.get('selectedOnly'))
    review['scope'] = 'mod_folder'
    review['dropped_source_lods'] = review.get('report', {}).get('dropped_source_lods', [])
    return review


def _commit_folder_profile(payload, options, report):
    """Commit success markers and safe defaults only after the package commits."""
    from .sf6_unified_ui import commit_operator_settings
    context = bpy.context
    values = deepcopy(payload['values'])
    if payload['mesh_export']:
        collection = bpy.data.collections.get(options['targetCollection'])
        context.scene['REMeshLastExportedCollection'] = options['targetCollection']
        context.scene['REMeshLastExportedMeshVersion'] = 230110883
        if collection is not None:
            collection['BatchExport_path'] = payload['path']
            for key, value in options.items():
                if (key not in ('targetCollection', 'selectedOnly', 'exportBlendShapes', 'sf6HybridPreserve',
                                'sf6ExportWorkflow', 'sf6LodPolicy', 'sf6RigPolicy') and not key.startswith('_')):
                    collection['BatchExport_' + key] = value
        context.scene.re_mdf_toolpanel.modDirectory = str(
            Path(payload['folder']['parent_directory']) / payload['folder']['folder_name'] / 'natives' / 'stm')
        proxy = SimpleNamespace(**{key: values.get(key, '') for key in package.DEFAULT_FIELDS})
        committed = commit_operator_settings(proxy, options)
        for key in ('exportBlendShapes', 'selectedOnly', 'sf6HybridPreserve', 'rotate90'):
            values[key] = options[key]
        for key in package.UNIFIED_DEFAULT_FIELDS + package.PROCESSING_DEFAULT_FIELDS:
            values.pop(key, None)
        values.update(committed)
        values.update({key: options[key] for key in package.PROCESSING_DEFAULT_FIELDS if key in options})
        if options.get('_sf6UnifiedSettingsReport', {}).get('mode') == 'ordinary':
            values['exportAllLODs'] = options.get('sf6LodPolicy') != 'lod0'
    warnings = []
    sink = SimpleNamespace(report=lambda level, message: warnings.append(message))
    remember_operator_defaults(sink, context, values)
    report.setdefault('warnings', []).extend(warnings)


def publish_folder_review(payload, overrides):
    """Publish one fully reviewed folder transaction and return its real outcome."""
    from .sf6_unified_export import execute_unified, plan_export
    from .blender_re_mesh import exportREMeshFile
    options = deepcopy(payload['options'])
    options.update(deepcopy(overrides))
    transaction = dict(publication_started=False, folder_unchanged=True,
                       rollback_verified=False, publication_outcome='not_started')
    scene = bpy.context.scene
    markers = {key: scene.get(key) for key in
               ('REMeshLastExportedCollection', 'REMeshLastExportedMeshVersion')}
    try:
        _check_package_snapshot(payload)
        if scene.as_pointer() != payload['scene_pointer']:
            raise ValueError('UNIFIED_REQUEST_CHANGED')
        expected = options.get('_sf6UnifiedExpectedPlan')
        if payload['mesh_export']:
            if not expected or plan_export(payload['path'], options)['plan_token'] != expected:
                raise ValueError('UNIFIED_STALE_PLAN')
            if not options.get('_sf6UnifiedExpectedOutputSHA256'):
                raise ValueError('UNIFIED_REVIEWED_OUTPUT_CHANGED')
        elif expected != prepare_folder_review(payload, {})['plan_token']:
            raise ValueError('UNIFIED_STALE_PLAN')

        staged_mesh = {}
        def export_mesh(stage):
            staged_mesh['path'] = stage
            try:
                return execute_unified(stage, options, exportREMeshFile, logical_path=payload['path'])
            finally:
                _restore_export_markers(scene, markers)

        def validate_staged():
            if payload['mesh_export']:
                if plan_export(payload['path'], options)['plan_token'] != expected:
                    raise ValueError('UNIFIED_PLAN_CHANGED')
                actual = package._file_state(staged_mesh['path'])
                if actual.get('sha256') != options['_sf6UnifiedExpectedOutputSHA256']:
                    raise ValueError('UNIFIED_REVIEWED_OUTPUT_CHANGED')

        arguments, keywords = _package_arguments(payload)
        output = package.export_mod_folder(*arguments, export_mesh,
            validate_staged=validate_staged, expected_snapshot=payload['package_snapshot'],
            transaction_report=transaction, **keywords)
        report = deepcopy(options.get('_sf6UnifiedReport', {}))
        report.update(transaction, schema=1, scope='mod_folder', status='committed', published=True,
            folder_committed=True, destination=str(output), mesh_export=payload['mesh_export'],
            target_collection=options.get('targetCollection', ''),
            settings=deepcopy(options.get('_sf6UnifiedSettingsReport', {})))
        if not payload['mesh_export']:
            report['route'] = 'menu_only'
        if transaction.get('cleanup_warning'):
            report.setdefault('warnings', []).append(
                'The mod folder was exported, but temporary export files could not be removed: ' + transaction['cleanup_warning'])
        try:
            _commit_folder_profile(payload, options, report)
        except Exception as error:
            report.setdefault('warnings', []).append(
                'The mod folder was exported, but its saved settings or history could not be updated: ' + str(error))
        return report
    except Exception as error:
        _restore_export_markers(scene, markers)
        report = deepcopy(options.get('_sf6UnifiedReport', {}))
        report.update(transaction, schema=1, scope='mod_folder', status='failed', published=False,
            folder_committed=False, destination=payload['path'], mesh_export=payload['mesh_export'],
            target_collection=options.get('targetCollection', ''),
            error_code=type(error).__name__, error_detail=str(error))
        if hasattr(error, 'report'):
            report['settings'] = deepcopy(error.report)
        return report


def folder_result_model(report):
    from . import sf6_unified_messages as messages
    if report.get('status') == 'cancelled':
        return messages.format_cancel(before_publication=report.get('publication_started') is False,
            folder_unchanged=report.get('publication_started') is False or report.get('folder_unchanged') is True)
    if report.get('status') == 'committed':
        model = messages.format_folder(outcome='committed', mesh_export=report.get('mesh_export', True))
        if report.get('mesh_export', True):
            result = messages.format_result(report)
            model['lines'] += result['lines']
            model['severity'] = result['severity']
        elif report.get('warnings'):
            model['lines'] += list(report['warnings'])
            model['severity'] = 'WARNING'
        model['lines'].append('Destination: ' + report.get('destination', ''))
        return model
    outcome = 'recovery_required' if report.get('publication_outcome') == 'recovery_required' else 'failed'
    model = messages.format_folder(outcome=outcome, mesh_export=report.get('mesh_export', True),
        before_publication=report.get('publication_started') is False,
        rollback_confirmed=report.get('rollback_verified') is True)
    detail = report.get('error_detail', '')
    if detail:
        error = messages.format_error(detail, previous_output_preserved=False)
        if error.get('unmapped_codes') and not detail.startswith(('UNIFIED_', 'PROTOTYPE_')):
            model['lines'].append(detail)
        else:
            model['lines'] += error['lines']
    return model


def finish_folder_review(report):
    from . import sf6_unified_dialogs as dialogs
    report = deepcopy(report)
    report['scope'] = 'mod_folder'
    if report.get('status') == 'cancelled' and report.get('publication_started') is False:
        report.update(folder_unchanged=True, folder_committed=False, published=False)
    safe = dialogs.remember_result(report)
    bpy.context.scene[SCENE_UNIFIED_RESULT] = json.dumps(safe, sort_keys=True)
    if report.get('status') == 'committed' and report.get('target_collection'):
        collection = bpy.data.collections.get(report['target_collection'])
        if collection is not None:
            collection[COLLECTION_UNIFIED_REPORT] = json.dumps(safe, sort_keys=True)
    model = folder_result_model(report)
    dialogs.notify('\n'.join(model['lines']), model['severity'])


def begin_folder_review(context, payload):
    from .sf6_unified_dialogs import begin_review
    # Only immutable captured data is retained. New invocations replace old
    # controller requests; neither a report nor a saved profile grants losses.
    def current_payload():
        fresh = deepcopy(payload)
        if payload['mesh_export']:
            collection = bpy.data.collections.get(payload['options']['targetCollection'])
            fresh['source_filename'] = package.asset_from_collection(collection).filename if collection else ''
        return fresh
    return begin_review(context, operation='mod_folder', payload=payload,
        prepare=prepare_folder_review, publish=publish_folder_review,
        on_result=finish_folder_review, current_payload=current_payload)


def update_lod_choice(self, context):
    self.sf6LodPolicy = self.sf6LodChoice


class ExportSF6ModFolder(bpy.types.Operator):
    bl_idname = 're_mesh.export_sf6_mod_folder'
    bl_label = 'Export SF6 Mod Folder'
    bl_description = 'Export an SF6 mesh mod or create a Fluffy bundle menu, with remembered mod information'

    targetCollection: StringProperty(name='Mesh Collection', update=update_collection)
    export_content: EnumProperty(name='Export Content', default='MESH', update=update_destination_notice, items=(
        ('MESH', 'Mesh Mod', 'Export the selected mesh and its mod information'),
        ('MENU', 'Menu Only', 'Create a DummyMod menu without exporting a mesh; use Parent Mod for nested menus')))
    parent_directory: StringProperty(name='Parent Directory', subtype='DIR_PATH', update=update_destination_notice,
        description='The mod folder will be created inside this directory, such as Fluffy Games/SF6/Mods')
    folder_name: StringProperty(name='Mod Folder Name', update=update_destination_notice)
    mod_name: StringProperty(name='Display Name', description='Name shown in Fluffy Mod Manager')
    mod_version: StringProperty(name='Version', default='v1')
    mod_author: StringProperty(name='Author')
    mod_category: StringProperty(name='Category')
    extra_categories: StringProperty(name='Additional Categories',
        description='Separate categories with semicolons, for example Colours; Hair. Each becomes its own category entry')
    mod_description: StringProperty(name='Description', description=r'Use \n for a line break in Fluffy')
    bundle_name: StringProperty(name='Bundle Name',
        description='Optional shared name (NameAsBundle). Options with the same name appear in one bundle')
    parent_mod_name: StringProperty(name='Parent Mod',
        description='Optional parent display name (AddonFor). Must exactly match the Name of its parent mod or menu')
    create_dummy_parent: BoolProperty(name='Create Parent Menu', default=False,
        description='Create a sibling dummy menu if needed; an existing matching menu is kept unchanged')
    parent_folder_name: StringProperty(name='Parent Menu Folder',
        description='Leave blank to use 00 followed by the parent mod name')
    parent_categories: StringProperty(name='Parent Categories',
        description='Separate with semicolons. Blank uses this option\'s categories; use !Characters > Multiple for a multi-character menu')
    parent_destination: StringProperty(name='Parent Menu Destination', get=parent_destination_preview)
    preview_path: StringProperty(name='Preview Image', subtype='FILE_PATH',
        description='Optional PNG or JPEG copied into the mod folder')
    last_character: StringProperty(options={'HIDDEN'})
    destination: StringProperty(name='Destination', get=destination_preview)
    destination_notice: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    exportBlendShapes: BoolProperty(name='Preserve Compatible Shape Keys', default=True,
        description='Keep verified corrective shapes where compatible; review any required shape-key removal before export')
    selectedOnly: BoolProperty(name='Selected Objects Only', default=False,
        description='Export only selected mesh objects in the chosen mesh collection')
    sf6HybridPreserve: BoolProperty(name='Legacy Hybrid Export', default=False, options={'HIDDEN', 'SKIP_SAVE'},
        description='Rebuild LOD0 geometry/rig, evaluate stable modifiers on the Basis and corrective keys, and retain compatible source shapes. Requires embedded SF6 source; lower LODs are lost')
    rotate90: BoolProperty(name='Convert Z Up to Y Up', default=True)
    # Legacy route flags remain available to explicit API clients only.
    autoSolveRepeatedUVs: BoolProperty(name='Repair Repeated UVs', default=False, options={'SKIP_SAVE'})
    preserveSharpEdges: BoolProperty(name='Split Sharp Edges', default=False, options={'SKIP_SAVE'})
    splitLoopVertices: BoolProperty(name='Split Corner Attributes', default=False, options={'SKIP_SAVE'})
    preserveBoneMatrices: BoolProperty(name='Preserve Bone Matrices', default=True, options={'SKIP_SAVE'})
    sf6UnifiedOptIn: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    sf6LegacyAPI: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    sf6AdvancedOptions: BoolProperty(name='Advanced Export Settings', default=False, options={'SKIP_SAVE'})
    show_bundle_options: BoolProperty(name='Bundles and Add-on Menus', default=False, options={'SKIP_SAVE'},
        description='Group this mod into a bundle or under a parent mod in Fluffy Mod Manager')
    sf6LodChoice: EnumProperty(name='Level of Detail', default='retain_if_compatible', update=update_lod_choice,
        items=(('retain_if_compatible', 'Automatic', 'Keep the lower LODs when the mesh is unchanged; export only LOD0 once it has been edited'),
               ('lod0', 'LOD0 Only', 'Always export only the highest-detail LOD, even for an unchanged mesh')), options={'SKIP_SAVE'})
    sf6ReviewOnly: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    sf6ExportWorkflow: StringProperty(default='', options={'HIDDEN', 'SKIP_SAVE'})
    sf6LodPolicy: StringProperty(default='', options={'HIDDEN', 'SKIP_SAVE'})
    sf6RigPolicy: StringProperty(default='auto', options={'HIDDEN', 'SKIP_SAVE'})
    sf6ApprovedCorrectiveLosses: StringProperty(default='[]', options={'HIDDEN', 'SKIP_SAVE'})
    sf6ExpectedPlan: StringProperty(default='', options={'HIDDEN', 'SKIP_SAVE'})
    sf6UnifiedResult: StringProperty(default='', options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        requested_collection = bpy.data.collections.get(self.targetCollection)
        collection = requested_collection or choose_collection(context)
        explicit = {key for key in package.DEFAULT_FIELDS if _property_is_set(self, key)}
        values = load_operator_defaults(context, collection)
        self._sf6FolderProfile = dict(values)
        self._sf6FolderHasSavedProfile = _has_saved_profile(context, values)
        self._sf6FolderExplicitProcessing = tuple(key for key in package.PROCESSING_DEFAULT_FIELDS
                                                  if _property_is_set(self, key))
        if requested_collection is None:
            collection = bpy.data.collections.get(values.get('targetCollection', '')) or collection
        for key, value in values.items():
            if key not in ('targetCollection', 'rotate90') + package.PROFILE_EVIDENCE_FIELDS and key not in explicit:
                # Keep raw invalid/future values in the snapshot for review,
                # never coerce them into Blender's StringProperties.
                if key in package.UNIFIED_DEFAULT_FIELDS and not isinstance(value, str):
                    continue
                if key in package.PROCESSING_DEFAULT_FIELDS:
                    if type(value) is not bool or key in self._sf6FolderExplicitProcessing:
                        continue
                if key in package.UNIFIED_DEFAULT_FIELDS and _property_is_set(self, key):
                    continue
                setattr(self, key, value)
        if collection:
            self.targetCollection = collection.name
            update_collection(self, context)
            if values.get('targetCollection') == collection.name and 'rotate90' in values:
                self.rotate90 = values['rotate90']
            try:
                asset = package.asset_from_collection(collection)
                suggested = f'{asset.character_name} C{int(asset.costume)} Mod'
                if not self.folder_name:
                    self.folder_name = suggested
                if not self.mod_name:
                    self.mod_name = suggested
            except ValueError:
                pass
        if _unified_requested(self, values):
            preferences = context.preferences.addons[__package__.split('.')[0]].preferences
            try:
                resolved = resolve_folder_options(self, values, dict(
                    exportBlendShapes=self.exportBlendShapes, sf6HybridPreserve=self.sf6HybridPreserve,
                    selectedOnly=self.selectedOnly, rotate90=self.rotate90,
                    exportAllLODs=preferences.default_exportAllLODs))
                self.sf6LodPolicy = resolved.get('sf6LodPolicy', 'retain_if_compatible')
                self.sf6LodChoice = self.sf6LodPolicy
                self.sf6HybridPreserve = False
            except ValueError:
                pass
            preferences = context.preferences.addons[__package__.split('.')[0]].preferences
            runtime = {key: getattr(preferences, 'default_' + key) for key in
                       package.PROCESSING_DEFAULT_FIELDS if key != 'splitLoopVertices'}
            runtime['exportBlendShapes'] = self.exportBlendShapes
            try:
                runtime = apply_folder_processing(self, context, values, runtime,
                    enable_unified=bool(getattr(self, 'sf6UnifiedOptIn', False)) or
                        getattr(self, 'sf6ExportWorkflow', '') == 'unified_v1')
            except ValueError:
                # Execute performs the structured review. Opening or cancelling
                # the dialog never rewrites an invalid stored profile.
                pass
            else:
                for key in package.PROCESSING_DEFAULT_FIELDS:
                    setattr(self, key, runtime.get(key, True))
                self._sf6FolderProcessingSnapshot = {key: getattr(self, key)
                    for key in package.PROCESSING_DEFAULT_FIELDS}
        update_destination_notice(self, context)
        return context.window_manager.invoke_props_dialog(self, width=650, confirm_text='Review and Export')

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, 'export_content')
        if self.export_content == 'MESH':
            layout.prop_search(self, 'targetCollection', bpy.data, 'collections')
        collection = bpy.data.collections.get(self.targetCollection)
        try:
            asset = package.asset_from_collection(collection) if collection and self.export_content == 'MESH' else None
            if asset and self.export_content == 'MESH':
                layout.label(text=f'{asset.character_name} (esf{asset.character})  |  Costume {asset.costume}  |  {asset.slot_name} ({asset.slot})')
        except ValueError as error:
            layout.label(text=str(error), icon='ERROR')
        layout.separator()
        layout.prop(self, 'parent_directory')
        layout.prop(self, 'folder_name')
        layout.prop(self, 'destination')
        if self.destination_notice:
            # One icon beside a tight text column: lines fill the width, stay
            # aligned with each other and sit close together.
            row = layout.box().row()
            row.label(text='', icon='INFO')
            text_column = row.column(align=True)
            text_column.scale_y = 0.75
            for paragraph in self.destination_notice.splitlines():
                for line in (textwrap.wrap(paragraph, width=96) or ['']):
                    text_column.label(text=line)
        layout.separator()
        for name in ('mod_name', 'mod_version', 'mod_author', 'mod_category', 'extra_categories', 'mod_description', 'preview_path'):
            layout.prop(self, name)
        # Collapsed unless used, like Advanced Export Settings below.
        bundles_open = bool(self.show_bundle_options or self.bundle_name or self.parent_mod_name)
        layout.prop(self, 'show_bundle_options', icon='TRIA_DOWN' if bundles_open else 'TRIA_RIGHT')
        if bundles_open:
            grouping = layout.box()
            grouping.prop(self, 'bundle_name')
            grouping.prop(self, 'parent_mod_name')
            if self.parent_mod_name:
                grouping.prop(self, 'create_dummy_parent')
                if self.create_dummy_parent:
                    grouping.prop(self, 'parent_folder_name')
                    grouping.prop(self, 'parent_categories')
                    grouping.prop(self, 'parent_destination')
        if self.export_content == 'MESH':
            layout.separator()
            layout.prop(self, 'selectedOnly')
            layout.prop(self, 'exportBlendShapes')
            layout.prop(self, 'rotate90')
            # Level of Detail is Automatic for almost everyone, so it lives in
            # Advanced. A saved "LOD0 Only" keeps the section open so a
            # non-default choice is never hidden.
            advanced_open = bool(self.sf6AdvancedOptions or self.sf6LodChoice == 'lod0')
            layout.prop(self, 'sf6AdvancedOptions', icon='TRIA_DOWN' if advanced_open else 'TRIA_RIGHT')
            if advanced_open:
                box = layout.box()
                box.prop(self, 'sf6LodChoice')
                for key in package.PROCESSING_DEFAULT_FIELDS:
                    box.prop(self, key)
        from .sf6_unified_dialogs import draw_details_button
        draw_details_button(layout)

    def execute(self, context):
        if self.sf6LegacyAPI:
            return self._execute_legacy(context)
        try:
            payload = build_folder_payload(self, context)
            if self.sf6ReviewOnly:
                review = prepare_folder_review(payload, {})
                report = dict(review.get('report', {}), review=review, status='planned',
                    scope='mod_folder', publication_started=False, folder_committed=False,
                    destination=payload['path'], target_collection=payload['options'].get('targetCollection', ''))
                self.sf6UnifiedResult = json.dumps(report, sort_keys=True)
                context.scene[SCENE_UNIFIED_RESULT] = self.sf6UnifiedResult
                return {'FINISHED'}
            result = begin_folder_review(context, payload)
            if isinstance(result, set):
                return result
            # Background API calls use the same no-loss fast path as the UI.
            # Anything needing a decision remains an unapproved live request.
            if result.state == 'ready' and not result.review.get('requires_loss_approval'):
                result.approve(result.revision)
            if result.state == 'published':
                return {'FINISHED'}
            if result.state in ('failed', 'cancelled'):
                return {'CANCELLED'}
            self.sf6UnifiedResult = json.dumps(dict(status='pending_review', request_id=result.id,
                scope='mod_folder', destination=payload['path']), sort_keys=True)
            context.scene[SCENE_UNIFIED_RESULT] = self.sf6UnifiedResult
            return {'INTERFACE'}
        except Exception as error:
            report = dict(schema=1, scope='mod_folder', status='failed', published=False,
                publication_started=False, folder_unchanged=True, folder_committed=False,
                error_code=type(error).__name__, error_detail=str(error),
                mesh_export=self.export_content == 'MESH', target_collection=self.targetCollection)
            if hasattr(error, 'report'):
                report['settings'] = deepcopy(error.report)
            finish_folder_review(report)
            return {'CANCELLED'}

    def cancel(self, context):
        finish_folder_review(dict(schema=1, scope='mod_folder', status='cancelled',
            publication_started=False, folder_unchanged=True, folder_committed=False,
            target_collection=self.targetCollection, destination=destination_preview(self)))

    def _execute_legacy(self, context):
        from .blender_re_mesh import exportREMeshFile
        collection = bpy.data.collections.get(self.targetCollection)
        mesh_export = self.export_content == 'MESH'
        previous = getattr(self, '_sf6FolderProfile', None)
        if previous is None:
            previous = load_operator_defaults(context, collection)
        previous = dict(previous)
        review_only = bool(getattr(self, 'sf6ReviewOnly', False))
        requested = review_only or (mesh_export and _unified_requested(self, previous))
        values = {key: getattr(self, key) for key in package.DEFAULT_FIELDS}
        # Empty hidden runtime defaults are not versioned saved profiles.
        for key in package.UNIFIED_DEFAULT_FIELDS + package.PROFILE_EVIDENCE_FIELDS + package.PROCESSING_DEFAULT_FIELDS:
            values.pop(key, None)
            if key in previous:
                values[key] = previous[key]
        if not mesh_export:
            _preserve_profile(values, previous)
            values.setdefault('exportBlendShapes', True)
            values.setdefault('selectedOnly', False)
            values.setdefault('sf6HybridPreserve', False)
            values.setdefault('targetCollection', self.targetCollection)
            values.setdefault('rotate90', self.rotate90)
        # Keep legacy failed-attempt remembering, but opted-in exports do not
        # persist any settings until the entire folder transaction commits.
        if not requested:
            remember_operator_defaults(self, context, values)
        options = {}
        markers = {key: context.scene.get(key) for key in
                   ('REMeshLastExportedCollection', 'REMeshLastExportedMeshVersion')}
        try:
            if review_only and not mesh_export:
                from .sf6_unified_ui import SettingsReviewRequired
                raise SettingsReviewRequired(dict(schema=1, scope='mod_folder',
                    status='review_required', needs_review=True,
                    reason_codes=['unified_review_requires_mesh']))
            if mesh_export and collection is None:
                raise ValueError('Select the mesh collection to export.')
            if mesh_export and self.sf6HybridPreserve and not self.exportBlendShapes and not requested:
                raise ValueError('SF6 Hybrid Shape Export requires Preserve Source Data to be enabled.')
            asset = package.asset_from_collection(collection) if mesh_export else None
            if not self.parent_directory.strip():
                raise ValueError('Choose the parent directory for the mod folder.')
            parent = bpy.path.abspath(self.parent_directory)
            preview = bpy.path.abspath(self.preview_path) if self.preview_path else ''
            preferences = context.preferences.addons[__package__.split('.')[0]].preferences
            options = dict(targetCollection=collection.name if collection else '', selectedOnly=self.selectedOnly,
                           exportBlendShapes=self.exportBlendShapes, sf6HybridPreserve=self.sf6HybridPreserve,
                           rotate90=self.rotate90)
            for name in ('exportAllLODs', 'autoSolveRepeatedUVs', 'preserveSharpEdges',
                         'useBlenderMaterialName', 'preserveBoneMatrices', 'exportBoundingBoxes'):
                options[name] = getattr(preferences, 'default_' + name)
            # Generated modifier vertices can inherit more than SF6's six
            # bone influences. Honor the same explicit weight-cleanup defaults
            # as direct export, without silently trimming the user's weights.
            options.update(limitTotal=getattr(preferences, 'default_limitTotal', False),
                           limitTotalCount=6,
                           normalizeWeights=getattr(preferences, 'default_normalizeWeights', True))
            if requested:
                from .sf6_unified_ui import apply_review_inputs
                options = resolve_folder_options(self, previous, options)
                options = apply_folder_processing(self, context, previous, options,
                    enable_unified=options.get('sf6ExportWorkflow') == 'unified_v1')
                options = apply_review_inputs(self, options)
            unified = options.get('sf6ExportWorkflow') == 'unified_v1'
            final_path = str(Path(parent).expanduser().resolve() /
                             package.validate_folder_name(self.folder_name) /
                             asset.relative_path) if asset else None
            reviewed_plan = None
            if unified:
                from .sf6_unified_export import plan_export, UnifiedSettingsConflict
                reviewed_plan = plan_export(final_path, options)
                options['_sf6UnifiedReport'] = dict(reviewed_plan, status='planned')
                expected = options.pop('_sf6UnifiedExpectedPlan', None)
                if expected is not None and expected != reviewed_plan['plan_token']:
                    options['_sf6UnifiedReport'].update(status='not_committed', reason='stale_plan')
                    raise ValueError('UNIFIED_STALE_PLAN')
                if reviewed_plan['conflicts']:
                    raise UnifiedSettingsConflict(reviewed_plan)
            if getattr(self, 'sf6ReviewOnly', False):
                if not unified:
                    from .sf6_unified_ui import SettingsReviewRequired
                    raise SettingsReviewRequired(dict(schema=1, scope='mod_folder',
                        status='review_required', needs_review=True,
                        reason_codes=['unified_review_requires_opt_in']))
                options['_sf6UnifiedReport']['destination'] = final_path
                _record_unified_result(self, context, options, review_only=True)
                return {'FINISHED'}

            def validate_staged():
                if not unified:
                    return
                # The public review token names the final destination. The
                # backend validates its own temporary path independently.
                if plan_export(final_path, options)['plan_token'] != reviewed_plan['plan_token']:
                    options['_sf6UnifiedReport'].update(status='not_committed', reason='stale_plan')
                    raise ValueError('UNIFIED_PLAN_CHANGED')
                report = options.get('_sf6UnifiedReport', {})
                report.update(plan_token=reviewed_plan['plan_token'], destination=final_path)
                if report.get('status') == 'committed':
                    report['status'] = 'validated'

            def export_mesh(path):
                validate_staged()
                try:
                    return exportREMeshFile(path, options)
                finally:
                    # A mesh committed inside a staging directory is not a
                    # successful folder export, even if a later INI copy fails.
                    _restore_export_markers(context.scene, markers)

            category = self.mod_category or (asset.category if asset else '')
            categories = package.categories_from_text(category) + package.categories_from_text(self.extra_categories)
            metadata = dict(name=self.mod_name, version=self.mod_version, author=self.mod_author,
                            category=categories, description=self.mod_description,
                            nameasbundle=self.bundle_name, addonfor=self.parent_mod_name,
                            dummymod='' if mesh_export else 'True')
            output = package.export_mod_folder(parent, self.folder_name, asset, metadata, preview,
                                               export_mesh,
                                               create_dummy_parent=self.create_dummy_parent and bool(self.parent_mod_name),
                                               parent_folder_name=self.parent_folder_name,
                                               parent_categories=self.parent_categories,
                                               validate_staged=validate_staged if unified else None)
        except Exception as error:
            _restore_export_markers(context.scene, markers)
            if requested:
                _record_unified_result(self, context, options, error)
                return {'CANCELLED'}
            if not isinstance(error, (ValueError, OSError, KeyError)):
                raise
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        if mesh_export:
            context.scene['REMeshLastExportedCollection'] = collection.name
            context.scene['REMeshLastExportedMeshVersion'] = 230110883
            collection['BatchExport_path'] = str(output)
            for key, value in options.items():
                if (key not in ('targetCollection', 'selectedOnly', 'exportBlendShapes', 'sf6HybridPreserve',
                                'sf6ExportWorkflow', 'sf6LodPolicy', 'sf6RigPolicy') and
                        not key.startswith('_')):
                    collection['BatchExport_' + key] = value
        mod_root = Path(parent).resolve() / self.folder_name
        if mesh_export:
            context.scene.re_mdf_toolpanel.modDirectory = str(mod_root / 'natives' / 'stm')
        if requested:
            from .sf6_unified_ui import commit_operator_settings
            committed_settings = commit_operator_settings(self, options)
            for key in ('exportBlendShapes', 'selectedOnly', 'sf6HybridPreserve', 'rotate90'):
                values[key] = options[key]
            for key in package.UNIFIED_DEFAULT_FIELDS:
                values.pop(key, None)
                if key in committed_settings:
                    values[key] = committed_settings[key]
            for key in package.PROCESSING_DEFAULT_FIELDS:
                values.pop(key, None)
                if key in options:
                    values[key] = options[key]
            if options.get('_sf6UnifiedSettingsReport', {}).get('mode') == 'ordinary':
                values['exportAllLODs'] = options.get('sf6LodPolicy') != 'lod0'
            encoded = _record_unified_result(self, context, options, committed=True)
            collection[COLLECTION_UNIFIED_REPORT] = encoded
        values.update(parent_directory=str(Path(parent).resolve()),
                      preview_path=str(Path(preview).resolve()) if preview else '',
                      mod_category=category, last_character=asset.character if asset else self.last_character)
        remember_operator_defaults(self, context, values)
        self._sf6FolderProfile = dict(values)
        self._sf6FolderHasSavedProfile = True
        self.report({'INFO'}, 'Exported mod folder: ' + str(mod_root))
        if mesh_export and self.sf6HybridPreserve and not requested:
            report_hybrid_export(self, options.get('_sf6HybridReport'))
        return {'FINISHED'}
