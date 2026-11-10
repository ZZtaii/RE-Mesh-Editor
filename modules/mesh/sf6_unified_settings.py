"""Opt-in SF6 settings migration, independent of Blender and persistence.

This module does not register properties, change a dialog, load/save presets,
or choose an export path. Pass one flat SF6 settings profile at a time, after
the caller has applied its normal precedence (explicit arguments over saved
values). Direct and mod-folder profiles must not be merged. For ``batch``,
pass the collection's stored properties, not a direct-export options dict.

Migration is deliberately opt-in. Existing strict/hybrid presets stay as they
are unless ``enable_unified=True`` or this *same profile* already contains the
versioned workflow marker. A stored Ordinary choice remains Ordinary even
when a stale unified marker is present. Changing that choice is a separate,
explicit settings edit, not something migration can infer.

Batch has its own marker, ``SF6BatchExportWorkflowV1``. A future batch
adapter may forward it as the runtime ``sf6ExportWorkflow`` field only after
checking ``result.mode == 'unified'``. Neither a direct-export marker nor the
historically shared ``BatchExport_exportBlendShapes`` grants batch opt-in.
The independent marker must not be ``BatchExport_sf6ExportWorkflow``: the
existing mod-folder exporter writes generic runtime options with that prefix.

For legacy migration, Hybrid keeps its LOD0 contract. Full strict exports
retain compatible LODs, even if exportAllLODs=False (the old full strict path
ignored that flag). Selected-only strict exports use their explicit LOD flag.
Missing/ambiguous legacy selection scope requires review, not an inferred
LOD0 choice. Already-unified or genuinely new profiles use the new default.
``retain_if_compatible`` is best effort; the exporter must report actual loss.

``needs_review`` describes ambiguous settings, not a mesh/export failure. The
caller must not activate unified routing for such a result. No legacy values
are silently repaired, and unknown future workflow versions are not replaced.
No geometry, shape compatibility, or lower-LOD promise can be inferred here.

Run the embedded pure regression suite without Blender or add-on imports:

    python path/to/sf6_unified_settings.py
"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass


SF6_VERSION = 230110883
WORKFLOW_KEY = 'sf6ExportWorkflow'
UNIFIED_WORKFLOW = 'unified_v1'
BATCH_WORKFLOW_KEY = 'SF6BatchExportWorkflowV1'
BATCH_PRESERVE_SOURCE = 'BatchExport_preserveSource'
LOD_POLICY_KEY = 'sf6LodPolicy'
BATCH_LOD_POLICY_KEY = 'SF6BatchExportLodPolicyV1'
RETAIN_COMPATIBLE = 'retain_if_compatible'
LOD0_ONLY = 'lod0'
_SCOPES = frozenset(('direct', 'mod_folder', 'batch'))


@dataclass(frozen=True)
class MigrationResult:
    """A detached profile plus an inspectable, non-user-facing decision."""

    settings: dict
    legacy_mode: str
    mode: str
    reason_codes: tuple
    needs_review: bool
    changed: bool


def migrate_settings(values=None, *, scope='direct', enable_unified=False,
                     mesh_version=SF6_VERSION):
    """Return a migrated *copy*; never mutate the supplied profile.

    ``enable_unified`` is an explicit opt-in, not an application default. The
    existing marker is also sufficient, but Ordinary and invalid settings
    take precedence. A missing direct/folder preservation flag follows those
    operators' True default. A missing independent batch flag follows its
    False default; unlike an explicitly stored False, that absence does not
    prevent a new, explicit opt-in.

    Direct/folder booleans must be actual booleans, as their loaders require.
    Independent batch storage also accepts integers 0 and 1, matching ID
    property serialization. Other types/values are not coerced to booleans.

    Pass the actual destination mesh version when using a generic settings
    loader. Non-SF6 profiles are copied unchanged, including any stale marker.
    This helper is not a replacement for validating the destination format
    at the export entry point.
    """
    if scope not in _SCOPES:
        raise ValueError('Unknown export settings scope: ' + str(scope))
    if type(enable_unified) is not bool:
        raise TypeError('enable_unified must be a bool')
    if values is not None and not isinstance(values, Mapping):
        raise TypeError('values must be a flat settings mapping or None')

    original = dict(values) if values is not None else {}
    result = deepcopy(original)
    reasons = []

    def decision(legacy_mode, mode, needs_review=False):
        return MigrationResult(result, legacy_mode, mode, tuple(reasons),
                               needs_review, result != original)

    if not ((type(mesh_version) is int and mesh_version == SF6_VERSION) or
            (type(mesh_version) is str and mesh_version in
             ('230110883', '.230110883'))):
        reasons.append('not_sf6')
        return decision('not_sf6', 'not_sf6')

    batch = scope == 'batch'
    workflow_key = BATCH_WORKFLOW_KEY if batch else WORKFLOW_KEY
    preserve_key = BATCH_PRESERVE_SOURCE if batch else 'exportBlendShapes'
    present = preserve_key in original
    preserve = original.get(preserve_key, not batch)
    hybrid = False if batch else original.get('sf6HybridPreserve', False)

    if batch and 'BatchExport_exportBlendShapes' in original:
        reasons.append('ignored_ambiguous_batch_preservation')
    if batch and any(key in original for key in
                     ('BatchExport_sf6ExportWorkflow', 'BatchExport_sf6LodPolicy')):
        reasons.append('ignored_ambiguous_batch_workflow_fields')
    if batch and any(key in original for key in
                     (WORKFLOW_KEY, 'exportBlendShapes', 'sf6HybridPreserve')):
        reasons.append('ignored_non_batch_workflow_fields')

    preserve_valid = (type(preserve) is bool or
                      (batch and type(preserve) is int and preserve in (0, 1)))
    hybrid_valid = type(hybrid) is bool
    if not preserve_valid:
        reasons.append('invalid_preservation_value')
    if not hybrid_valid:
        reasons.append('invalid_hybrid_value')
    if not preserve_valid or not hybrid_valid:
        # Suppress only a recognized opt-in. Keep unknown future markers for
        # review rather than overwriting them with this version's assumptions.
        if result.get(workflow_key) == UNIFIED_WORKFLOW:
            result.pop(workflow_key)
        return decision('invalid', 'invalid', True)

    preserve = bool(preserve)
    if not preserve and hybrid:
        reasons.append('conflicting_legacy_flags')
        if result.get(workflow_key) == UNIFIED_WORKFLOW:
            result.pop(workflow_key)
        return decision('invalid', 'invalid', True)

    legacy_mode = ('legacy_hybrid' if hybrid else 'legacy_strict') if preserve else 'ordinary'

    # A newer/unknown workflow value might carry different semantics. Never
    # downgrade or overwrite it merely because this migration was requested.
    if workflow_key in original and original[workflow_key] != UNIFIED_WORKFLOW:
        reasons.append('unrecognized_workflow')
        return decision(legacy_mode, 'unrecognized_workflow', True)

    if present and not preserve:
        reasons.append('explicit_ordinary_retained')
        if workflow_key in result:
            result.pop(workflow_key)
            reasons.append('stale_unified_opt_in_removed')
        return decision(legacy_mode, 'ordinary')

    if enable_unified or original.get(workflow_key) == UNIFIED_WORKFLOW:
        policy_key = BATCH_LOD_POLICY_KEY if batch else LOD_POLICY_KEY
        policy, policy_reason = _lod_policy(original, batch, workflow_key, policy_key, hybrid)
        reasons.append(policy_reason)
        if policy is None:
            # A recognized marker must not bypass unresolved legacy scope or
            # a malformed policy when a caller forwards the copied settings.
            result.pop(workflow_key, None)
            return decision(legacy_mode, 'review_required', True)
        result[workflow_key] = UNIFIED_WORKFLOW
        result[policy_key] = policy
        reasons.append('unified_opt_in_retained' if workflow_key in original
                       else 'unified_opt_in_added')
        if not present:
            reasons.append('no_stored_preservation_choice')
        # Retain the legacy flags and all unrelated settings, including LOD
        # controls. The unified exporter owns compatibility and loss reports.
        return decision(legacy_mode, 'unified')

    reasons.append('no_unified_opt_in')
    if batch and not present:
        reasons.append('batch_default_ordinary_retained')
    return decision(legacy_mode, legacy_mode)


def _lod_policy(values, batch, workflow_key, policy_key, hybrid):
    """Resolve intent only, never use geometry to imply compatibility."""
    if policy_key in values:
        policy = values[policy_key]
        if type(policy) is str and policy in (RETAIN_COMPATIBLE, LOD0_ONLY):
            return policy, 'explicit_lod_policy_retained'
        return None, 'unrecognized_lod_policy'

    if values.get(workflow_key) == UNIFIED_WORKFLOW:
        return RETAIN_COMPATIBLE, 'unified_default_lod_policy_added'
    if hybrid:
        return LOD0_ONLY, 'legacy_hybrid_lod0_retained'
    if batch:
        # The batch operator does not pass selectedOnly, so its preservation
        # path was full-source. Its All LODs flag did not limit strict output.
        return RETAIN_COMPATIBLE, 'legacy_batch_full_scope_retained'

    legacy_fields = ('exportBlendShapes', 'sf6HybridPreserve', 'selectedOnly', 'exportAllLODs')
    if not any(key in values for key in legacy_fields):
        return RETAIN_COMPATIBLE, 'new_profile_lod_policy_defaulted'
    if 'selectedOnly' not in values:
        return None, 'missing_legacy_selection_scope'
    if type(values['selectedOnly']) is not bool:
        return None, 'invalid_legacy_selection_scope'
    if not values['selectedOnly']:
        return RETAIN_COMPATIBLE, 'legacy_strict_full_scope_retained'
    if 'exportAllLODs' not in values:
        return None, 'missing_selected_lod_scope'
    if type(values['exportAllLODs']) is not bool:
        return None, 'invalid_selected_lod_scope'
    if values['exportAllLODs']:
        return RETAIN_COMPATIBLE, 'legacy_selected_all_lods_retained'
    return LOD0_ONLY, 'legacy_selected_lod0_retained'


def _run_tests():
    """Keep this isolated prototype's reproducible tests in its sole file."""
    import unittest

    class MigrationTests(unittest.TestCase):
        def test_legacy_presets_do_not_implicitly_opt_in(self):
            for scope in ('direct', 'mod_folder'):
                for hybrid in (False, True):
                    with self.subTest(scope=scope, hybrid=hybrid):
                        source = {'exportBlendShapes': True, 'sf6HybridPreserve': hybrid}
                        actual = migrate_settings(source, scope=scope)
                        self.assertEqual(actual.settings, source)
                        self.assertEqual(actual.mode, 'legacy_hybrid' if hybrid else 'legacy_strict')
                        self.assertFalse(actual.changed)
                        self.assertFalse(actual.needs_review)

        def test_explicit_opt_in_migrates_both_preservation_modes(self):
            for scope in ('direct', 'mod_folder'):
                for hybrid in (False, True):
                    with self.subTest(scope=scope, hybrid=hybrid):
                        source = {'exportBlendShapes': True, 'sf6HybridPreserve': hybrid,
                                  'exportAllLODs': True, 'selectedOnly': True,
                                  'normalizeWeights': False, 'nested': {'items': [1]}}
                        actual = migrate_settings(source, scope=scope, enable_unified=True)
                        self.assertEqual(actual.mode, 'unified')
                        policy = LOD0_ONLY if hybrid else RETAIN_COMPATIBLE
                        self.assertEqual(actual.settings, dict(source, sf6ExportWorkflow=UNIFIED_WORKFLOW,
                                                               sf6LodPolicy=policy))
                        self.assertNotIn(WORKFLOW_KEY, source)
                        self.assertTrue(actual.changed)
                        self.assertFalse(actual.needs_review)
                        actual.settings['nested']['items'].append(2)
                        self.assertEqual(source['nested']['items'], [1])

        def test_explicit_ordinary_cannot_be_upgraded_by_migration(self):
            for scope in ('direct', 'mod_folder'):
                for opt_in in (False, True):
                    for marker in (False, True):
                        with self.subTest(scope=scope, opt_in=opt_in, marker=marker):
                            source = {'exportBlendShapes': False, 'sf6HybridPreserve': False}
                            if marker:
                                source[WORKFLOW_KEY] = UNIFIED_WORKFLOW
                            actual = migrate_settings(source, scope=scope, enable_unified=opt_in)
                            self.assertEqual(actual.mode, 'ordinary')
                            self.assertNotIn(WORKFLOW_KEY, actual.settings)
                            self.assertFalse(actual.settings['exportBlendShapes'])
                            self.assertFalse(actual.needs_review)
                            self.assertIn('explicit_ordinary_retained', actual.reason_codes)

        def test_conflicting_flags_are_not_silently_fixed(self):
            for scope in ('direct', 'mod_folder'):
                source = {'exportBlendShapes': False, 'sf6HybridPreserve': True,
                          WORKFLOW_KEY: UNIFIED_WORKFLOW}
                actual = migrate_settings(source, scope=scope, enable_unified=True)
                self.assertTrue(actual.needs_review)
                self.assertEqual(actual.mode, 'invalid')
                self.assertIn('conflicting_legacy_flags', actual.reason_codes)
                self.assertNotIn(WORKFLOW_KEY, actual.settings)
                self.assertTrue(actual.settings['sf6HybridPreserve'])
                self.assertFalse(actual.settings['exportBlendShapes'])
                self.assertEqual(source[WORKFLOW_KEY], UNIFIED_WORKFLOW)

        def test_unknown_workflow_is_never_overwritten(self):
            for scope in ('direct', 'mod_folder', 'batch'):
                key = BATCH_WORKFLOW_KEY if scope == 'batch' else WORKFLOW_KEY
                for marker in ('unified_v2', 'legacy', '', None, True, 1, []):
                    with self.subTest(scope=scope, marker=marker):
                        source = {key: marker}
                        actual = migrate_settings(source, scope=scope, enable_unified=True)
                        self.assertEqual(actual.settings, source)
                        self.assertTrue(actual.needs_review)
                        self.assertFalse(actual.changed)
                        self.assertEqual(actual.mode, 'unrecognized_workflow')

        def test_direct_and_folder_values_are_strictly_typed(self):
            for scope in ('direct', 'mod_folder'):
                for key in ('exportBlendShapes', 'sf6HybridPreserve'):
                    for value in (0, 1, 1.0, 'False', 'true', '', None, [], {}):
                        with self.subTest(scope=scope, key=key, value=value):
                            source = {key: value, WORKFLOW_KEY: UNIFIED_WORKFLOW}
                            actual = migrate_settings(source, scope=scope, enable_unified=True)
                            self.assertEqual(actual.mode, 'invalid')
                            self.assertTrue(actual.needs_review)
                            self.assertNotIn(WORKFLOW_KEY, actual.settings)
                            self.assertEqual(actual.settings[key], value)

        def test_new_profiles_require_opt_in(self):
            for scope in ('direct', 'mod_folder', 'batch'):
                with self.subTest(scope=scope):
                    default = migrate_settings(scope=scope)
                    self.assertEqual(default.settings, {})
                    self.assertEqual(default.mode, 'ordinary' if scope == 'batch' else 'legacy_strict')
                    chosen = migrate_settings(scope=scope, enable_unified=True)
                    key = BATCH_WORKFLOW_KEY if scope == 'batch' else WORKFLOW_KEY
                    policy_key = BATCH_LOD_POLICY_KEY if scope == 'batch' else LOD_POLICY_KEY
                    self.assertEqual(chosen.settings, {key: UNIFIED_WORKFLOW, policy_key: RETAIN_COMPATIBLE})
                    self.assertEqual(chosen.mode, 'unified')

        def test_batch_ignores_contaminated_legacy_and_direct_choices(self):
            for old_value in (False, True, 'true', None):
                source = {'BatchExport_exportBlendShapes': old_value,
                          'exportBlendShapes': True, 'sf6HybridPreserve': True,
                          WORKFLOW_KEY: UNIFIED_WORKFLOW}
                actual = migrate_settings(source, scope='batch')
                self.assertEqual(actual.mode, 'ordinary')
                self.assertEqual(actual.settings, source)
                self.assertNotIn(BATCH_WORKFLOW_KEY, actual.settings)
                self.assertIn('ignored_ambiguous_batch_preservation', actual.reason_codes)
                self.assertIn('ignored_non_batch_workflow_fields', actual.reason_codes)

        def test_batch_uses_only_its_independent_preservation_choice(self):
            for value in (False, True, 0, 1):
                for marker in (False, True):
                    with self.subTest(value=value, marker=marker):
                        source = {BATCH_PRESERVE_SOURCE: value,
                                  'BatchExport_exportBlendShapes': not bool(value)}
                        if marker:
                            source[BATCH_WORKFLOW_KEY] = UNIFIED_WORKFLOW
                        actual = migrate_settings(source, scope='batch', enable_unified=True)
                        self.assertEqual(actual.mode, 'unified' if value else 'ordinary')
                        self.assertEqual(BATCH_WORKFLOW_KEY in actual.settings, bool(value))
                        self.assertEqual(actual.settings[BATCH_PRESERVE_SOURCE], value)
                        self.assertEqual(type(actual.settings[BATCH_PRESERVE_SOURCE]), type(value))
                        self.assertFalse(actual.needs_review)

        def test_batch_invalid_values_do_not_gain_opt_in(self):
            for value in (2, -1, 1.0, 0.0, '1', 'False', None, [], {}):
                source = {BATCH_PRESERVE_SOURCE: value, BATCH_WORKFLOW_KEY: UNIFIED_WORKFLOW}
                actual = migrate_settings(source, scope='batch', enable_unified=True)
                self.assertEqual(actual.mode, 'invalid')
                self.assertTrue(actual.needs_review)
                self.assertNotIn(BATCH_WORKFLOW_KEY, actual.settings)

        def test_scopes_cannot_change_each_others_keys(self):
            source = {'exportBlendShapes': True, 'sf6HybridPreserve': False,
                      'selectedOnly': False,
                      BATCH_PRESERVE_SOURCE: False, BATCH_WORKFLOW_KEY: UNIFIED_WORKFLOW,
                      'BatchExport_path': 'untouched.mesh.230110883'}
            for scope in ('direct', 'mod_folder'):
                actual = migrate_settings(source, scope=scope, enable_unified=True)
                self.assertEqual(actual.mode, 'unified')
                for key in source:
                    self.assertEqual(actual.settings[key], source[key])
            batch = migrate_settings(source, scope='batch', enable_unified=True)
            self.assertEqual(batch.mode, 'ordinary')
            self.assertNotIn(WORKFLOW_KEY, batch.settings)
            self.assertTrue(batch.settings['exportBlendShapes'])
            self.assertNotIn(BATCH_WORKFLOW_KEY, batch.settings)

        def test_idempotent_for_all_valid_legacy_combinations(self):
            for scope in ('direct', 'mod_folder', 'batch'):
                preserve_key = BATCH_PRESERVE_SOURCE if scope == 'batch' else 'exportBlendShapes'
                for value in (False, True):
                    first = migrate_settings({preserve_key: value, 'selectedOnly': False},
                                             scope=scope, enable_unified=True)
                    second = migrate_settings(first.settings, scope=scope, enable_unified=True)
                    self.assertEqual(first.settings, second.settings)
                    self.assertEqual(first.mode, second.mode)
                    self.assertFalse(second.changed)

        def test_preexisting_marker_is_sufficient_opt_in(self):
            for scope in ('direct', 'mod_folder', 'batch'):
                key = BATCH_WORKFLOW_KEY if scope == 'batch' else WORKFLOW_KEY
                source = {key: UNIFIED_WORKFLOW}
                actual = migrate_settings(source, scope=scope)
                self.assertEqual(actual.mode, 'unified')
                self.assertTrue(actual.changed)
                self.assertFalse(actual.needs_review)
                self.assertFalse(migrate_settings(actual.settings, scope=scope).changed)

        def test_other_formats_remain_unchanged(self):
            source = {'exportBlendShapes': True, 'sf6HybridPreserve': True,
                      WORKFLOW_KEY: UNIFIED_WORKFLOW, 'shapeKeyExportMode': 'MODE1'}
            for version in (241111606, 260421070, 230110883.0, True, None, 'invalid'):
                actual = migrate_settings(source, mesh_version=version, enable_unified=True)
                self.assertEqual(actual.mode, 'not_sf6')
                self.assertEqual(actual.settings, source)
                self.assertFalse(actual.changed)
                self.assertFalse(actual.needs_review)

        def test_supported_sf6_version_representations(self):
            for version in (230110883, '230110883', '.230110883'):
                self.assertEqual(migrate_settings(mesh_version=version,
                                                 enable_unified=True).mode, 'unified')

        def test_missing_legacy_flags_use_scope_defaults(self):
            self.assertEqual(migrate_settings({'sf6HybridPreserve': True}).mode, 'legacy_hybrid')
            self.assertEqual(migrate_settings({'exportBlendShapes': False}).mode, 'ordinary')
            batch = migrate_settings({BATCH_PRESERVE_SOURCE: True}, scope='batch')
            self.assertEqual(batch.mode, 'legacy_strict')
            self.assertNotIn(BATCH_WORKFLOW_KEY, batch.settings)

        def test_none_and_mapping_inputs_are_detached(self):
            from types import MappingProxyType
            source = MappingProxyType({'exportBlendShapes': True, 'nested': [1]})
            actual = migrate_settings(source)
            actual.settings['nested'].append(2)
            self.assertEqual(source['nested'], [1])
            self.assertEqual(migrate_settings(None).settings, {})

        def test_full_strict_ignores_old_lod_toggle(self):
            for scope in ('direct', 'mod_folder'):
                for old_lods in (False, True):
                    source = {'exportBlendShapes': True, 'selectedOnly': False,
                              'exportAllLODs': old_lods}
                    actual = migrate_settings(source, scope=scope, enable_unified=True)
                    self.assertEqual(actual.mode, 'unified')
                    self.assertEqual(actual.settings[LOD_POLICY_KEY], RETAIN_COMPATIBLE)
                    self.assertIn('legacy_strict_full_scope_retained', actual.reason_codes)
                    self.assertEqual(actual.settings['exportAllLODs'], old_lods)

        def test_selected_strict_preserves_explicit_lod_scope(self):
            for scope in ('direct', 'mod_folder'):
                for old_lods in (False, True):
                    source = {'exportBlendShapes': True, 'selectedOnly': True,
                              'exportAllLODs': old_lods}
                    actual = migrate_settings(source, scope=scope, enable_unified=True)
                    self.assertEqual(actual.mode, 'unified')
                    self.assertEqual(actual.settings[LOD_POLICY_KEY],
                                     RETAIN_COMPATIBLE if old_lods else LOD0_ONLY)
                    self.assertFalse(actual.needs_review)

        def test_explicit_hybrid_keeps_its_lod0_contract(self):
            actual = migrate_settings({'sf6HybridPreserve': True}, enable_unified=True)
            self.assertEqual(actual.mode, 'unified')
            self.assertEqual(actual.settings[LOD_POLICY_KEY], LOD0_ONLY)
            self.assertIn('legacy_hybrid_lod0_retained', actual.reason_codes)

        def test_missing_legacy_selection_scope_requires_review(self):
            for source in ({'exportBlendShapes': True}, {'exportAllLODs': False},
                           {'exportBlendShapes': True, 'selectedOnly': None}):
                for scope in ('direct', 'mod_folder'):
                    actual = migrate_settings(source, scope=scope, enable_unified=True)
                    self.assertEqual(actual.mode, 'review_required')
                    self.assertTrue(actual.needs_review)
                    self.assertEqual(actual.settings, source)
                    self.assertNotIn(LOD_POLICY_KEY, actual.settings)

        def test_selected_missing_or_invalid_lod_scope_requires_review(self):
            for scope in ('direct', 'mod_folder'):
                for extra in ({}, {'exportAllLODs': None}, {'exportAllLODs': 0},
                              {'exportAllLODs': 'False'}):
                    source = dict(extra, exportBlendShapes=True, selectedOnly=True)
                    actual = migrate_settings(source, scope=scope, enable_unified=True)
                    self.assertEqual(actual.mode, 'review_required')
                    self.assertTrue(actual.needs_review)
                    self.assertEqual(actual.settings, source)

        def test_explicit_new_lod_policy_wins_over_legacy_inference(self):
            for policy in (RETAIN_COMPATIBLE, LOD0_ONLY):
                source = {'exportBlendShapes': True, 'sf6HybridPreserve': True,
                          LOD_POLICY_KEY: policy}
                actual = migrate_settings(source, enable_unified=True)
                self.assertEqual(actual.mode, 'unified')
                self.assertEqual(actual.settings[LOD_POLICY_KEY], policy)
                self.assertIn('explicit_lod_policy_retained', actual.reason_codes)

        def test_invalid_new_lod_policy_suppresses_unified_opt_in(self):
            for scope in ('direct', 'mod_folder', 'batch'):
                key = BATCH_LOD_POLICY_KEY if scope == 'batch' else LOD_POLICY_KEY
                workflow_key = BATCH_WORKFLOW_KEY if scope == 'batch' else WORKFLOW_KEY
                for policy in ('all', '', None, True, 0, []):
                    source = {key: policy, workflow_key: UNIFIED_WORKFLOW}
                    actual = migrate_settings(source, scope=scope)
                    self.assertEqual(actual.mode, 'review_required')
                    self.assertTrue(actual.needs_review)
                    self.assertNotIn(workflow_key, actual.settings)
                    self.assertEqual(actual.settings[key], policy)

        def test_batch_cannot_inherit_generic_folder_history(self):
            source = {'BatchExport_sf6ExportWorkflow': UNIFIED_WORKFLOW,
                      'BatchExport_sf6LodPolicy': LOD0_ONLY,
                      'BatchExport_exportAllLODs': False}
            actual = migrate_settings(source, scope='batch')
            self.assertEqual(actual.mode, 'ordinary')
            self.assertEqual(actual.settings, source)
            self.assertIn('ignored_ambiguous_batch_workflow_fields', actual.reason_codes)
            chosen = migrate_settings(source, scope='batch', enable_unified=True)
            self.assertEqual(chosen.mode, 'unified')
            self.assertEqual(chosen.settings[BATCH_LOD_POLICY_KEY], RETAIN_COMPATIBLE)
            self.assertEqual(chosen.settings[BATCH_WORKFLOW_KEY], UNIFIED_WORKFLOW)

        def test_batches_keep_full_strict_scope_despite_false_lod_flag(self):
            source = {BATCH_PRESERVE_SOURCE: True, 'BatchExport_exportAllLODs': False}
            actual = migrate_settings(source, scope='batch', enable_unified=True)
            self.assertEqual(actual.mode, 'unified')
            self.assertEqual(actual.settings[BATCH_LOD_POLICY_KEY], RETAIN_COMPATIBLE)
            self.assertIn('legacy_batch_full_scope_retained', actual.reason_codes)

        def test_invalid_api_arguments_fail_without_side_effects(self):
            source = {'exportBlendShapes': False}
            for value in ('yes', 1, None):
                with self.assertRaises(TypeError):
                    migrate_settings(source, enable_unified=value)
            with self.assertRaises(ValueError):
                migrate_settings(source, scope='all')
            for value in ([], True, 1, 'profile'):
                with self.assertRaises(TypeError):
                    migrate_settings(value)
            self.assertEqual(source, {'exportBlendShapes': False})

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MigrationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(_run_tests())
