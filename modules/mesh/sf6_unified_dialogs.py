"""Visible, fail-closed review shared by direct, folder and batch exports.

Integration contract:
    begin_review(context, operation=..., payload=..., prepare=..., publish=...)
Both callbacks receive (a detached copy of payload, transient overrides).
``prepare`` returns sf6_review_plan.prepare_review's structured review and
MUST NOT publish. ``publish`` returns the actual publication report, including
partial/uncertain folder outcomes. Neither callback may infer loss approval.
An optional current_payload callback detects edits to live caller fields.

Only the review dialog's confirm button (or the continue operator of the legacy
popup) calls ReviewRequest.approve, and only for the revision that was drawn.
Dismissal, Enter, owner choices, refresh, Details and rig changes never call it.
Requests and approvals are execution-only, never stored in a .blend. Readable
result Details are stored separately as a Blender Text datablock.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import textwrap
import uuid

try:
    import bpy
    from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                           IntProperty, StringProperty)
except ImportError:  # The controller and its tests do not depend on Blender.
    bpy = None


_REQUESTS = {}
_RESULTS = {}
_ACTIVE = {}
_ENUM_ITEMS = {}
_SYNCING = False
DETAILS_NAME = 'SF6 Export Details'
TRANSIENT_KEYS = ('_sf6UnifiedExpectedPlan', '_sf6UnifiedExpectedOutputSHA256',
                  '_sf6ApprovedCorrectiveLosses')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode('utf8')).hexdigest()


def _review_identity(review):
    # Compare the complete reviewed scope, not just a caller-supplied token.
    return _digest({key: review.get(key) for key in (
        'status', 'complete', 'plan_token', 'output_sha256', 'losses', 'approved_loss_tokens',
        'requires_loss_approval', 'duplicate_groups', 'conflicts')})


def _validate_ready(review):
    """Malformed review data cannot become permission through presentation."""
    if review.get('status') != 'ready':
        return
    if not review.get('complete') or not isinstance(review.get('plan_token'), str) or not review['plan_token']:
        raise ValueError('UNIFIED_REVIEW_INCOMPLETE')
    if any(g.get('requires_choice') for g in review.get('duplicate_groups', [])):
        raise ValueError('UNIFIED_REVIEW_OWNER_CHOICE_REQUIRED')
    for loss in review.get('losses', []):
        if loss.get('scope') not in ('section', 'object', 'output'):
            raise ValueError('UNIFIED_REVIEW_LOSS_SCOPE_INVALID')
        if loss.get('source_status') not in ('verified', 'unverified'):
            raise ValueError('UNIFIED_REVIEW_SOURCE_STATUS_INVALID')
        names = loss.get('shape_names')
        if not isinstance(names, list) or any(type(name) is not str for name in names):
            raise ValueError('UNIFIED_REVIEW_SHAPE_NAMES_INVALID')


def _options(payload):
    """The conventional payload nests options; caller-specific fields survive."""
    return payload.get('options', payload)


def _exception_report(error):
    report = deepcopy(getattr(error, 'export_report', None) or getattr(error, 'report', None) or getattr(error, 'plan', None) or {})
    if not isinstance(report, dict):
        report = {}
    report.update(error_code=type(error).__name__, error_detail=str(error))
    if getattr(error, 'conflicts', None):
        report['conflicts'] = deepcopy(error.conflicts)
    if report.get('conflicts'):
        report['status'] = 'needs_policy_choice'
    else:
        report.setdefault('status', 'blocked')
    return report


class ReviewRequest:
    """Single-use immutable request plus explicit, invalidating local choices."""
    def __init__(self, *, operation, payload, prepare, publish, on_result=None,
                 current_payload=None, context_guard=None):
        self.id = uuid.uuid4().hex
        self.operation = str(operation)
        self._payload = deepcopy(payload)
        self._payload_digest = _digest(self._payload)
        self._prepare = prepare
        self._publish = publish
        self._on_result = on_result
        self._current_payload = current_payload
        self._context_guard = context_guard
        self._original_context = context_guard() if context_guard else None
        self.overrides = {}
        self.review = {}
        self.result = None
        self.revision = 0
        self.state = 'new'
        self.reason = ''
        self._busy = False
        self._notified = False
        self.notification_owned = False
        self.view_nonce = None
        self.showing_details = False

    @property
    def payload(self):
        return deepcopy(self._payload)

    def _fresh_context(self):
        return (not self._context_guard or self._context_guard() == self._original_context)

    def _fresh_payload(self):
        return (not self._current_payload or
                _digest(self._current_payload()) == self._payload_digest)

    def _clean_overrides(self):
        values = deepcopy(self.overrides)
        for key in TRANSIENT_KEYS:
            values.pop(key, None)
        return values

    def prepare(self):
        if self._busy or self.state in ('cancelled', 'published', 'publishing', 'failed'):
            return deepcopy(self.review)
        self.revision += 1
        self.state = 'preparing'
        self.reason = ''
        self.overrides = self._clean_overrides()
        try:
            if not self._fresh_context() or not self._fresh_payload():
                raise ValueError('UNIFIED_REQUEST_CHANGED')
            value = self._prepare(self.payload, deepcopy(self.overrides))
            if not isinstance(value, dict):
                raise TypeError('UNIFIED_REVIEW_REPORT_MISSING')
            self.review = deepcopy(value)
            self.review.setdefault('selected_only', bool(_options(self._payload).get('selectedOnly', False)))
            _digest(self.review)
            _validate_ready(self.review)
            if self.review.get('status') == 'ready' and self.review.get('complete') and self.review.get('plan_token'):
                self.state = 'ready'
            elif self.review.get('status') == 'needs_owner_choice':
                self.state = 'needs_owner_choice'
            else:
                self.state = 'blocked'
        except Exception as error:
            self.review = _exception_report(error)
            self.state = 'blocked'
        return deepcopy(self.review)

    def invalidate(self, reason='choices_changed'):
        if self._busy or self.state in ('cancelled', 'published', 'publishing', 'failed'):
            return False
        self.overrides = self._clean_overrides()
        self.revision += 1
        self.state = 'dirty'
        self.reason = reason
        return True

    def choose_owner(self, source_key, owner):
        groups = {g['source_key']: g for g in self.review.get('duplicate_groups', [])}
        if source_key not in groups:
            raise ValueError('UNIFIED_UNKNOWN_OWNER_GROUP')
        eligible = {c.get('object_pointer') for c in groups[source_key].get('claimants', [])
                    if c.get('eligible', False)}
        if owner is not None and (type(owner) is not int or owner not in eligible):
            raise ValueError('UNIFIED_INELIGIBLE_OWNER')
        if not self.invalidate('owner_changed'):
            return False
        self.overrides.setdefault('_sf6PreserveSourceOwners', {})[source_key] = owner
        return True

    def clear_owner(self, source_key):
        if source_key not in {g['source_key'] for g in self.review.get('duplicate_groups', [])}:
            raise ValueError('UNIFIED_UNKNOWN_OWNER_GROUP')
        if not self.invalidate('owner_changed'):
            return False
        values = self.overrides.setdefault('_sf6PreserveSourceOwners', {})
        values.pop(source_key, None)
        return True

    def set_preserve_bone_matrices(self, value):
        if type(value) is not bool:
            raise TypeError('Preserve Bone Matrices requires a boolean')
        if not self.invalidate('rig_settings_changed'):
            return False
        self.overrides['preserveBoneMatrices'] = value
        return True

    def cancel(self):
        if self.state in ('published', 'publishing', 'failed') or self._busy:
            return False
        self.overrides = {}
        self.revision += 1
        self.state = 'cancelled'
        self.result = dict(schema=1, status='cancelled', scope=self.operation,
                           publication_started=False,
                           review=deepcopy(self.review),
                           destination=self._payload.get('path', self._payload.get('destination', '')))
        self._notify()
        return True

    def _notify(self):
        if self._on_result and not self._notified:
            self._notified = True
            try:
                self._on_result(deepcopy(self.result))
            except Exception as error:
                self.result.setdefault('notification_errors', []).append(str(error))

    def approve(self, revision):
        """An explicit click approves exactly one current, fully displayed plan."""
        if self._busy or self.state != 'ready' or revision != self.revision:
            return False
        self._busy = True
        previous = _review_identity(self.review)
        try:
            if not self._fresh_context() or not self._fresh_payload():
                self.state = 'blocked'
                self.reason = 'request_changed'
                self.review = dict(status='blocked', error_detail='UNIFIED_REQUEST_CHANGED')
                self.revision += 1
                return False
            fresh = self._prepare(self.payload, self._clean_overrides())
            if isinstance(fresh, dict):
                fresh = deepcopy(fresh)
                fresh.setdefault('selected_only', bool(_options(self._payload).get('selectedOnly', False)))
                _validate_ready(fresh)
            if not isinstance(fresh, dict) or _review_identity(fresh) != previous:
                self.review = deepcopy(fresh) if isinstance(fresh, dict) else {}
                self.state = ('ready' if self.review.get('status') == 'ready' and
                              self.review.get('complete') and self.review.get('plan_token') else 'blocked')
                if self.review.get('status') == 'needs_owner_choice':
                    self.state = 'needs_owner_choice'
                self.reason = 'plan_changed'
                self.revision += 1
                return False
            grants = self._clean_overrides()
            grants['_sf6UnifiedExpectedPlan'] = self.review['plan_token']
            if self.review.get('output_sha256'):
                grants['_sf6UnifiedExpectedOutputSHA256'] = self.review['output_sha256']
            losses = self.review.get('losses', [])
            tokens = self.review.get('approved_loss_tokens', [r.get('loss_token') for r in losses])
            if not isinstance(tokens, list) or any(type(t) is not str or not t for t in tokens):
                raise ValueError('UNIFIED_REVIEW_LOSS_TOKENS_INVALID')
            if self.review.get('requires_loss_approval') and not tokens:
                raise ValueError('UNIFIED_REVIEW_LOSS_TOKENS_MISSING')
            if bool(losses) != bool(self.review.get('requires_loss_approval')):
                raise ValueError('UNIFIED_REVIEW_LOSS_APPROVAL_MISMATCH')
            grants['_sf6ApprovedCorrectiveLosses'] = list(dict.fromkeys(tokens))
            self.state = 'publishing'
            value = self._publish(self.payload, grants)
            if not isinstance(value, dict):
                raise TypeError('UNIFIED_PUBLICATION_REPORT_MISSING')
            self.result = deepcopy(value)
            self.result.setdefault('scope', self.operation)
            self.result.setdefault('review', deepcopy(self.review))
            self.result.setdefault('destination', self._payload.get('path', self._payload.get('destination', '')))
            self.state = 'published' if self.result.get('status') == 'committed' else 'failed'
            self.overrides = {}
            self._notify()
            return self.state == 'published'
        except Exception as error:
            if self.state == 'publishing':
                # A folder may already contain published files. Never infer rollback.
                self.result = _exception_report(error)
                self.result.update(scope=self.operation, publication_started=True)
                self.result.setdefault('publication_outcome', 'unverified')
                self.state = 'failed'
                self.overrides = {}
                self._notify()
            else:
                self.review = _exception_report(error)
                self.state = 'blocked'
                self.revision += 1
            return False
        finally:
            self._busy = False


def standard_prepare(payload, overrides):
    from .sf6_review_plan import prepare_review
    from .blender_re_mesh import exportREMeshFile
    options = deepcopy(payload['options'])
    for key in TRANSIENT_KEYS:
        options.pop(key, None)
    options.update(deepcopy(overrides))
    return prepare_review(payload['path'], options, exportREMeshFile)


def _messages():
    try:
        from . import sf6_unified_messages
        return sf6_unified_messages
    except ImportError:
        return None


def _wrapped(lines, width=93):
    output = []
    for line in lines:
        output.extend(textwrap.wrap(str(line), width, replace_whitespace=False,
                                   drop_whitespace=True) or [''])
    return output


def review_lines(review, *, history=False):
    """Complete readable lines; no loss or key-name truncation."""
    lines = []
    messages = _messages()
    files = review.get('files', review.get('report', {}).get('files', []))
    for item in files if not history else []:
        lines.append('File: ' + str(item.get('file', item.get('destination', item.get('path', '')))))
        lines.append('Status: ' + str(item.get('status', 'unverified')))
        if item.get('error') or item.get('error_detail'):
            lines.append('Error: ' + str(item.get('error', item.get('error_detail'))))
        if messages:
            lines.extend(messages.format_ordinary_errors(item.get('result', {}).get('ordinary_errors', [])))
        if item.get('validation_deferred'):
            lines.append('Validation will run before publishing this file.')
    for conflict in review.get('conflicts', []):
        if conflict.get('file'):
            lines.append('Rig setting conflict: ' + str(conflict['file']))
    if messages:
        lines.extend(messages.format_ordinary_errors(review.get('ordinary_errors',
            review.get('report', {}).get('ordinary_errors', []))))
    if messages and (review.get('duplicate_groups') or review.get('conflicts')):
        lines.extend(messages.view_model(review)['lines'])
    retained = review.get('retained_sections', [])
    losses = review.get('losses', [])
    for index, loss in enumerate(losses, 1):
        scope = loss.get('scope', 'section')
        obj = str(loss.get('object', ''))
        material = str(loss.get('material') or ', '.join(loss.get('materials', [])))
        names = loss.get('shape_names', [])
        lines.append(f'{index}. {scope.title()}: {obj}' + (f' / {material}' if material else ''))
        if messages:
            card = messages.review_card(loss, retained_sections=(len(retained) if isinstance(retained, list) else retained),
                selected_only=loss.get('selected_only', review.get('selected_only', False)))
            lines.append('Reviewed shape-key loss' if history else card['title'])
            lines.extend(line for line in card['lines'] if not line.startswith('Keys not exported:'))
            if card.get('unmapped_codes'):
                lines.append('Technical reason: ' + '; '.join(card['unmapped_codes']))
        else:
            if loss.get('file'):
                lines.append('File: ' + str(loss['file']))
            lines.append('Reason: ' + str(loss.get('reason', loss.get('source_reason', ''))))
            if loss.get('source_status') == 'unverified':
                lines.append('Original corrective shapes may be unavailable to identify.')
        lines.append('Keys not exported:')
        lines.extend('  ' + str(name) for name in names)
        if not names:
            lines.append('  No known shape-key names could be identified.')
        lines.append('')
    if retained:
        lines.append('Retained sections:')
        for section in retained if isinstance(retained, list) else []:
            if isinstance(section, dict):
                lines.append(str(section.get('object', '')) + ' / ' + str(section.get('material', '')))
                lines.extend('  ' + str(name) for name in section.get('shape_names', section.get('key_names', [])))
            else:
                lines.append(str(section))
    if not lines:
        if review.get('conflicts'):
            lines.append('Review Preserve Bone Matrices for the edited rest rig before exporting.')
        elif review.get('status') == 'needs_owner_choice':
            lines.append('Choose at most one compatible copy to retain the original corrective shapes.')
        elif review.get('status') == 'ready':
            lines.append('No shape-key loss approval is required by this plan.')
        else:
            lines.append('Export stopped. Review the error details before trying again.')
    if review.get('error_detail'):
        if messages:
            lines.extend(messages.format_error(review['error_detail'])['lines'])
        lines.append('Technical detail: ' + str(review['error_detail']))
    return lines


def details_lines(report):
    """One readable result, complete section/key history, then technical evidence."""
    messages = _messages()
    committed = report.get('status') == 'committed'
    lines = ['SF6 Export Details', '', 'Status: ' + str(report.get('status', 'unverified')),
             'Scope: ' + str(report.get('scope', 'direct')),
             'Destination: ' + str(report.get('destination') or report.get('output_path') or 'See files below')]
    if messages:
        if report.get('scope') == 'batch' and report.get('files'):
            model = messages.format_batch(report)
        elif committed:
            model = messages.format_result(report)
        elif report.get('status') == 'cancelled':
            model = messages.format_cancel(before_publication=report.get('publication_started') is False,
                folder_unchanged=report.get('folder_unchanged') is True)
        elif report.get('error_detail'):
            model = messages.format_error(report['error_detail'],
                previous_output_preserved=report.get('previous_output_preserved') is True)
        else:
            model = None
        if model:
            lines += ['', model['title']] + model['lines']
            if model.get('unmapped_codes'):
                lines.append('Technical reasons: ' + '; '.join(model['unmapped_codes']))
    if report.get('publication_outcome'):
        lines.append('Publication outcome: ' + str(report['publication_outcome']))
    if report.get('publication_started') and not committed:
        lines.append('Publication was attempted. Check Details for any files already written.')
    review = report.get('review', report)
    actual = report if 'output_lods' in report else review.get('report', report)
    for key, label in (('output_lods', 'Output LODs'), ('source_lods', 'Source LODs'),
                       ('output_shape_links', 'Output shape links')):
        if key in actual:
            lines.append(label + ': ' + str(actual[key]))
    if review.get('retained_sections') is not None:
        rows = review['retained_sections']
        lines.append('Retained sections: ' + str(len(rows) if isinstance(rows, list) else rows))
    for key, label in (('ordinary_section_count', 'Ordinary sections'),
                       ('shape_free_compatible_section_count', 'Compatible sections without corrective links')):
        if review.get(key) is not None:
            lines.append(label + ': ' + str(review[key]))
    if report.get('error_detail'):
        lines.append('Error: ' + str(report['error_detail']))
    if messages:
        lines.extend(messages.format_ordinary_errors(report.get('ordinary_errors',
            actual.get('ordinary_errors', []))))
    for item in report.get('files', []):
        lines += ['', 'File: ' + str(item.get('file', item.get('destination', item.get('path', '')))),
                  'Status: ' + str(item.get('status', 'unverified'))]
        child = item.get('result', {})
        if messages and child and item.get('status') == 'committed':
            lines += messages.format_result(child)['lines']
        if item.get('error') or item.get('error_detail'):
            lines.append('Error: ' + str(item.get('error', item.get('error_detail'))))
        if messages:
            lines.extend(messages.format_ordinary_errors(child.get('ordinary_errors', [])))
        for section in child.get('output_sections', []):
            lines.append('Section: ' + str(section.get('object', '')) + ' / ' +
                         str(section.get('material', '')) + '; ' + str(section.get('status', 'unverified')).replace('_', ' '))
            lines.extend('  ' + str(name) for name in section.get('shape_names', []))
    if any(review.get(key) for key in ('losses', 'retained_sections', 'conflicts', 'duplicate_groups')):
        lines += ['', 'Reviewed preservation details' if committed else 'Preservation review', '']
        lines += review_lines(review, history=committed)
    lines += ['', 'Technical details', json.dumps(_technical_details(_without_grants(report)),
                                                 indent=2, ensure_ascii=False, default=str)]
    return lines


def _technical_details(value, key=''):
    """Summarize only long numeric index arrays in the presentation copy."""
    index_fields = {'face_ids', 'vertex_ids', 'source_face_ids', 'source_vertex_ids',
                    'face_indices', 'vertex_indices'}
    if (key in index_fields and isinstance(value, (list, tuple)) and len(value) > 100
            and all(type(index) is int for index in value)):
        return dict(count=len(value), sha256=_digest(value),
                    presentation_note='Numeric indices summarized for readability.')
    if isinstance(value, dict):
        return {name: _technical_details(item, name) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_technical_details(item) for item in value]
    return value


def _without_grants(value):
    if isinstance(value, dict):
        return {key: _without_grants(item) for key, item in value.items()
                if key not in TRANSIENT_KEYS and key not in (
                    'plan_token', 'loss_token', 'covered_loss_tokens', 'approved_loss_tokens')}
    if isinstance(value, (list, tuple)):
        return [_without_grants(item) for item in value]
    return value


def remember_result(report, *, context=None):
    report = _without_grants(deepcopy(report))
    report.setdefault('recorded_at', datetime.now(timezone.utc).isoformat())
    key = (report.get('scope', 'direct'), report.get('target_collection', ''))
    _RESULTS.pop(key, None)
    _RESULTS[key] = report
    while len(_RESULTS) > 32:
        _RESULTS.pop(next(iter(_RESULTS)))
    if bpy is not None:
        text = bpy.data.texts.get(DETAILS_NAME) or bpy.data.texts.new(DETAILS_NAME)
        text.clear()
        text.write('\n'.join(details_lines(report)))
        text.cursor_set(0, character=0)
        text.use_fake_user = True
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    return report


def _window_key(context):
    return context.window.as_pointer() if context.window else 0


def cancel_active_review(context):
    """Retire a superseded review even when its replacement fails preflight."""
    request = _REQUESTS.get(_ACTIVE.get(_window_key(context)))
    return bool(request and request.cancel())


def _prune_requests(limit=16):
    if bpy is not None:
        live_windows = {window.as_pointer() for window in bpy.context.window_manager.windows} | {0}
        for window in list(_ACTIVE):
            if window not in live_windows:
                orphan = _REQUESTS.get(_ACTIVE.pop(window))
                if orphan:
                    orphan.cancel()
    protected = set(_ACTIVE.values())
    terminal = [key for key, value in _REQUESTS.items()
                if key not in protected and value.state in ('cancelled', 'published', 'failed')]
    for key in terminal[:-limit]:
        _REQUESTS.pop(key, None)
        for enum_key in list(_ENUM_ITEMS):
            if enum_key[0] == key:
                _ENUM_ITEMS.pop(enum_key, None)


def notify(message, severity='INFO'):
    """Show a final callback's truthful message without holding an old operator."""
    if severity not in ('INFO', 'WARNING', 'ERROR'):
        raise ValueError('Invalid notification severity')
    if bpy is not None:
        try:
            return bpy.ops.re_mesh.sf6_export_notice(message=str(message), severity=severity)
        except RuntimeError as error:
            # Blender propagates an ERROR report as RuntimeError even when the
            # notice operator completed. Do not mistake delivery for failure.
            if severity == 'ERROR' and str(message) in str(error):
                return {'FINISHED'}
            raise


