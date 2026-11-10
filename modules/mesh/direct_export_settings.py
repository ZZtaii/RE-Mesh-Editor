"""Direct-export preferences, isolated from batch and mod-folder exporters.

Stored profiles are merged before validation so malformed higher-priority
values cannot silently resurrect a lower-priority preservation choice. Loads
never rewrite disk or collection state. Workflow migration commits only on
successful export or an explicit save with migration_success=True.
"""
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
import bpy
from . import sf6_unified_ui as unified

KEY = 'REMeshDirectExportSettingsV1'
FIELDS = ('selectedOnly', 'exportAllLODs', 'exportBlendShapes', 'sf6HybridPreserve', 'rotate90',
          'autoSolveRepeatedUVs', 'preserveSharpEdges', 'splitLoopVertices', 'limitTotal',
          'limitTotalCount', 'normalizeWeights', 'shapeKeyExportMode', 'useBlenderMaterialName',
          'preserveBoneMatrices', 'exportBoundingBoxes', 'preserveMaterialTable')
UNIFIED_FIELDS = unified.PERSISTENT_FIELDS

# Blender RNA callbacks may receive a different Python wrapper for the same
# operator. Keep load provenance transiently by its RNA pointer, not in a file.
_RUNTIME_STATE = {}
_STATE_FIELDS = ('_sf6DirectLoadedValues', '_sf6DirectProfileExists',
                 '_sf6DirectLoadIssues', '_sf6DirectLoadedSnapshot',
                 '_sf6DirectExplicitFields', '_sf6DirectHasSavedProfile',
                 '_sf6DirectStoredWorkflow')


class _StateOperator:
    """RNA callbacks cannot accept arbitrary Python attributes."""
    def __init__(self, target, key):
        object.__setattr__(self, '_target', target)
        object.__setattr__(self, '_key', key)
    def __getattr__(self, name):
        if name in _STATE_FIELDS and name in _RUNTIME_STATE.get(self._key, {}):
            return deepcopy(_RUNTIME_STATE[self._key][name])
        return getattr(self._target, name)
    def __setattr__(self, name, value):
        if name in _STATE_FIELDS:
            _RUNTIME_STATE.setdefault(self._key, {})[name] = deepcopy(value)
            try: setattr(self._target, name, deepcopy(value))
            except AttributeError: pass
        else:
            setattr(self._target, name, value)


def _state_operator(operator):
    if isinstance(operator, _StateOperator): return operator
    key = _runtime_key(operator)
    if key is None: return operator
    if len(_RUNTIME_STATE) >= 256 and key not in _RUNTIME_STATE:
        _RUNTIME_STATE.pop(next(iter(_RUNTIME_STATE)))
    return _StateOperator(operator, key)


def _runtime_key(operator):
    session = getattr(operator, 'sf6DialogSession', '')
    if session: return session
    try: return operator.as_pointer()
    except (AttributeError, ReferenceError): return None


def _store_runtime(operator):
    key = _runtime_key(operator)
    if key is not None:
        if len(_RUNTIME_STATE) >= 256 and key not in _RUNTIME_STATE:
            _RUNTIME_STATE.pop(next(iter(_RUNTIME_STATE)))
        _RUNTIME_STATE[key] = {name: deepcopy(getattr(operator, name)) for name in _STATE_FIELDS if hasattr(operator, name)}


def restore_runtime(operator):
    if not (getattr(operator, 'sf6DialogActive', False) or getattr(operator, '_direct_export_dialog', False)):
        return
    for name, value in _RUNTIME_STATE.get(_runtime_key(operator), {}).items():
        setattr(operator, name, deepcopy(value))


def clear_runtime():
    _RUNTIME_STATE.clear()



def settings_path():
    return Path(bpy.utils.user_resource('CONFIG')) / 're_mesh_editor' / 'direct_mesh_export.json'


def _read_defaults():
    try:
        value = json.loads(settings_path().read_text())
    except FileNotFoundError:
        return {}, []
    except OSError:
        return {}, ['direct_settings_unreadable']
    except (ValueError, UnicodeError):
        return {}, ['direct_settings_corrupt']
    if not isinstance(value, dict):
        return {}, ['direct_settings_invalid_root']
    return value, []


def read_defaults():
    return _read_defaults()[0]


def _saved_profile_exists(operator, disk, issues=()):
    """Conservative physical presence, independent of Remember Fields."""
    versions = disk.get('versions', {})
    if issues or not isinstance(versions, dict) or operator.filename_ext in versions:
        return True
    collection = bpy.data.collections.get(operator.targetCollection)
    if collection is not None and KEY in collection:
        try:
            local = json.loads(collection[KEY])
        except (ValueError, TypeError, UnicodeError):
            return True
        return not isinstance(local, dict) or local.get('version') == operator.filename_ext
    return False


