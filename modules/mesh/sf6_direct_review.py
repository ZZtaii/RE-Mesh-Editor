"""Detached direct-export callbacks: no expired file-browser RNA is retained."""
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import hashlib
import bpy
from . import direct_export_settings as settings, sf6_unified_ui as ui
from . import sf6_unified_dialogs as dialogs, sf6_unified_messages as messages


def snapshot(operator, options):
    fields = settings.FIELDS + settings.UNIFIED_FIELDS + ('filepath', 'filename_ext', 'targetCollection', 'rememberFields', 'sf6SettingsScope')
    values = {key: deepcopy(getattr(operator, key)) for key in fields if hasattr(operator, key)}
    for key in ('_sf6DirectLoadIssues', '_sf6DirectLoadedValues'):
        values[key] = deepcopy(getattr(operator, key, () if key.endswith('Issues') else {}))
    return dict(path=operator.filepath, options=deepcopy(options), settings=values)


def destination_signature(path):
    try: return ("file", hashlib.sha256(Path(path).read_bytes()).hexdigest())
    except FileNotFoundError: return ("absent",)
    except OSError: return None


def publish(payload, overrides):
    from .blender_re_mesh import exportREMeshFile
    options = deepcopy(payload['options']); options.update(deepcopy(overrides))
    notices = []
    operator = SimpleNamespace(**deepcopy(payload['settings']))
    operator.report = lambda severity, text: notices.append(str(text))
    before = destination_signature(payload['path'])
    try:
        if not exportREMeshFile(payload['path'], options):
            raise RuntimeError('PROTOTYPE_ORDINARY_EXPORT_FAILED')
    except Exception as error:
        committed = options.get('_sf6UnifiedReport', {}).get('status') == 'committed'
        try:
            result = ui.record_result(operator, options, error=error)
        except Exception as reporting_error:
            result = deepcopy(options.get('_sf6UnifiedReport', {}))
            result.update(error_detail=str(error), reporting_error=str(reporting_error))
        unchanged = before is not None and before == destination_signature(payload['path'])
        result.update(status='committed' if committed else 'failed', published=committed, destination=payload['path'], previous_output_preserved=unchanged)
        if committed: result.setdefault('warnings', []).append('The mesh was published, but final reporting failed: ' + str(error))
        return result
    # Publication has happened. Settings/notification failures cannot turn it
    # into a claim that nothing was written.
    result = deepcopy(options.get('_sf6UnifiedReport', {}))
    result.update(status='committed', published=True, destination=payload['path'], scope='direct', target_collection=operator.targetCollection)
    try:
        for key in settings.FIELDS:
            if key in options: setattr(operator, key, options[key])
        ui.commit_operator_settings(operator, options)
        result['persistence'] = settings.save(operator, bpy.context, migration_success=True)
        if not result['persistence'].get('persisted'):
            notices.append('Export settings were not saved. Existing saved settings were kept.')
    except Exception as error:
        result['persistence'] = dict(persisted=False, error_detail=str(error))
        notices.append('The mesh was exported, but settings could not be saved: ' + str(error))
    try:
        bpy.context.scene['REMeshDefaultExportSettingsLoaded'] = 1
    except Exception as error:
        notices.append('The mesh was exported, but the scene settings flag could not be updated: ' + str(error))
    try:
        panel = getattr(bpy.context.scene, 're_mdf_toolpanel', None)
        if panel is not None and not panel.modDirectory:
            from ... import setModDirectoryFromFilePath
            setModDirectoryFromFilePath(payload['path'])
    except Exception as error:
        notices.append('The mesh was exported, but the mod-directory shortcut could not be updated: ' + str(error))
    if notices: result.setdefault('warnings', []).extend(notices)
    options['_sf6UnifiedReport'] = result
    try:
        ui.record_result(operator, options)
    except Exception as error:
        result.setdefault('warnings', []).append('The mesh was exported, but result recording failed: ' + str(error))
    return result


def finish(report):
    if report.get('status') == 'committed': model = messages.format_result(report)
    elif report.get('status') == 'cancelled': model = messages.format_cancel(before_publication=report.get('publication_started') is False)
    else: model = messages.format_error(report.get('error_detail', 'Export could not be completed.'), previous_output_preserved=report.get('previous_output_preserved') is True)
    dialogs.notify(' '.join(model['lines']), model['severity'])


def begin(operator, context, options):
    request = dialogs.begin_review(context, operation='direct', payload=snapshot(operator, options), publish=publish, on_result=finish)
    if isinstance(request, set): return request
    # Tests/background callers never implicitly approve a loss or owner choice.
    if request.state == 'ready' and not request.review.get('requires_loss_approval'):
        request.approve(request.revision)
    if request.state == 'published': return {'FINISHED'}
    if request.state in ('failed', 'cancelled'): return {'CANCELLED'}
    return {'INTERFACE'}