def begin_review(context, *, operation, payload, prepare=standard_prepare, publish,
                 on_result=None, current_payload=None):
    """Start one visible review. Background callers receive a review, not consent."""
    def finish(report):
        remember_result(report, context=context)
        if on_result:
            on_result(_without_grants(deepcopy(report)))
    guard = (lambda: (bpy.context.scene.as_pointer(), bpy.data.filepath)) if bpy is not None else None
    request = ReviewRequest(operation=operation, payload=payload, prepare=prepare,
                            publish=publish, on_result=finish,
                            current_payload=current_payload, context_guard=guard)
    if bpy is not None:
        window = _window_key(context)
        old = _REQUESTS.get(_ACTIVE.get(window))
        if old:
            old.cancel()
        _ACTIVE[window] = request.id
    request.notification_owned = on_result is not None
    _REQUESTS[request.id] = request
    _prune_requests()
    request.prepare()
    if bpy is not None and not bpy.app.background:
        # Skipping the window is the user's standing approval of the listed
        # losses. Anything that needs an actual choice still opens the review.
        if request.state == 'ready' and (skip_review_enabled() or
                                         not request.review.get('requires_loss_approval')):
            request.approve(request.revision)
            if request.state == 'published':
                return {'FINISHED'}
            if request.state == 'failed':
                return {'CANCELLED'}
        bpy.ops.re_mesh.sf6_export_review('INVOKE_DEFAULT', request_id=request.id)
        return {'INTERFACE'}  # Pending review is never counted as a committed export.
    return request