def _display_new_profile_defaults(operator):
    if not unified.is_sf6(operator.filename_ext):
        return
    values = {key: getattr(operator, key) for key in FIELDS + UNIFIED_FIELDS if hasattr(operator, key)}
    resolved = unified.apply_new_profile_defaults(values,
        has_saved_profile=operator._sf6DirectHasSavedProfile,
        explicit=operator._sf6DirectExplicitFields,
        enable_unified=bool(getattr(operator, 'sf6UnifiedOptIn', False)))
    for key in resolved.get('_sf6NewProfileDefaults', ()):
        setattr(operator, key, resolved[key])


def _valid(operator, key, value):
    if key == unified.WORKFLOW_KEY:
        return type(value) is str and value == 'unified_v1'
    if key == unified.LOD_POLICY_KEY:
        return type(value) is str and value in ('retain_if_compatible', 'lod0')
    prop = getattr(operator, 'properties', operator).bl_rna.properties.get(key)
    if prop is None:
        return False
    return ((prop.type == 'BOOLEAN' and type(value) is bool) or
            (prop.type == 'INT' and type(value) is int and prop.hard_min <= value <= prop.hard_max) or
            (prop.type == 'ENUM' and type(value) is str and value in prop.enum_items.keys()))


def _remember_state(operator, values, issues, profile_exists):
    operator._sf6DirectLoadedValues = deepcopy(values)
    operator._sf6DirectProfileExists = profile_exists
    operator._sf6DirectLoadIssues = tuple(issues)
    operator._sf6DirectLoadedSnapshot = {key: getattr(operator, key) for key in FIELDS + UNIFIED_FIELDS
                                        if hasattr(operator, key)}
    _store_runtime(operator)
    return dict(schema=1, status='review_required' if issues else 'loaded',
                scope='direct', reason_codes=list(issues), needs_review=bool(issues))


def load(operator, context, explicit=(), restore_enabled=True):
    """Load priority: explicit caller fields > matching collection > disk."""
    operator = _state_operator(operator)
    disk, issues = _read_defaults()
    # invoke() captured these before its default-setting callbacks marked all
    # RNA fields as set. Preserve that original provenance for new defaults.
    operator._sf6DirectExplicitFields = tuple(explicit)
    operator._sf6DirectHasSavedProfile = _saved_profile_exists(operator, disk, issues)
    if restore_enabled and 'rememberFields' not in explicit:
        enabled = disk.get('enabled', True)
        operator.rememberFields = enabled if type(enabled) is bool else True
    if not operator.rememberFields:
        operator._sf6DirectStoredWorkflow = None
        _display_new_profile_defaults(operator)
        return _remember_state(operator, {}, [], False)

    sf6 = unified.is_sf6(operator.filename_ext)
    versions = disk.get('versions', {})
    if not isinstance(versions, dict):
        versions = {}
        issues.append('direct_settings_invalid_versions')
    profile = versions.get(operator.filename_ext, {})
    profile_exists = operator.filename_ext in versions
    if not isinstance(profile, dict):
        profile = {}
        issues.append('direct_settings_invalid_profile')
    values = deepcopy(profile)
    collection = bpy.data.collections.get(operator.targetCollection)
    if collection is not None and KEY in collection:
        try:
            local = json.loads(collection[KEY])
        except (ValueError, TypeError, UnicodeError):
            local = {}
            issues.append('direct_collection_settings_corrupt')
        if not isinstance(local, dict):
            local = {}
            issues.append('direct_collection_settings_invalid_root')
        if local.get('version') == operator.filename_ext:
            local_values = local.get('values', {})
            if isinstance(local_values, dict):
                values.update(deepcopy(local_values))
                profile_exists = True
            else:
                issues.append('direct_collection_settings_invalid_profile')
            if 'filepath' not in explicit and isinstance(local.get('filepath'), str):
                operator.filepath = local['filepath']

    operator._sf6DirectStoredWorkflow = values.get(unified.WORKFLOW_KEY) if sf6 else None
    fields = FIELDS + (UNIFIED_FIELDS if sf6 else ())
    # Clear stale hidden fields when switching versions/collections, unless the
    # current invocation explicitly supplied them. Do not persist this reset.
    for key in UNIFIED_FIELDS:
        if key not in explicit and hasattr(operator, key):
            setattr(operator, key, '')
    for key in fields:
        if key in explicit:
            value = explicit[key] if isinstance(explicit, dict) else getattr(operator, key)
            if key in UNIFIED_FIELDS and value == '':
                values.pop(key, None)
            else:
                values[key] = value
            if _valid(operator, key, value):
                setattr(operator, key, value)
            continue
        if key in values and _valid(operator, key, values[key]):
            setattr(operator, key, values[key])

    # Preserve only this direct profile's fields as migration evidence. Never
    # import batch state or old execution-scoped grants found in a JSON blob.
    loaded = {key: values[key] for key in fields if key in values}
    if not sf6:
        issues = []
    _display_new_profile_defaults(operator)
    return _remember_state(operator, loaded, issues, profile_exists)


