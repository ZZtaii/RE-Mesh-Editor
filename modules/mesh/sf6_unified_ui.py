"""Internal, opt-in operator adapters; no final UI wording or implicit consent.

Runtime adapters return detached dictionaries and never persist settings.
Commit helpers are called only after a successful export (or an explicit
settings-save action). Only workflow and LOD intent may persist. Rig choices,
plan tokens, corrective-loss grants, and reports are execution-scoped.
"""
from collections.abc import Mapping
from copy import deepcopy
import json

if __package__:
    from . import sf6_unified_settings as settings
else:  # Allows the embedded pure tests to run without importing the add-on.
    import sf6_unified_settings as settings

WORKFLOW_KEY = settings.WORKFLOW_KEY
LOD_POLICY_KEY = settings.LOD_POLICY_KEY
BATCH_WORKFLOW_KEY = settings.BATCH_WORKFLOW_KEY
BATCH_LOD_POLICY_KEY = settings.BATCH_LOD_POLICY_KEY
BATCH_PRESERVE_SOURCE = settings.BATCH_PRESERVE_SOURCE
PERSISTENT_FIELDS = (WORKFLOW_KEY, LOD_POLICY_KEY)
RIG_POLICIES = ('auto', 'export_scene_rest')
NEW_PROFILE_DEFAULTS = {
    'autoSolveRepeatedUVs': False,
    'preserveSharpEdges': False,
    'splitLoopVertices': False,
    'preserveBoneMatrices': True,
}
_LAST_RESULTS = {}


class SettingsReviewRequired(ValueError):
    def __init__(self, report):
        self.report = deepcopy(report)
        super().__init__('UNIFIED_SETTINGS_REVIEW_REQUIRED')


def is_sf6(mesh_version):
    return ((type(mesh_version) is int and mesh_version == settings.SF6_VERSION) or
            (type(mesh_version) is str and mesh_version in ('230110883', '.230110883')))


def review_report(reason_codes, *, scope='direct', mode='review_required'):
    return dict(schema=1, status='review_required', scope=scope, mode=mode,
                reason_codes=list(reason_codes), needs_review=True)


def profile_report(result, scope):
    return dict(schema=1, status='review_required' if result.needs_review else 'ready',
                scope=scope, mode=result.mode, legacy_mode=result.legacy_mode,
                reason_codes=list(result.reason_codes), needs_review=result.needs_review,
                changed=result.changed)


def apply_new_profile_defaults(options, *, has_saved_profile, explicit=(), enable_unified=False):
    """Apply approved defaults only to a genuinely new unified preserve profile.

    Callers own profile existence and explicit-field provenance. Even an empty
    or partial saved profile counts as existing. Defaults are never written by
    this helper. Explicit Ordinary and explicit per-field choices stay intact.
    """
    output = deepcopy(dict(options))
    if type(has_saved_profile) is not bool or type(enable_unified) is not bool:
        raise TypeError('Profile existence and opt-in must be booleans')
    requested = enable_unified or output.get(WORKFLOW_KEY) == settings.UNIFIED_WORKFLOW
    preserve = output.get('exportBlendShapes', True)
    ordinary = type(preserve) in (bool, int) and preserve == 0
    if has_saved_profile or not requested or ordinary:
        return output
    applied = []
    for key, default in NEW_PROFILE_DEFAULTS.items():
        if key not in explicit:
            output[key] = default
            applied.append(key)
    output['_sf6NewProfileDefaults'] = applied
    return output


def resolve_profile(values=None, *, mesh_version, scope='direct', enable_unified=False):
    """Resolve raw stored values before filling operator defaults.

    Empty *stored* workflow/policy values are unknown, not an opt-out. Runtime
    empty hidden strings are handled separately by resolve_options. No fields
    are written by this function. Rig fields may be validated for callers but
    are never included in this adapter's persistence allowlist.
    """
    if values is not None and not isinstance(values, Mapping):
        return settings.MigrationResult({}, 'invalid', 'review_required',
                                        ('invalid_profile_type',), True, False)
    original = dict(values or {})
    candidate = deepcopy(original)
    retired = (is_sf6(mesh_version) and scope != 'batch' and
               candidate.get(WORKFLOW_KEY) == settings.UNIFIED_WORKFLOW and
               candidate.get('sf6HybridPreserve') is True)
    if retired:
        candidate['sf6HybridPreserve'] = False
    result = settings.migrate_settings(candidate, scope=scope, enable_unified=enable_unified,
                                       mesh_version=mesh_version)
    if not is_sf6(mesh_version):
        return result
    raw = original
    issues = []
    policy_key = BATCH_LOD_POLICY_KEY if scope == 'batch' else LOD_POLICY_KEY
    if policy_key in raw and (type(raw[policy_key]) is not str or raw[policy_key] not in
                              (settings.RETAIN_COMPATIBLE, settings.LOD0_ONLY)):
        issues.append('unrecognized_lod_policy')
    if scope != 'batch' and 'sf6RigPolicy' in raw and (
            type(raw['sf6RigPolicy']) is not str or raw['sf6RigPolicy'] not in RIG_POLICIES):
        issues.append('unrecognized_rig_policy')
    if issues:
        copied = deepcopy(raw)
        key = BATCH_WORKFLOW_KEY if scope == 'batch' else WORKFLOW_KEY
        if copied.get(key) == settings.UNIFIED_WORKFLOW:
            copied.pop(key)
        reasons = tuple(dict.fromkeys(result.reason_codes + tuple(issues)))
        return settings.MigrationResult(copied, result.legacy_mode, 'review_required',
                                        reasons, True, copied != raw)
    if retired:
        return settings.MigrationResult(result.settings, result.legacy_mode, result.mode,
            result.reason_codes + ('retired_legacy_hybrid_flag',), result.needs_review,
            result.settings != original)
    return result