def _preferences():
    """The add-on preferences, or None outside Blender or before registration."""
    if bpy is None:
        return None
    addon = bpy.context.preferences.addons.get((__package__ or '').split('.')[0])
    return getattr(addon, 'preferences', None)


def skip_review_enabled():
    return bool(getattr(_preferences(), 'sf6SkipExportReview', False))


def draw_details_button(layout, *, skip_option=True):
    # Secondary controls: keep them small so they never read as a main choice.
    row = layout.row()
    preferences = _preferences()
    if skip_option and preferences is not None and hasattr(preferences, 'sf6SkipExportReview'):
        row.prop(preferences, 'sf6SkipExportReview', text='Skip review window, export anyway')
    side = row.row()
    side.alignment = 'RIGHT'
    side.operator('re_mesh.sf6_export_details', text='Details', icon='TEXT')


def _confirm_label(request):
    if request.state != 'ready':
        return 'Review updated plan'
    losses = request.review.get('losses', [])
    if any(loss.get('scope') == 'output' for loss in losses):
        return 'Export without blend shapes'
    return 'Export without these shape keys' if request.review.get('requires_loss_approval') else 'Export'


def _is_details_window(context):
    """A separate window that only shows the details report."""
    window = context.window
    windows = context.window_manager.windows
    if window is None or len(windows) < 2 or window == windows[0] or len(window.screen.areas) != 1:
        return False
    space = window.screen.areas[0].spaces.active
    return getattr(getattr(space, 'text', None), 'name', None) == DETAILS_NAME