def prepare_options(operator, options, *, mesh_version, enable_unified=False):
    """Validate remembered intent before overlaying complete runtime defaults."""
    if getattr(operator, 'sf6DialogActive', False) or getattr(operator, '_direct_export_dialog', False) or hasattr(operator, '_sf6DirectExplicitFields'):
        operator = _state_operator(operator)
    restore_runtime(operator)
    if not unified.is_sf6(mesh_version):
        return unified.resolve_options(options, mesh_version=mesh_version)
    original_explicit = getattr(operator, '_sf6DirectExplicitFields', None)
    if original_explicit is None:
        properties = getattr(operator, 'properties', operator)
        is_set = getattr(properties, 'is_property_set', lambda key: False)
        explicit = {key for key in unified.NEW_PROFILE_DEFAULTS if is_set(key)}
        disk, disk_issues = _read_defaults()
        has_saved_profile = _saved_profile_exists(operator, disk, disk_issues)
    else:
        explicit = set(original_explicit)
        loaded_snapshot = getattr(operator, '_sf6DirectLoadedSnapshot', {})
        explicit.update(key for key in unified.NEW_PROFILE_DEFAULTS
                        if key in loaded_snapshot and getattr(operator, key, None) != loaded_snapshot[key])
        has_saved_profile = getattr(operator, '_sf6DirectHasSavedProfile', True)
    options = unified.apply_new_profile_defaults(options, has_saved_profile=has_saved_profile,
        explicit=explicit, enable_unified=enable_unified)
    issues = getattr(operator, '_sf6DirectLoadIssues', ())
    if issues:
        raise unified.SettingsReviewRequired(unified.review_report(issues))
    values = deepcopy(getattr(operator, '_sf6DirectLoadedValues', {}))
    snapshot = getattr(operator, '_sf6DirectLoadedSnapshot', {})
    profile_exists = getattr(operator, '_sf6DirectProfileExists', False)
    # Dialog edits after load override its raw saved value. Snapshot comparison
    # keeps absent old scope fields absent rather than silently inventing them.
    for key in FIELDS + UNIFIED_FIELDS:
        current = getattr(operator, key, options.get(key))
        if key in snapshot and current != snapshot[key]:
            if key in UNIFIED_FIELDS and current == '':
                values.pop(key, None)
            else:
                values[key] = current
    if profile_exists:
        # Validate all stored recognized values, including ones the RNA loader
        # refused to assign. Explicit caller overrides were already merged.
        invalid = ['invalid_direct_field:' + key for key, value in values.items()
                   if not _valid(operator, key, value)]
        if invalid:
            raise unified.SettingsReviewRequired(unified.review_report(invalid))
        requested = enable_unified or options.get(unified.WORKFLOW_KEY) == 'unified_v1'
        # A newly supplied marker must not erase the old Hybrid LOD0 contract.
        stored_marker = getattr(operator, '_sf6DirectStoredWorkflow', None)
        newly_marked = (requested and stored_marker != 'unified_v1')
        if newly_marked and values.get(unified.WORKFLOW_KEY) == 'unified_v1':
            values.pop(unified.WORKFLOW_KEY)
        result = unified.resolve_profile(values, mesh_version=mesh_version,
                                          enable_unified=requested)
        if result.needs_review:
            raise unified.SettingsReviewRequired(unified.profile_report(result, 'direct'))
        runtime = dict(options)
        for key in UNIFIED_FIELDS:
            runtime.pop(key, None)
            if key in result.settings:
                runtime[key] = result.settings[key]
        if 'sf6HybridPreserve' in result.settings:
            runtime['sf6HybridPreserve'] = result.settings['sf6HybridPreserve']
        resolved = unified.resolve_options(runtime, mesh_version=mesh_version,
                                            enable_unified=requested)
        runtime_wrapper = resolved['_sf6UnifiedSettingsReport'].get('runtime_unified_wrapper', False)
        resolved['_sf6UnifiedSettingsReport'] = unified.profile_report(result, 'direct')
        if runtime_wrapper:
            resolved['_sf6UnifiedSettingsReport']['runtime_unified_wrapper'] = True
        return resolved
    return unified.resolve_options(options, mesh_version=mesh_version,
                                    enable_unified=enable_unified)