def resolve_options(options, *, mesh_version, scope='direct', enable_unified=False):
    """Return runtime options, or raise a structured review before dispatch.

    Batch callers must use resolve_batch_options so only their namespace is
    interpreted. No browser, Blender, or filesystem dependency is used here.
    """
    if scope == 'batch':
        raise ValueError('Batch options require resolve_batch_options')
    output = deepcopy(dict(options))
    if not is_sf6(mesh_version):
        # A stale SF6-only marker must never activate dispatch for other games.
        for key in PERSISTENT_FIELDS + ('sf6RigPolicy', '_sf6UnifiedExpectedPlan',
                                        '_sf6ApprovedCorrectiveLosses'):
            output.pop(key, None)
        output['_sf6UnifiedSettingsReport'] = dict(schema=1, status='ready', scope=scope,
                                                   mode='not_sf6', needs_review=False,
                                                   reason_codes=['not_sf6'])
        return output
    for key in PERSISTENT_FIELDS:
        if output.get(key) == '':
            output.pop(key)
    if output.get('sf6RigPolicy') == '':
        output.pop('sf6RigPolicy')
    result = resolve_profile(output, mesh_version=mesh_version, scope=scope,
                             enable_unified=enable_unified)
    report = profile_report(result, scope)
    if result.needs_review:
        raise SettingsReviewRequired(report)
    requested = enable_unified or output.get(WORKFLOW_KEY) == settings.UNIFIED_WORKFLOW
    output = result.settings
    if result.mode == 'unified':
        # Derive the old Hybrid LOD0 policy first, then retire its route flag.
        output['sf6HybridPreserve'] = False
    elif result.mode == 'ordinary' and requested:
        # Ordinary remains the preservation intent, but an opted-in execution
        # still gets unified atomic publication and stale-plan validation.
        output[WORKFLOW_KEY] = settings.UNIFIED_WORKFLOW
        output.setdefault(LOD_POLICY_KEY, settings.RETAIN_COMPATIBLE if output.get('exportAllLODs', True) else settings.LOD0_ONLY)
        output['sf6HybridPreserve'] = False
        report['runtime_unified_wrapper'] = True
    output['_sf6UnifiedSettingsReport'] = report
    if output.get('_sf6NewProfileDefaults'):
        report['new_profile_defaults'] = list(output['_sf6NewProfileDefaults'])
    output['_sf6SettingsScope'] = scope
    return output


def resolve_batch_options(collection, options, *, mesh_version, enable_unified=False):
    """Read only independently owned batch settings; explicit row values win."""
    output = deepcopy(dict(options))
    if not is_sf6(mesh_version):
        output = resolve_options(output, mesh_version=mesh_version)
        output['_sf6UnifiedSettingsReport']['scope'] = 'batch'
        output['_sf6SettingsScope'] = 'batch'
        return output
    source = collection if collection is not None else {}
    values = {key: deepcopy(source[key]) for key in
              (BATCH_PRESERVE_SOURCE, BATCH_WORKFLOW_KEY, BATCH_LOD_POLICY_KEY)
              if key in source}
    # Explicit row/operator arguments are authorized only in this batch scope.
    if 'exportBlendShapes' in output:
        values[BATCH_PRESERVE_SOURCE] = output['exportBlendShapes']
    for runtime, stored in ((WORKFLOW_KEY, BATCH_WORKFLOW_KEY),
                            (LOD_POLICY_KEY, BATCH_LOD_POLICY_KEY)):
        if runtime in output and output[runtime] != '':
            values[stored] = output[runtime]
    result = resolve_profile(values, mesh_version=mesh_version, scope='batch',
                             enable_unified=enable_unified)
    report = profile_report(result, 'batch')
    if result.needs_review:
        raise SettingsReviewRequired(report)
    for key in PERSISTENT_FIELDS:
        output.pop(key, None)
    requested = enable_unified or values.get(BATCH_WORKFLOW_KEY) == settings.UNIFIED_WORKFLOW
    if result.mode == 'unified' or (result.mode == 'ordinary' and requested):
        output[WORKFLOW_KEY] = settings.UNIFIED_WORKFLOW
        output[LOD_POLICY_KEY] = result.settings.get(BATCH_LOD_POLICY_KEY, settings.RETAIN_COMPATIBLE)
        if result.mode == 'ordinary':
            report['runtime_unified_wrapper'] = True
    # Keep missing batch preservation Ordinary unless this same batch was
    # explicitly opted into unified. Never read the shared historical flag.
    output['exportBlendShapes'] = bool(values.get(BATCH_PRESERVE_SOURCE,
                                                result.mode == 'unified'))
    output['sf6HybridPreserve'] = False
    output['_sf6UnifiedSettingsReport'] = report
    output['_sf6SettingsScope'] = 'batch'
    return output


def _checked_persistent(options):
    marker = options.get(WORKFLOW_KEY, '')
    policy = options.get(LOD_POLICY_KEY, '')
    if marker not in ('', settings.UNIFIED_WORKFLOW):
        raise SettingsReviewRequired(review_report(['unrecognized_workflow']))
    if policy not in ('', settings.RETAIN_COMPATIBLE, settings.LOD0_ONLY):
        raise SettingsReviewRequired(review_report(['unrecognized_lod_policy']))
    if options.get('_sf6UnifiedSettingsReport', {}).get('needs_review'):
        raise SettingsReviewRequired(options['_sf6UnifiedSettingsReport'])
    if options.get('_sf6UnifiedSettingsReport', {}).get('mode') == 'ordinary':
        return '', ''
    return marker, policy


def commit_operator_settings(operator, options):
    """Success-only commit of safe workflow intent, never execution grants."""
    marker, policy = _checked_persistent(options)
    for key, value in ((WORKFLOW_KEY, marker), (LOD_POLICY_KEY, policy)):
        if hasattr(operator, key):
            setattr(operator, key, value if marker else '')
    if (hasattr(operator, 'sf6HybridPreserve') and
            options.get('_sf6UnifiedSettingsReport', {}).get('mode') in ('unified', 'ordinary')):
        operator.sf6HybridPreserve = False
    for key in options.get('_sf6NewProfileDefaults', ()):
        if key in NEW_PROFILE_DEFAULTS and hasattr(operator, key) and type(options.get(key)) is bool:
            setattr(operator, key, options[key])
    return {key: value for key, value in ((WORKFLOW_KEY, marker), (LOD_POLICY_KEY, policy))
            if value and marker}


apply_resolved = commit_operator_settings


def commit_batch_settings(collection, options):
    """Persist only independently owned marker/policy after successful export."""
    if options.get('_sf6UnifiedSettingsReport', {}).get('mode') == 'not_sf6':
        return {}
    marker, policy = _checked_persistent(options)
    if collection is None:
        return {}
    if marker:
        collection[BATCH_WORKFLOW_KEY] = marker
        collection[BATCH_LOD_POLICY_KEY] = policy or settings.RETAIN_COMPATIBLE
        return {BATCH_WORKFLOW_KEY: marker,
                BATCH_LOD_POLICY_KEY: collection[BATCH_LOD_POLICY_KEY]}
    for key in (BATCH_WORKFLOW_KEY, BATCH_LOD_POLICY_KEY):
        if key in collection:
            del collection[key]
    return {}