def _show_details_window(context, text):
    """Open the report in its own window so an open dialog never hides it."""
    def show(area):
        area.type = 'TEXT_EDITOR'
        space = area.spaces.active
        space.text = text
        space.show_word_wrap = True
        space.show_line_numbers = False
        space.show_syntax_highlight = False
        space.top = 0
    manager = context.window_manager
    for window in tuple(manager.windows)[1:]:
        areas = window.screen.areas
        if len(areas) == 1 and getattr(getattr(areas[0].spaces.active, 'text', None), 'name', None) == DETAILS_NAME:
            show(areas[0])
            areas[0].tag_redraw()
            return True
    before = set(manager.windows)
    try:
        bpy.ops.screen.info_log_show('INVOKE_DEFAULT')
    except (AttributeError, RuntimeError):
        return False
    created = [window for window in manager.windows if window not in before]
    if not created or not created[0].screen.areas:
        return False
    show(created[0].screen.areas[0])
    return True


if bpy is not None:
    def _request(value):
        return _REQUESTS.get(value.request_id)

    def _owner_items(self, context):
        request = _request(self)
        if request is None:
            return [('CHOOSE', 'Choose an owner', '')]
        key = (request.id, request.revision, self.source_key)
        if key not in _ENUM_ITEMS:
            group = next((g for g in request.review.get('duplicate_groups', [])
                          if g['source_key'] == self.source_key), {})
            values = [('CHOOSE', 'Choose an owner', 'No choice has been made'),
                      ('NONE', 'None', 'No copy keeps these original corrective shapes')]
            for claimant in group.get('claimants', []):
                if claimant.get('eligible', False):
                    values.append(('P' + str(claimant['object_pointer']), str(claimant['object']),
                                   'Keep verified source correctives on this compatible copy'))
            _ENUM_ITEMS[key] = values
        return _ENUM_ITEMS[key]

    def _owner_update(self, context):
        if _SYNCING:
            return
        request = _request(self)
        if request:
            if self.owner == 'CHOOSE':
                request.clear_owner(self.source_key)
            else:
                request.choose_owner(self.source_key, None if self.owner == 'NONE' else int(self.owner[1:]))

    def _rig_update(self, context):
        if _SYNCING:
            return
        request = _request(self)
        if request:
            request.set_preserve_bone_matrices(self.preserve_bone_matrices)

    class SF6ReviewLine(bpy.types.PropertyGroup):
        text: StringProperty(options={'SKIP_SAVE'})

    class SF6ReviewOwner(bpy.types.PropertyGroup):
        request_id: StringProperty(options={'SKIP_SAVE'})
        source_key: StringProperty(options={'SKIP_SAVE'})
        label: StringProperty(options={'SKIP_SAVE'})
        owner: EnumProperty(name='Keep source correctives on', items=_owner_items,
                            update=_owner_update, options={'SKIP_SAVE'})

    class SF6_UL_review_lines(bpy.types.UIList):
        def draw_item(self, context, layout, data, item, icon, active_data,
                      active_propname, index=0, flt_flag=0):
            layout.label(text=item.text)

    def _sync_rows(context, request):
        global _SYNCING
        _SYNCING = True
        try:
            wm = context.window_manager
            wm.sf6_review_lines.clear()
            for line in _wrapped(review_lines(request.review), 116):
                wm.sf6_review_lines.add().text = line
            wm.sf6_review_line_index = 0
            wm.sf6_detail_lines.clear()
            report = request.result or dict(request.review, scope=request.operation,
                destination=request.payload.get('path', request.payload.get('destination', '')))
            for line in _wrapped(details_lines(report), 116):
                wm.sf6_detail_lines.add().text = line
            wm.sf6_detail_line_index = 0
            wm.sf6_review_owners.clear()
            for group in request.review.get('duplicate_groups', []):
                row = wm.sf6_review_owners.add()
                row.request_id = request.id
                row.source_key = group['source_key']
                row.label = ', '.join(str(c['object']) for c in group.get('claimants', []))
                owners = request.overrides.get('_sf6PreserveSourceOwners', {})
                if group['source_key'] in owners:
                    chosen = owners[group['source_key']]
                    row.owner = 'NONE' if chosen is None else 'P' + str(chosen)
                elif not group.get('requires_choice', True):
                    chosen = group.get('selected_owner')
                    row.owner = 'NONE' if chosen is None else 'P' + str(chosen)
                else:
                    row.owner = 'CHOOSE'
        finally:
            _SYNCING = False

    class SF6_OT_export_review(bpy.types.Operator):
        bl_idname = 're_mesh.sf6_export_review'
        bl_label = 'Review SF6 export'
        bl_options = {'INTERNAL'}
        request_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
        view_nonce: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
        preserve_bone_matrices: BoolProperty(name='Preserve Bone Matrices',
                                             default=True, update=_rig_update,
                                             options={'SKIP_SAVE'})
        show_details: BoolProperty(name='Details', default=False, options={'SKIP_SAVE'},
                                   description='Show the full export report here')
        legacy_popup: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

        def invoke(self, context, event):
            global _SYNCING
            request = _request(self)
            if request is None or request.state in ('cancelled', 'published', 'failed'):
                return {'CANCELLED'}
            self.view_nonce = uuid.uuid4().hex
            request.view_nonce = self.view_nonce
            request.showing_details = False
            request.shown_revision = None
            _sync_rows(context, request)
            _SYNCING = True
            try:
                self.preserve_bone_matrices = request.overrides.get('preserveBoneMatrices',
                    _options(request.payload).get('preserveBoneMatrices', True))
            finally:
                _SYNCING = False
            # A dialog stays open until a button is chosen. A plain popup closes
            # as soon as the pointer leaves it, which used to cancel the export.
            # Cancel is the default button, so Enter never exports.
            try:
                self.legacy_popup = False
                return context.window_manager.invoke_props_dialog(self, width=790,
                    title='Review SF6 export', confirm_text=_confirm_label(request), cancel_default=True)
            except TypeError:
                # Older Blender cannot make Cancel the default button.
                self.legacy_popup = True
                return context.window_manager.invoke_popup(self, width=790)

        def execute(self, context):
            request = _request(self)
            if not request or request.view_nonce != self.view_nonce:
                return {'CANCELLED'}
            if self.legacy_popup:
                # Never an export action. Enter/implicit execution is cancellation.
                request.cancel()
                return {'CANCELLED'}
            if request.state in ('cancelled', 'published', 'failed'):
                return {'CANCELLED'}
            if request.state != 'ready' or getattr(request, 'shown_revision', None) != request.revision:
                # The confirm button only ever approves the plan that was shown.
                request.prepare()
                _reopen(context, request)
                self.report({'WARNING'}, 'Review the updated export plan before exporting.')
                return {'CANCELLED'}
            published = request.approve(request.revision)
            if published and not request.notification_owned:
                self.report({'INFO'}, 'Export completed. Details are available in SF6 Export Details.')
            elif request.state == 'failed' and not request.notification_owned:
                self.report({'ERROR'}, 'Export stopped. Review SF6 Export Details.')
            elif request.state not in ('published', 'failed'):
                _reopen(context, request)
                self.report({'WARNING'}, 'Review the updated export plan before exporting.')
            return {'FINISHED'}

        def cancel(self, context):
            request = _request(self)
            if request and request.view_nonce == self.view_nonce:
                request.cancel()

        def draw(self, context):
            request = _request(self)
            layout = self.layout
            if not request:
                layout.label(text='This export review is no longer available.')
                return
            if request.state in ('cancelled', 'published', 'failed'):
                layout.label(text='Export ' + request.state + '.')
                draw_details_button(layout, skip_option=False)
                return
            if self.legacy_popup:
                layout.label(text='Review SF6 export', icon='EXPORT')
            destination = request.payload.get('path', request.payload.get('destination', request.review.get('destination', '')))
            files = request.review.get('files', request.review.get('report', {}).get('files', []))
            destination_label = ('Destination: ' + str(destination)) if destination else ('Batch export: ' + str(len(files)) + (' file. Destination is listed below.' if len(files) == 1 else ' files. Destinations are listed below.') if files else 'Destination is listed in Details.')
            for line in _wrapped([destination_label], 120):
                layout.label(text=line)
            for owner in context.window_manager.sf6_review_owners:
                box = layout.box()
                for line in _wrapped([owner.label], 90):
                    box.label(text=line)
                box.prop(owner, 'owner')
            if request.review.get('conflicts') or 'preserveBoneMatrices' in request.overrides:
                box = layout.box()
                box.alert = bool(request.review.get('conflicts'))
                box.label(text='Review Preserve Bone Matrices for the edited rest rig.' if box.alert
                          else 'Rig setting for this export')
                for conflict in request.review.get('conflicts', []):
                    if conflict.get('file'):
                        for line in _wrapped(['Applies to: ' + str(conflict['file'])], 88):
                            box.label(text=line)
                box.prop(self, 'preserve_bone_matrices')
            if request.reason in ('plan_changed', 'request_changed'):
                layout.label(text='The scene or export settings changed. Review the updated plan.', icon='ERROR')
            if request.state == 'dirty':
                layout.label(text='Choices changed. Review the updated losses before exporting.', icon='INFO')
            layout.template_list('SF6_UL_review_lines', '', context.window_manager,
                                 'sf6_review_lines', context.window_manager,
                                 'sf6_review_line_index', rows=12, maxrows=12)
            # The dialog's own confirm button approves exactly this revision.
            request.shown_revision = request.revision if request.state == 'ready' else None
            if self.legacy_popup:
                row = layout.row(align=True)
                cancel_column = row.row()
                cancel_column.active_default = True
                cancel = cancel_column.operator('re_mesh.sf6_review_cancel', text='Cancel export', icon='CANCEL')
                cancel.request_id = request.id
            if request.state in ('dirty', 'blocked', 'needs_owner_choice'):
                refresh = layout.operator('re_mesh.sf6_review_refresh', text='Review updated plan', icon='FILE_REFRESH')
                refresh.request_id = request.id
            elif self.legacy_popup:
                column = row.row()
                column.active_default = False
                column.enabled = request.state == 'ready'
                action = column.operator('re_mesh.sf6_review_continue', text=_confirm_label(request), icon='EXPORT')
                action.request_id = request.id
                action.revision = request.revision
            row = layout.row()
            toggle = row.row()
            toggle.alignment = 'LEFT'
            toggle.prop(self, 'show_details', emboss=False,
                        icon='TRIA_DOWN' if self.show_details else 'TRIA_RIGHT')
            side = row.row()
            side.alignment = 'RIGHT'
            details = side.operator('re_mesh.sf6_export_details', text='Open in Window', icon='WINDOW')
            details.request_id = request.id
            if self.show_details:
                layout.template_list('SF6_UL_review_lines', 'details', context.window_manager,
                                     'sf6_detail_lines', context.window_manager,
                                     'sf6_detail_line_index', rows=10, maxrows=10)
            preferences = _preferences()
            if preferences is not None and hasattr(preferences, 'sf6SkipExportReview'):
                layout.prop(preferences, 'sf6SkipExportReview',
                            text='Skip this window next time and export anyway')

    class SF6_OT_review_cancel(bpy.types.Operator):
        bl_idname = 're_mesh.sf6_review_cancel'
        bl_label = 'Cancel export'
        bl_options = {'INTERNAL'}
        request_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

        def execute(self, context):
            request = _request(self)
            if request:
                request.cancel()
            return {'FINISHED'}

    class SF6_OT_review_refresh(bpy.types.Operator):
        bl_idname = 're_mesh.sf6_review_refresh'
        bl_label = 'Review updated plan'
        bl_options = {'INTERNAL'}
        request_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

        def execute(self, context):
            request = _request(self)
            if request:
                request.prepare()
                _sync_rows(context, request)
                if context.area:
                    context.area.tag_redraw()
            return {'FINISHED'}

    class SF6_OT_review_continue(bpy.types.Operator):
        bl_idname = 're_mesh.sf6_review_continue'
        bl_label = 'Export reviewed plan'
        bl_options = {'INTERNAL'}
        request_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
        revision: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})

        def execute(self, context):
            request = _request(self)
            if not request:
                return {'CANCELLED'}
            published = request.approve(self.revision)
            _sync_rows(context, request)
            if published and not request.notification_owned:
                self.report({'INFO'}, 'Export completed. Details are available in SF6 Export Details.')
            elif request.state == 'failed' and not request.notification_owned:
                self.report({'ERROR'}, 'Export stopped. Review SF6 Export Details.')
            elif request.state not in ('published', 'failed'):
                self.report({'WARNING'}, 'Review the updated export plan before exporting.')
            if context.area:
                context.area.tag_redraw()
            return {'FINISHED'}

    class SF6_OT_export_details(bpy.types.Operator):
        bl_idname = 're_mesh.sf6_export_details'
        bl_label = 'SF6 Export Details'
        bl_options = {'INTERNAL'}
        request_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

        def execute(self, context):
            request = _request(self)
            if request:
                report = request.result or dict(request.review, scope=request.operation,
                    destination=request.payload.get('path', request.payload.get('destination', '')))
                remember_result(report, context=context)
            text = bpy.data.texts.get(DETAILS_NAME)
            if not text:
                self.report({'INFO'}, 'No export details are available yet.')
                return {'CANCELLED'}
            # Its own window: turning the area under an open dialog into a
            # Text Editor left the report hidden behind that dialog.
            if _show_details_window(context, text):
                return {'FINISHED'}
            area = context.area
            if area and area.type != 'FILE_BROWSER':
                area.type = 'TEXT_EDITOR'
                area.spaces.active.text = text
                area.spaces.active.show_word_wrap = True
                area.spaces.active.show_line_numbers = False
                area.spaces.active.show_syntax_highlight = False
                area.spaces.active.top = 0
                area.spaces.active.show_region_ui = True
                return {'FINISHED'}
            self.report({'INFO'}, 'Open SF6 Export Details in Blender’s Text Editor.')
            return {'FINISHED'}

    class SF6_PT_export_details(bpy.types.Panel):
        bl_label = 'SF6 Export Details'
        bl_idname = 'SF6_PT_export_details'
        bl_space_type = 'VIEW_3D'
        bl_region_type = 'UI'
        bl_category = 'RE Mesh'

        def draw(self, context):
            request = _REQUESTS.get(_ACTIVE.get(_window_key(context)))
            if request and request.state not in ('cancelled', 'published', 'failed'):
                action = self.layout.operator('re_mesh.sf6_export_review', text='Review pending export', icon='EXPORT')
                action.request_id = request.id
            draw_details_button(self.layout)
            self.layout.label(text='Readable report in Blender’s Text Editor.')

    class SF6_OT_details_return(bpy.types.Operator):
        bl_idname = 're_mesh.sf6_details_return'
        bl_label = 'Back to 3D View'
        bl_options = {'INTERNAL'}

        def execute(self, context):
            if _is_details_window(context):
                bpy.ops.wm.window_close()
                return {'FINISHED'}
            if context.area:
                context.area.type = 'VIEW_3D'
            request = _REQUESTS.get(_ACTIVE.get(_window_key(context)))
            if request and request.state not in ('cancelled', 'published', 'failed'):
                _reopen(context, request)
            return {'FINISHED'}

    class SF6_OT_export_notice(bpy.types.Operator):
        bl_idname = 're_mesh.sf6_export_notice'
        bl_label = 'SF6 Export Result'
        bl_options = {'INTERNAL'}
        message: StringProperty(options={'SKIP_SAVE'})
        severity: EnumProperty(items=[(x, x.title(), '') for x in ('INFO', 'WARNING', 'ERROR')],
                               default='INFO', options={'SKIP_SAVE'})

        def execute(self, context):
            self.report({self.severity}, self.message)
            return {'FINISHED'}

    class SF6_PT_details_text(bpy.types.Panel):
        bl_label = 'SF6 Export Details'
        bl_idname = 'SF6_PT_details_text'
        bl_space_type = 'TEXT_EDITOR'
        bl_region_type = 'UI'
        bl_category = 'SF6'

        @classmethod
        def poll(cls, context):
            return context.space_data.text and context.space_data.text.name == DETAILS_NAME

        def draw(self, context):
            _draw_details_return(self.layout, context)

    def _draw_details_return(layout, context):
        if _is_details_window(context):
            layout.operator('re_mesh.sf6_details_return', text='Close Details', icon='PANEL_CLOSE')
            return
        request = _REQUESTS.get(_ACTIVE.get(_window_key(context)))
        pending = request and request.state not in ('cancelled', 'published', 'failed')
        layout.operator('re_mesh.sf6_details_return',
            text='Back to export review' if pending else 'Back to 3D View', icon='BACK')

    def _details_header(self, context):
        if context.space_data.text and context.space_data.text.name == DETAILS_NAME:
            _draw_details_return(self.layout, context)

    CLASSES = (SF6ReviewLine, SF6ReviewOwner, SF6_UL_review_lines,
               SF6_OT_export_review, SF6_OT_review_cancel, SF6_OT_review_refresh,
               SF6_OT_review_continue, SF6_OT_export_details, SF6_PT_export_details,
               SF6_OT_details_return, SF6_OT_export_notice, SF6_PT_details_text)