def save(operator, context, *, migration_success=False):
    """Save normal preferences; promote migrated workflow only after success.

    The default keeps existing workflow metadata, so an old cancel/save hook
    cannot accidentally promote a hidden opt-in that never exported. Unknown
    or corrupt existing profiles are never overwritten automatically, even
    after a successful export with remembered fields disabled.
    """
    operator = _state_operator(operator)
    disk, issues = _read_defaults()
    sf6 = unified.is_sf6(operator.filename_ext)
    issues.extend(getattr(operator, '_sf6DirectLoadIssues', ()))
    versions = disk.get('versions', {})
    if not isinstance(versions, dict):
        issues.append('direct_settings_invalid_versions')
    elif operator.filename_ext in versions:
        existing = versions[operator.filename_ext]
        if not isinstance(existing, dict):
            issues.append('direct_settings_invalid_profile')
        elif sf6:
            check = unified.resolve_profile(existing, mesh_version=operator.filename_ext)
            if check.needs_review:
                issues.extend(check.reason_codes)
    # Recheck the persisted collection too: a disabled load or an external
    # change since invocation must not authorize overwriting unknown data.
    collection = bpy.data.collections.get(operator.targetCollection)
    if collection is not None and KEY in collection:
        try:
            local = json.loads(collection[KEY])
        except (ValueError, TypeError, UnicodeError):
            local = None
            issues.append('direct_collection_settings_corrupt')
        if not isinstance(local, dict):
            issues.append('direct_collection_settings_invalid_root')
        elif (local.get('version') == operator.filename_ext or
              unified.is_sf6(local.get('version'))):
            existing = local.get('values', {})
            if not isinstance(existing, dict):
                issues.append('direct_collection_settings_invalid_profile')
            elif unified.is_sf6(local.get('version')):
                check = unified.resolve_profile(existing, mesh_version=local['version'])
                if check.needs_review:
                    issues.extend(check.reason_codes)
    if issues:
        report = unified.review_report(tuple(dict.fromkeys(issues)))
        report.update(phase='persistence', save_blocked=True, persisted=False)
        return report
    disk['enabled'] = bool(operator.rememberFields)
    values = None
    if operator.rememberFields:
        values = {key: getattr(operator, key) for key in FIELDS}
        if sf6:
            if migration_success:
                workflow = getattr(operator, unified.WORKFLOW_KEY, '')
                policy = getattr(operator, unified.LOD_POLICY_KEY, '')
                candidate = {key: value for key, value in
                             ((unified.WORKFLOW_KEY, workflow), (unified.LOD_POLICY_KEY, policy)) if value}
                check = unified.resolve_profile(dict(values, **candidate), mesh_version=operator.filename_ext)
                if check.needs_review:
                    return unified.profile_report(check, 'direct')
                values.update({key: check.settings[key] for key in UNIFIED_FIELDS if key in check.settings})
            else:
                previous = getattr(operator, '_sf6DirectLoadedValues', {})
                check = unified.resolve_profile(previous, mesh_version=operator.filename_ext)
                if check.needs_review:
                    return unified.profile_report(check, 'direct')
                values.update({key: previous[key] for key in UNIFIED_FIELDS if key in previous})
        versions = disk.get('versions', {})
        if not isinstance(versions, dict):
            versions = {}
        versions[operator.filename_ext] = values
        disk['versions'] = versions

    path = settings_path()
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         delete=False) as stream:
            temporary = stream.name
            json.dump(disk, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        operator.report({'WARNING'}, 'Direct export settings could not be saved: ' + str(error))
        return dict(schema=1, status='not_saved', phase='persistence', persisted=False,
                    reason_codes=['direct_settings_write_failed'])
    # Match collection metadata only after atomic disk write succeeds. This
    # avoids promoting one persistent location when the other failed.
    if values is not None:
        collection = bpy.data.collections.get(operator.targetCollection)
        if collection is not None:
            collection[KEY] = json.dumps(dict(version=operator.filename_ext,
                                              values=values, filepath=operator.filepath))
        _remember_state(operator, values, [], True)
    return dict(schema=1, status='saved', phase='persistence', persisted=True, reason_codes=[])