def apply_review_inputs(operator, options):
    """Validate current invocation's grants; never infer or persist consent."""
    output = deepcopy(dict(options))
    scope = output.get('_sf6SettingsScope', getattr(operator, 'sf6SettingsScope', 'direct'))
    # Never carry grants over from a reused output/options dictionary. The
    # current invocation's hidden properties are the sole handoff source.
    for key in ('sf6RigPolicy', '_sf6ApprovedCorrectiveLosses', '_sf6UnifiedExpectedPlan'):
        output.pop(key, None)
    if output.get('_sf6UnifiedSettingsReport', {}).get('mode') == 'not_sf6':
        return output
    rig = getattr(operator, 'sf6RigPolicy', '')
    if rig:
        if type(rig) is not str or rig not in RIG_POLICIES:
            raise SettingsReviewRequired(review_report(['unrecognized_rig_policy'], scope=scope))
        output['sf6RigPolicy'] = rig
    raw = getattr(operator, 'sf6ApprovedCorrectiveLosses', '')
    if raw:
        try:
            tokens = json.loads(raw)
        except (ValueError, TypeError):
            raise SettingsReviewRequired(review_report(['invalid_corrective_loss_grants_json'], scope=scope))
        if (not isinstance(tokens, list) or any(type(token) is not str or not token
                                               for token in tokens)):
            raise SettingsReviewRequired(review_report(['invalid_corrective_loss_grants'], scope=scope))
        output['_sf6ApprovedCorrectiveLosses'] = list(dict.fromkeys(tokens))
    expected = getattr(operator, 'sf6ExpectedPlan', '')
    if expected:
        if type(expected) is not str:
            raise SettingsReviewRequired(review_report(['invalid_expected_plan'], scope=scope))
        output['_sf6UnifiedExpectedPlan'] = expected
    return output


def record_result(operator, options, error=None):
    """Capture structured status on the operator and an execution-only cache."""
    settings_report = deepcopy(options.get('_sf6UnifiedSettingsReport', {}))
    report = deepcopy(options.get('_sf6UnifiedReport', {}))
    if not isinstance(report, dict):
        report = {}
    report.setdefault('schema', 1)
    report.setdefault('status', 'ready' if error is None else 'not_committed')
    if settings_report:
        report['settings'] = settings_report
    scope = options.get('_sf6SettingsScope', getattr(operator, 'sf6SettingsScope', 'direct'))
    if error is not None:
        report['error_code'] = type(error).__name__
        report['error_detail'] = str(error)
        if hasattr(error, 'report') and isinstance(error.report, dict):
            report['settings'] = deepcopy(error.report)
            report['status'] = 'review_required'
            scope = error.report.get('scope', scope)
        if hasattr(error, 'plan'):
            report.update(deepcopy(error.plan))
            report['status'] = 'needs_policy_choice'
        if hasattr(error, 'conflicts'):
            report['conflicts'] = deepcopy(error.conflicts)
        if hasattr(error, 'loss_token'):
            report['status'] = 'needs_corrective_loss_consent'
            report['corrective_loss'] = dict(token=error.loss_token,
                object=getattr(error, 'object_name', ''), reason=getattr(error, 'reason', ''),
                shape_names=list(getattr(error, 'shape_names', ())))
        if str(error) in ('UNIFIED_STALE_PLAN', 'UNIFIED_PLAN_CHANGED'):
            report['reason'] = 'stale_plan'
            report['status'] = 'not_committed'
    report['scope'] = scope
    target = options.get('targetCollection', getattr(operator, 'targetCollection', ''))
    report['target_collection'] = target
    operator.sf6UnifiedResult = json.dumps(report, sort_keys=True)
    _LAST_RESULTS[(scope, target)] = deepcopy(report)
    return report


def get_result(scope, target_collection):
    return deepcopy(_LAST_RESULTS.get((scope, target_collection)))


def pop_result(scope, target_collection):
    return deepcopy(_LAST_RESULTS.pop((scope, target_collection), None))


def clear_result(scope, target_collection):
    _LAST_RESULTS.pop((scope, target_collection), None)