else:
    CLASSES = ()


def _reopen(context, request):
    """A child button must not let the old dialog cancel the refreshed plan."""
    if bpy is None or bpy.app.background:
        return
    window = context.window
    area = context.area
    request_id = request.id
    def reopen():
        current = _REQUESTS.get(request_id)
        if current is None or current.state in ('cancelled', 'published', 'failed'):
            return None
        if window not in tuple(bpy.context.window_manager.windows):
            current.cancel()
            return None
        usable = area if area in tuple(window.screen.areas) else next(iter(window.screen.areas), None)
        if usable is not None:
            with bpy.context.temp_override(window=window, area=usable):
                bpy.ops.re_mesh.sf6_export_review('INVOKE_DEFAULT', request_id=request_id)
        return None
    bpy.app.timers.register(reopen, first_interval=0.01)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.sf6_review_lines = CollectionProperty(type=SF6ReviewLine, options={'SKIP_SAVE'})
    bpy.types.WindowManager.sf6_review_line_index = IntProperty(options={'SKIP_SAVE'})
    bpy.types.WindowManager.sf6_review_owners = CollectionProperty(type=SF6ReviewOwner, options={'SKIP_SAVE'})
    bpy.types.WindowManager.sf6_detail_lines = CollectionProperty(type=SF6ReviewLine, options={'SKIP_SAVE'})
    bpy.types.WindowManager.sf6_detail_line_index = IntProperty(options={'SKIP_SAVE'})
    bpy.types.TEXT_HT_header.append(_details_header)


def unregister():
    for request in list(_REQUESTS.values()):
        request.cancel()
    bpy.types.TEXT_HT_header.remove(_details_header)
    for name in ('sf6_review_lines', 'sf6_review_line_index', 'sf6_review_owners',
                 'sf6_detail_lines', 'sf6_detail_line_index'):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _REQUESTS.clear()
    _ACTIVE.clear()
    _ENUM_ITEMS.clear()