def _run_tests():
    """Pure adapter + isolated direct persistence tests, without Blender."""
    import importlib.util
    from pathlib import Path
    import sys
    import tempfile
    from types import ModuleType, SimpleNamespace
    import unittest
    from unittest.mock import patch

    package_name = '_sf6_unified_ui_tests'
    package = ModuleType(package_name)
    package.__path__ = [str(Path(__file__).parent)]
    sys.modules[package_name] = package
    sys.modules[package_name + '.sf6_unified_ui'] = sys.modules[__name__]
    fake_bpy = SimpleNamespace(data=SimpleNamespace(collections={}),
                               utils=SimpleNamespace(user_resource=lambda _: '/tmp'))
    previous_bpy = sys.modules.get('bpy')
    sys.modules['bpy'] = fake_bpy
    spec = importlib.util.spec_from_file_location(package_name + '.direct_export_settings',
                                                  Path(__file__).with_name('direct_export_settings.py'))
    direct = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = direct
    spec.loader.exec_module(direct)

    class FakeOperator(SimpleNamespace):
        def report(self, level, message):
            self.messages.append((level, message))

    def operator(**overrides):
        values = {key: False for key in direct.FIELDS}
        values.update(exportBlendShapes=True, exportAllLODs=True, rotate90=True,
                      splitLoopVertices=True, normalizeWeights=True, limitTotalCount=6,
                      shapeKeyExportMode='MODE0', rememberFields=True,
                      targetCollection='mesh', filename_ext='.230110883', filepath='chosen.mesh.230110883',
                      sf6ExportWorkflow='', sf6LodPolicy='', sf6RigPolicy='',
                      sf6ApprovedCorrectiveLosses='', sf6ExpectedPlan='', sf6UnifiedResult='',
                      sf6SettingsScope='direct', messages=[])
        props = {key: SimpleNamespace(type='BOOLEAN') for key in direct.FIELDS}
        props['limitTotalCount'] = SimpleNamespace(type='INT', hard_min=1, hard_max=32)
        props['shapeKeyExportMode'] = SimpleNamespace(type='ENUM', enum_items={'NO': 0, 'MODE0': 1, 'MODE1': 2})
        values['bl_rna'] = SimpleNamespace(properties=props)
        values.update(overrides)
        return FakeOperator(**values)

    def runtime(op):
        result = {key: getattr(op, key) for key in direct.FIELDS}
        result['targetCollection'] = op.targetCollection
        for key in PERSISTENT_FIELDS:
            if getattr(op, key):
                result[key] = getattr(op, key)
        return result

    class AdapterTests(unittest.TestCase):
        def test_new_profile_defaults_are_opt_in_preserve_only(self):
            old = dict(autoSolveRepeatedUVs=True, preserveSharpEdges=True,
                       splitLoopVertices=True, preserveBoneMatrices=False)
            for preserve in (False, 0):
                source = dict(old, exportBlendShapes=preserve)
                self.assertEqual(apply_new_profile_defaults(source, has_saved_profile=False,
                                                            enable_unified=True), source)
            self.assertEqual(apply_new_profile_defaults(old, has_saved_profile=False), old)
            output = apply_new_profile_defaults(dict(old, exportBlendShapes=True),
                                                 has_saved_profile=False, enable_unified=True)
            for key, value in NEW_PROFILE_DEFAULTS.items():
                self.assertEqual(output[key], value)
            self.assertEqual(old['preserveBoneMatrices'], False)

        def test_existing_profiles_and_explicit_fields_keep_their_choices(self):
            old = dict(autoSolveRepeatedUVs=True, preserveSharpEdges=True,
                       splitLoopVertices=True, preserveBoneMatrices=False)
            self.assertEqual(apply_new_profile_defaults(old, has_saved_profile=True,
                                                        enable_unified=True), old)
            output = apply_new_profile_defaults(old, has_saved_profile=False,
                explicit={'autoSolveRepeatedUVs', 'preserveBoneMatrices'}, enable_unified=True)
            self.assertTrue(output['autoSolveRepeatedUVs'])
            self.assertFalse(output['preserveBoneMatrices'])
            self.assertFalse(output['splitLoopVertices'])
            self.assertEqual(output['_sf6NewProfileDefaults'], ['preserveSharpEdges', 'splitLoopVertices'])

        def test_new_defaults_commit_only_the_resolved_metadata_fields(self):
            op = operator(autoSolveRepeatedUVs=True, splitLoopVertices=True,
                          preserveSharpEdges=True, preserveBoneMatrices=False)
            output = apply_new_profile_defaults(runtime(op), has_saved_profile=False,
                                                explicit={'autoSolveRepeatedUVs'}, enable_unified=True)
            output = resolve_options(output, mesh_version=settings.SF6_VERSION, enable_unified=True)
            self.assertTrue(op.splitLoopVertices)
            self.assertFalse(op.preserveBoneMatrices)
            commit_operator_settings(op, output)
            self.assertTrue(op.autoSolveRepeatedUVs)
            self.assertFalse(op.splitLoopVertices)
            self.assertTrue(op.preserveBoneMatrices)

        def test_legacy_requires_opt_in(self):
            for hybrid in (False, True):
                source = dict(exportBlendShapes=True, sf6HybridPreserve=hybrid, selectedOnly=False)
                actual = resolve_options(source, mesh_version=settings.SF6_VERSION)
                self.assertNotIn(WORKFLOW_KEY, actual)
                chosen = resolve_options(source, mesh_version=settings.SF6_VERSION, enable_unified=True)
                self.assertEqual(chosen[WORKFLOW_KEY], 'unified_v1')
                self.assertEqual(chosen[LOD_POLICY_KEY], 'lod0' if hybrid else 'retain_if_compatible')
                self.assertNotIn(WORKFLOW_KEY, source)

        def test_ordinary_is_not_promoted(self):
            output = resolve_options(dict(exportBlendShapes=False, sf6ExportWorkflow='unified_v1'),
                                     mesh_version=settings.SF6_VERSION, enable_unified=True)
            self.assertFalse(output['exportBlendShapes'])
            self.assertEqual(output[WORKFLOW_KEY], 'unified_v1')
            self.assertEqual(output['_sf6UnifiedSettingsReport']['mode'], 'ordinary')
            self.assertTrue(output['_sf6UnifiedSettingsReport']['runtime_unified_wrapper'])
            op = operator(sf6ExportWorkflow='unified_v1')
            commit_operator_settings(op, output)
            self.assertEqual(op.sf6ExportWorkflow, '')
            legacy = resolve_options(dict(exportBlendShapes=False), mesh_version=settings.SF6_VERSION)
            self.assertNotIn(WORKFLOW_KEY, legacy)

        def test_known_unified_to_ordinary_retires_obsolete_hybrid_flag(self):
            output = resolve_options(dict(exportBlendShapes=False, sf6HybridPreserve=True,
                                          sf6ExportWorkflow='unified_v1', sf6LodPolicy='lod0'),
                                     mesh_version=settings.SF6_VERSION)
            self.assertFalse(output['exportBlendShapes'])
            self.assertFalse(output['sf6HybridPreserve'])
            self.assertEqual(output[WORKFLOW_KEY], 'unified_v1')
            self.assertIn('retired_legacy_hybrid_flag',
                          output['_sf6UnifiedSettingsReport']['reason_codes'])
            with self.assertRaises(SettingsReviewRequired):
                resolve_options(dict(exportBlendShapes=False, sf6HybridPreserve=True),
                                mesh_version=settings.SF6_VERSION, enable_unified=True)

        def test_migrated_hybrid_keeps_lod0_but_retires_route_flag(self):
            output = resolve_options(dict(exportBlendShapes=True, sf6HybridPreserve=True),
                                     mesh_version=settings.SF6_VERSION, enable_unified=True)
            self.assertEqual(output[LOD_POLICY_KEY], 'lod0')
            self.assertFalse(output['sf6HybridPreserve'])
            op = operator(sf6HybridPreserve=True)
            commit_operator_settings(op, output)
            self.assertFalse(op.sf6HybridPreserve)

        def test_raw_unknown_empty_markers_require_review(self):
            for key in PERSISTENT_FIELDS:
                for value in ('', 'future_v2', None, 1):
                    self.assertTrue(resolve_profile({key: value}, mesh_version=settings.SF6_VERSION).needs_review)
            runtime_result = resolve_options(dict(sf6ExportWorkflow='', sf6LodPolicy=''),
                                             mesh_version=settings.SF6_VERSION)
            self.assertFalse(runtime_result['_sf6UnifiedSettingsReport']['needs_review'])

        def test_malformed_profile_types_require_review(self):
            for value in ([], [], 0, False, 'profile'):
                result = resolve_profile(value, mesh_version=settings.SF6_VERSION)
                self.assertTrue(result.needs_review)
                self.assertIn('invalid_profile_type', result.reason_codes)

        def test_unknown_rig_is_review_only_for_sf6(self):
            self.assertTrue(resolve_profile({'sf6RigPolicy': 'future'}, mesh_version=settings.SF6_VERSION).needs_review)
            output = resolve_options(dict(sf6ExportWorkflow='future', sf6RigPolicy='future',
                                          _sf6UnifiedExpectedPlan='stale'), mesh_version=241111606)
            output = apply_review_inputs(operator(sf6RigPolicy='future', sf6ApprovedCorrectiveLosses='bad'), output)
            self.assertNotIn(WORKFLOW_KEY, output)
            self.assertNotIn('sf6RigPolicy', output)
            self.assertNotIn('_sf6UnifiedExpectedPlan', output)

        def test_review_input_validation_and_no_carry_over(self):
            op = operator(sf6ApprovedCorrectiveLosses='["token-a", "token-a"]',
                          sf6ExpectedPlan='plan', sf6RigPolicy='export_scene_rest')
            output = apply_review_inputs(op, {})
            self.assertEqual(output['_sf6ApprovedCorrectiveLosses'], ['token-a'])
            self.assertEqual(output['_sf6UnifiedExpectedPlan'], 'plan')
            self.assertEqual(output['sf6RigPolicy'], 'export_scene_rest')
            cleared = apply_review_inputs(operator(), output)
            for key in ('sf6RigPolicy', '_sf6ApprovedCorrectiveLosses', '_sf6UnifiedExpectedPlan'):
                self.assertNotIn(key, cleared)
            for raw in ('bad', '{}', '[1]', '[""]', 'true'):
                with self.assertRaises(SettingsReviewRequired):
                    apply_review_inputs(operator(sf6ApprovedCorrectiveLosses=raw), {})

        def test_batch_ignores_shared_and_direct_storage(self):
            collection = {'BatchExport_exportBlendShapes': True,
                          'BatchExport_sf6ExportWorkflow': 'unified_v1',
                          'BatchExport_sf6LodPolicy': 'lod0', WORKFLOW_KEY: 'unified_v1'}
            output = resolve_batch_options(collection, {}, mesh_version=settings.SF6_VERSION)
            self.assertFalse(output['exportBlendShapes'])
            self.assertNotIn(WORKFLOW_KEY, output)
            self.assertNotIn(BATCH_WORKFLOW_KEY, collection)

        def test_batch_explicit_row_overrides_and_success_commit(self):
            collection = {BATCH_PRESERVE_SOURCE: False}
            output = resolve_batch_options(collection,
                dict(exportBlendShapes=True, sf6ExportWorkflow='unified_v1', sf6LodPolicy='lod0'),
                mesh_version=settings.SF6_VERSION)
            self.assertEqual(output[WORKFLOW_KEY], 'unified_v1')
            self.assertFalse(collection[BATCH_PRESERVE_SOURCE])
            output['_sf6ApprovedCorrectiveLosses'] = ['never-save']
            output['sf6RigPolicy'] = 'export_scene_rest'
            commit_batch_settings(collection, output)
            self.assertEqual(collection[BATCH_WORKFLOW_KEY], 'unified_v1')
            self.assertEqual(collection[BATCH_LOD_POLICY_KEY], 'lod0')
            self.assertEqual(len(collection), 3)
            ordinary = resolve_batch_options(collection, {'exportBlendShapes': False},
                                               mesh_version=settings.SF6_VERSION)
            self.assertEqual(ordinary[WORKFLOW_KEY], 'unified_v1')
            self.assertEqual(ordinary['_sf6UnifiedSettingsReport']['mode'], 'ordinary')
            commit_batch_settings(collection, ordinary)
            self.assertEqual(collection, {BATCH_PRESERVE_SOURCE: False})

        def test_batch_unknown_owned_marker_never_downgrades(self):
            collection = {BATCH_WORKFLOW_KEY: 'future_v2'}
            with self.assertRaises(SettingsReviewRequired):
                resolve_batch_options(collection, {}, mesh_version=settings.SF6_VERSION)
            self.assertEqual(collection[BATCH_WORKFLOW_KEY], 'future_v2')

        def test_non_sf6_batch_success_preserves_sf6_stored_profile(self):
            collection = {BATCH_PRESERVE_SOURCE: True, BATCH_WORKFLOW_KEY: 'unified_v1',
                          BATCH_LOD_POLICY_KEY: 'lod0'}
            before = deepcopy(collection)
            output = resolve_batch_options(collection, {}, mesh_version=241111606)
            commit_batch_settings(collection, output)
            self.assertEqual(collection, before)

        def test_result_transport_is_transient_and_detached(self):
            op = operator(sf6SettingsScope='batch')
            error = SettingsReviewRequired(review_report(['unknown'], scope='batch'))
            report = record_result(op, {'targetCollection': 'mesh'}, error)
            self.assertEqual(json.loads(op.sf6UnifiedResult)['status'], 'review_required')
            report['status'] = 'changed'
            self.assertEqual(get_result('batch', 'mesh')['status'], 'review_required')
            get_result('batch', 'mesh')['settings']['reason_codes'].append('changed')
            self.assertEqual(pop_result('batch', 'mesh')['settings']['reason_codes'], ['unknown'])
            self.assertIsNone(get_result('batch', 'mesh'))

        def test_result_preserves_policy_loss_and_stale_plan_status(self):
            op = operator()
            error = ValueError('loss')
            error.loss_token = 'token'
            error.object_name = 'part'
            error.reason = 'reason'
            error.shape_names = ('a', 'b')
            report = record_result(op, {}, error)
            self.assertEqual(report['corrective_loss']['token'], 'token')
            self.assertEqual(report['status'], 'needs_corrective_loss_consent')
            conflict = ValueError('conflict')
            conflict.plan = {'route': 'ordinary_rig', 'plan_token': 'plan'}
            conflict.conflicts = [{'code': 'rig'}]
            report = record_result(op, {}, conflict)
            self.assertEqual(report['status'], 'needs_policy_choice')
            self.assertEqual(report['plan_token'], 'plan')
            report = record_result(op, {'_sf6UnifiedReport': {'status': 'not_committed'}},
                                   ValueError('UNIFIED_STALE_PLAN'))
            self.assertEqual(report['reason'], 'stale_plan')

    class DirectSettingsTests(unittest.TestCase):
        def setUp(self):
            self.temporary = tempfile.TemporaryDirectory(prefix='sf6-direct-settings-')
            self.path = Path(self.temporary.name) / 'settings.json'
            self.path_patch = patch.object(direct, 'settings_path', lambda: self.path)
            self.path_patch.start()
            fake_bpy.data.collections = {'mesh': {}}

        def tearDown(self):
            self.path_patch.stop()
            self.temporary.cleanup()

        def write(self, values, **extra):
            data = dict(enabled=True, versions={'.230110883': values}, **extra)
            self.path.write_text(json.dumps(data))
            return data

        def local(self, values, version='.230110883'):
            fake_bpy.data.collections['mesh'][direct.KEY] = json.dumps(
                dict(version=version, values=values, filepath='collection.mesh.230110883'))

        def test_new_programmatic_defaults_and_explicit_rna_values(self):
            op = operator(autoSolveRepeatedUVs=True, preserveSharpEdges=True,
                          splitLoopVertices=True, preserveBoneMatrices=False)
            op.properties = SimpleNamespace(is_property_set=lambda key: key == 'autoSolveRepeatedUVs')
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            self.assertTrue(output['autoSolveRepeatedUVs'])
            self.assertFalse(output['preserveSharpEdges'])
            self.assertFalse(output['splitLoopVertices'])
            self.assertTrue(output['preserveBoneMatrices'])
            self.assertFalse(self.path.exists())
            self.assertTrue(op.splitLoopVertices)

        def test_new_invoke_uses_original_explicit_keys_not_default_assignment(self):
            op = operator(sf6UnifiedOptIn=True, autoSolveRepeatedUVs=True,
                          preserveSharpEdges=True, splitLoopVertices=True, preserveBoneMatrices=False)
            # invoke's default assignment has already marked every RNA field.
            op.properties = SimpleNamespace(bl_rna=op.bl_rna, is_property_set=lambda key: True)
            direct.load(op, None, explicit={'sf6UnifiedOptIn': True, 'autoSolveRepeatedUVs': True})
            self.assertTrue(op.autoSolveRepeatedUVs)
            self.assertFalse(op.preserveSharpEdges)
            self.assertFalse(op.splitLoopVertices)
            self.assertTrue(op.preserveBoneMatrices)
            self.assertFalse(self.path.exists())
            self.assertNotIn(direct.KEY, fake_bpy.data.collections['mesh'])
            op.splitLoopVertices = True  # A real edit after the display load.
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            self.assertTrue(output['splitLoopVertices'])
            self.assertNotIn('splitLoopVertices', output['_sf6NewProfileDefaults'])

        def test_partial_and_empty_saved_profiles_suppress_new_defaults(self):
            for existing in ({}, {'exportBlendShapes': True}, {'selectedOnly': False}):
                for remember in (False, True):
                    with self.subTest(existing=existing, remember=remember):
                        self.write(existing)
                        op = operator(rememberFields=remember, sf6UnifiedOptIn=True,
                                      autoSolveRepeatedUVs=True, preserveSharpEdges=True,
                                      splitLoopVertices=True, preserveBoneMatrices=False)
                        direct.load(op, None, explicit={'rememberFields': remember, 'sf6UnifiedOptIn': True})
                        self.assertTrue(op.autoSolveRepeatedUVs)
                        self.assertTrue(op.preserveSharpEdges)
                        self.assertTrue(op.splitLoopVertices)
                        self.assertFalse(op.preserveBoneMatrices)

        def test_new_defaults_success_persists_actual_used_values_only(self):
            op = operator(autoSolveRepeatedUVs=True, preserveSharpEdges=True,
                          splitLoopVertices=True, preserveBoneMatrices=False)
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            self.assertFalse(self.path.exists())
            commit_operator_settings(op, output)
            saved = direct.save(op, None, migration_success=True)
            self.assertTrue(saved['persisted'])
            stored = json.loads(self.path.read_text())['versions']['.230110883']
            for key, value in NEW_PROFILE_DEFAULTS.items():
                self.assertEqual(stored[key], value)
            self.assertNotIn('_sf6NewProfileDefaults', stored)

        def test_explicit_ordinary_opt_in_keeps_normal_processing_defaults(self):
            op = operator(exportBlendShapes=False, autoSolveRepeatedUVs=True,
                          preserveSharpEdges=True, splitLoopVertices=True, preserveBoneMatrices=False)
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            self.assertEqual(output[WORKFLOW_KEY], 'unified_v1')
            self.assertEqual(output['_sf6UnifiedSettingsReport']['mode'], 'ordinary')
            self.assertNotIn('_sf6NewProfileDefaults', output)
            self.assertTrue(output['autoSolveRepeatedUVs'])
            self.assertTrue(output['preserveSharpEdges'])
            self.assertTrue(output['splitLoopVertices'])
            self.assertFalse(output['preserveBoneMatrices'])

        def test_priority_explicit_over_collection_over_disk(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False, rotate90=False))
            self.local(dict(exportBlendShapes=False, selectedOnly=True))
            op = operator(exportBlendShapes=True)
            direct.load(op, None, explicit={'exportBlendShapes': True})
            self.assertTrue(op.exportBlendShapes)
            self.assertTrue(op.selectedOnly)
            self.assertFalse(op.rotate90)
            self.assertEqual(op.filepath, 'collection.mesh.230110883')
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION)
            self.assertTrue(output['exportBlendShapes'])

        def test_explicit_filepath_and_remember_override(self):
            self.write(dict(exportBlendShapes=False))
            disk = json.loads(self.path.read_text())
            disk['enabled'] = False
            self.path.write_text(json.dumps(disk))
            self.local(dict(exportBlendShapes=False))
            op = operator(rememberFields=True)
            direct.load(op, None, explicit={'rememberFields': True, 'filepath': op.filepath})
            self.assertTrue(op.rememberFields)
            self.assertFalse(op.exportBlendShapes)
            self.assertEqual(op.filepath, 'chosen.mesh.230110883')

        def test_disabled_remember_ignores_corrupt_profiles(self):
            self.path.write_text('{bad')
            self.local({'sf6ExportWorkflow': 'future_v2'})
            op = operator(rememberFields=False)
            before = fake_bpy.data.collections['mesh'][direct.KEY]
            direct.load(op, None, explicit={'rememberFields': False})
            self.assertTrue(op.exportBlendShapes)
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            self.assertEqual(output[WORKFLOW_KEY], 'unified_v1')
            saved = direct.save(op, None, migration_success=True)
            self.assertTrue(saved['save_blocked'])
            self.assertFalse(saved['persisted'])
            self.assertEqual(self.path.read_text(), '{bad')
            self.assertEqual(fake_bpy.data.collections['mesh'][direct.KEY], before)

        def test_disabled_disk_flag_does_not_load_values(self):
            self.write(dict(exportBlendShapes=False))
            disk = json.loads(self.path.read_text())
            disk['enabled'] = False
            self.path.write_text(json.dumps(disk))
            op = operator()
            direct.load(op, None)
            self.assertFalse(op.rememberFields)
            self.assertTrue(op.exportBlendShapes)

        def test_success_save_never_overwrites_unknown_disk_or_collection_profiles(self):
            for remember in (False, True):
                for destination in ('disk', 'collection'):
                    with self.subTest(remember=remember, destination=destination):
                        fake_bpy.data.collections['mesh'].clear()
                        known = dict(exportBlendShapes=True, selectedOnly=False)
                        unknown = dict(known, sf6ExportWorkflow='future_v2')
                        self.write(unknown if destination == 'disk' else known)
                        self.local(unknown if destination == 'collection' else known)
                        before_disk = self.path.read_bytes()
                        before_collection = deepcopy(fake_bpy.data.collections['mesh'])
                        op = operator(rememberFields=remember, sf6ExportWorkflow='unified_v1',
                                      sf6LodPolicy='retain_if_compatible')
                        saved = direct.save(op, None, migration_success=True)
                        self.assertTrue(saved['save_blocked'])
                        self.assertFalse(saved['persisted'])
                        self.assertEqual(self.path.read_bytes(), before_disk)
                        self.assertEqual(fake_bpy.data.collections['mesh'], before_collection)

        def test_unreadable_disk_cannot_be_replaced_after_success(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False))
            self.local(dict(exportBlendShapes=True, selectedOnly=False))
            before_disk = self.path.read_bytes()
            before_collection = deepcopy(fake_bpy.data.collections['mesh'])
            with patch.object(direct.Path, 'read_text', side_effect=PermissionError('unreadable')):
                saved = direct.save(operator(rememberFields=False), None, migration_success=True)
            self.assertTrue(saved['save_blocked'])
            self.assertIn('direct_settings_unreadable', saved['reason_codes'])
            self.assertEqual(self.path.read_bytes(), before_disk)
            self.assertEqual(fake_bpy.data.collections['mesh'], before_collection)

        def test_success_save_preserves_malformed_collection_json_scalars(self):
            for malformed in (None, [], False, 1, 'future'):
                self.write(dict(exportBlendShapes=True, selectedOnly=False))
                fake_bpy.data.collections['mesh'][direct.KEY] = json.dumps(malformed)
                before_disk = self.path.read_bytes()
                before_collection = deepcopy(fake_bpy.data.collections['mesh'])
                saved = direct.save(operator(rememberFields=False), None, migration_success=True)
                self.assertTrue(saved['save_blocked'])
                self.assertEqual(self.path.read_bytes(), before_disk)
                self.assertEqual(fake_bpy.data.collections['mesh'], before_collection)

        def test_other_game_save_cannot_destroy_unknown_sf6_collection_profile(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False))
            self.local(dict(sf6ExportWorkflow='future_v2'))
            before_disk = self.path.read_bytes()
            before_collection = deepcopy(fake_bpy.data.collections['mesh'])
            saved = direct.save(operator(filename_ext='.241111606'), None, migration_success=True)
            self.assertTrue(saved['save_blocked'])
            self.assertEqual(self.path.read_bytes(), before_disk)
            self.assertEqual(fake_bpy.data.collections['mesh'], before_collection)

        def test_load_never_writes_or_promotes_marker(self):
            self.write(dict(exportBlendShapes=True, sf6HybridPreserve=True, selectedOnly=False))
            self.local(dict(exportBlendShapes=True, sf6HybridPreserve=True, selectedOnly=False))
            before_disk = self.path.read_bytes()
            before_local = dict(fake_bpy.data.collections['mesh'])
            op = operator()
            direct.load(op, None)
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            self.assertEqual(output[LOD_POLICY_KEY], 'lod0')
            self.assertEqual(op.sf6ExportWorkflow, '')
            self.assertEqual(self.path.read_bytes(), before_disk)
            self.assertEqual(fake_bpy.data.collections['mesh'], before_local)

        def test_success_commit_persists_only_approved_fields(self):
            self.write(dict(exportBlendShapes=True, sf6HybridPreserve=True, selectedOnly=False))
            op = operator(sf6ApprovedCorrectiveLosses='["secret-token"]', sf6ExpectedPlan='plan',
                          sf6RigPolicy='export_scene_rest')
            direct.load(op, None)
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            commit_operator_settings(op, output)
            self.assertEqual(direct.save(op, None, migration_success=True)['status'], 'saved')
            disk = json.loads(self.path.read_text())['versions']['.230110883']
            self.assertEqual(disk[WORKFLOW_KEY], 'unified_v1')
            self.assertEqual(disk[LOD_POLICY_KEY], 'lod0')
            self.assertNotIn('sf6RigPolicy', disk)
            self.assertNotIn('sf6ApprovedCorrectiveLosses', disk)
            self.assertNotIn('sf6ExpectedPlan', disk)
            self.assertNotIn('secret-token', self.path.read_text())
            self.assertNotIn('_sf6', self.path.read_text())
            self.assertEqual(json.loads(fake_bpy.data.collections['mesh'][direct.KEY])['values'], disk)

        def test_conservative_save_does_not_promote_unsuccessful_choice(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False))
            op = operator()
            direct.load(op, None)
            op.sf6ExportWorkflow = 'unified_v1'
            op.sf6LodPolicy = 'lod0'
            direct.save(op, None)
            disk = json.loads(self.path.read_text())['versions']['.230110883']
            self.assertNotIn(WORKFLOW_KEY, disk)
            self.assertNotIn(LOD_POLICY_KEY, disk)

        def test_existing_valid_marker_roundtrips_without_explicit_opt_in(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False,
                            sf6ExportWorkflow='unified_v1', sf6LodPolicy='lod0'))
            op = operator()
            direct.load(op, None)
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION)
            self.assertEqual(output[WORKFLOW_KEY], 'unified_v1')
            self.assertEqual(output[LOD_POLICY_KEY], 'lod0')
            direct.save(op, None)
            self.assertEqual(json.loads(self.path.read_text())['versions']['.230110883'][LOD_POLICY_KEY], 'lod0')

        def test_new_explicit_marker_preserves_legacy_hybrid_lod0(self):
            self.write(dict(exportBlendShapes=True, sf6HybridPreserve=True, selectedOnly=False))
            op = operator(sf6ExportWorkflow='unified_v1')
            direct.load(op, None, explicit={WORKFLOW_KEY: 'unified_v1'})
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION)
            self.assertEqual(output[LOD_POLICY_KEY], 'lod0')

        def test_explicit_ordinary_survives_stored_unified_marker(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False,
                            sf6ExportWorkflow='unified_v1', sf6LodPolicy='retain_if_compatible'))
            op = operator(exportBlendShapes=False)
            direct.load(op, None, explicit={'exportBlendShapes': False})
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            self.assertFalse(output['exportBlendShapes'])
            self.assertEqual(output[WORKFLOW_KEY], 'unified_v1')
            self.assertEqual(output['_sf6UnifiedSettingsReport']['mode'], 'ordinary')
            commit_operator_settings(op, output)
            direct.save(op, None, migration_success=True)
            persisted = json.loads(self.path.read_text())['versions']['.230110883']
            self.assertNotIn(WORKFLOW_KEY, persisted)
            self.assertFalse(persisted['exportBlendShapes'])

        def test_full_strict_false_lod_flag_migrates_to_compatible_retention(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False, exportAllLODs=False))
            op = operator()
            direct.load(op, None)
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            self.assertEqual(output[LOD_POLICY_KEY], 'retain_if_compatible')

        def test_missing_legacy_scope_does_not_use_operator_default(self):
            self.write(dict(exportBlendShapes=True, exportAllLODs=False))
            op = operator()
            direct.load(op, None)
            with self.assertRaises(SettingsReviewRequired) as caught:
                direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                       enable_unified=True)
            self.assertIn('missing_legacy_selection_scope', caught.exception.report['reason_codes'])

        def test_post_load_dialog_edit_overrides_raw_saved_value(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False, exportAllLODs=False))
            op = operator()
            direct.load(op, None)
            op.selectedOnly = True
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            self.assertEqual(output[LOD_POLICY_KEY], 'lod0')

        def test_unknown_profile_marker_blocks_without_rewrite(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False, sf6ExportWorkflow='future_v2'))
            op = operator()
            before = self.path.read_bytes()
            direct.load(op, None)
            with self.assertRaises(SettingsReviewRequired):
                direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION)
            self.assertEqual(direct.save(op, None)['status'], 'review_required')
            self.assertEqual(self.path.read_bytes(), before)

        def test_explicit_known_marker_overrides_unknown_saved_marker(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False, sf6ExportWorkflow='future_v2'))
            op = operator(sf6ExportWorkflow='unified_v1')
            direct.load(op, None, explicit={WORKFLOW_KEY: 'unified_v1'})
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION)
            self.assertEqual(output[WORKFLOW_KEY], 'unified_v1')

        def test_malformed_higher_priority_value_does_not_resurrect_disk(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False))
            self.local(dict(exportBlendShapes='False'))
            op = operator()
            direct.load(op, None)
            with self.assertRaises(SettingsReviewRequired):
                direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION)
            resolved = operator(exportBlendShapes=False)
            direct.load(resolved, None, explicit={'exportBlendShapes': False})
            output = direct.prepare_options(resolved, runtime(resolved), mesh_version=settings.SF6_VERSION)
            self.assertFalse(output['exportBlendShapes'])

        def test_corrupt_disk_and_collection_return_review_without_rewrite(self):
            for disk_value, local_value in (('{bad', None), ('[]', None),
                    ('{"versions": []}', None), ('{"versions": {".230110883": []}}', None),
                    ('{}', '{bad'), ('{}', '[]'),
                    ('{}', '{"version": ".230110883", "values": []}')):
                self.path.write_text(disk_value)
                fake_bpy.data.collections['mesh'].clear()
                if local_value is not None:
                    fake_bpy.data.collections['mesh'][direct.KEY] = local_value
                op = operator()
                status = direct.load(op, None)
                self.assertTrue(status['needs_review'])
                with self.assertRaises(SettingsReviewRequired):
                    direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                           enable_unified=True)
                self.assertEqual(self.path.read_text(), disk_value)

        def test_other_game_does_not_load_or_save_sf6_workflow(self):
            data = self.write(dict(exportBlendShapes=True, selectedOnly=False,
                                   sf6ExportWorkflow='unified_v1', sf6LodPolicy='lod0'))
            data['versions']['.241111606'] = dict(exportBlendShapes=False, shapeKeyExportMode='NO',
                                                 sf6ExportWorkflow='future')
            self.path.write_text(json.dumps(data))
            self.local(dict(exportBlendShapes=True), version='.230110883')
            op = operator(filename_ext='.241111606', sf6ExportWorkflow='unified_v1')
            direct.load(op, None)
            self.assertFalse(op.exportBlendShapes)
            self.assertEqual(op.sf6ExportWorkflow, '')
            output = direct.prepare_options(op, runtime(op), mesh_version=241111606)
            self.assertNotIn(WORKFLOW_KEY, output)
            direct.save(op, None, migration_success=True)
            final = json.loads(self.path.read_text())['versions']
            self.assertEqual(final['.230110883'], data['versions']['.230110883'])
            self.assertNotIn(WORKFLOW_KEY, final['.241111606'])

        def test_write_failure_does_not_promote_collection(self):
            self.write(dict(exportBlendShapes=True, selectedOnly=False))
            self.local(dict(exportBlendShapes=True, selectedOnly=False))
            op = operator()
            direct.load(op, None)
            output = direct.prepare_options(op, runtime(op), mesh_version=settings.SF6_VERSION,
                                             enable_unified=True)
            commit_operator_settings(op, output)
            before_local = fake_bpy.data.collections['mesh'][direct.KEY]
            before_disk = self.path.read_bytes()
            with patch.object(direct.os, 'replace', side_effect=OSError('test failure')):
                result = direct.save(op, None, migration_success=True)
            self.assertEqual(result['status'], 'not_saved')
            self.assertEqual(fake_bpy.data.collections['mesh'][direct.KEY], before_local)
            self.assertEqual(self.path.read_bytes(), before_disk)
            self.assertEqual(len(list(self.path.parent.iterdir())), 1)

        def test_save_does_not_touch_batch_or_folder_preferences(self):
            collection = fake_bpy.data.collections['mesh']
            collection.update({BATCH_WORKFLOW_KEY: 'future_batch', BATCH_PRESERVE_SOURCE: False,
                               'SF6ModFolderDefaults': '{"keep": true}'})
            op = operator(sf6ExportWorkflow='unified_v1', sf6LodPolicy='retain_if_compatible')
            before = dict(collection)
            direct.save(op, None, migration_success=True)
            for key, value in before.items():
                self.assertEqual(collection[key], value)

    try:
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                                   for cls in (AdapterTests, DirectSettingsTests))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    finally:
        if previous_bpy is None:
            sys.modules.pop('bpy', None)
        else:
            sys.modules['bpy'] = previous_bpy
        for key in list(sys.modules):
            if key == package_name or key.startswith(package_name + '.'):
                del sys.modules[key]


if __name__ == '__main__':
    raise SystemExit(_run_tests())
