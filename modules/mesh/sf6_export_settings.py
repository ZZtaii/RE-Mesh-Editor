"""Batch preservation is independent of direct and mod-folder export choices."""

BATCH_PRESERVE_SOURCE = 'BatchExport_preserveSource'


def batch_preserve_source(collection):
    # Do not migrate BatchExport_exportBlendShapes: older direct/folder exports
    # also wrote it, so it does not identify an intentional batch choice.
    value = collection.get(BATCH_PRESERVE_SOURCE, False) if collection is not None else False
    return isinstance(value, (bool, int)) and value == 1


# Deliberately not prefixed with BatchExport_: mod-folder history used that
# prefix for unrelated runtime arguments in older versions.
BATCH_WORKFLOW = 'SF6BatchExportWorkflowV1'
BATCH_LOD_POLICY = 'SF6BatchExportLodPolicyV1'
BATCH_PROCESSING_FIELDS = (
    'exportAllLODs', 'autoSolveRepeatedUVs', 'preserveSharpEdges',
    'splitLoopVertices', 'normalizeWeights', 'limitTotal', 'limitTotalCount',
    'rotate90', 'useBlenderMaterialName', 'preserveBoneMatrices',
    'exportBoundingBoxes', 'shapeKeyExportMode',
)


def load_batch_workflow(item, collection):
    """Restore only this collection's batch choices, never review consent.

    Unknown strings are retained for the resolver's review-required report.
    Malformed non-string values remain in storage so the resolver can reject
    them rather than silently replacing them with an RNA string conversion.
    """
    for attr, key in (('sf6ExportWorkflow', BATCH_WORKFLOW),
                      ('sf6LodPolicy', BATCH_LOD_POLICY)):
        value = collection.get(key, '')
        setattr(item, attr, value if isinstance(value, str) else '')
    preserve = collection.get(BATCH_PRESERVE_SOURCE, False)
    item.sf6BatchPreserveReviewRequired = (BATCH_PRESERVE_SOURCE in collection and
        not (type(preserve) is bool or type(preserve) is int and preserve in (0, 1)))
    item.sf6UnifiedOptIn = False
    item.sf6ReviewOnly = False
    item.sf6RigPolicy = 'auto'
    item.sf6ApprovedCorrectiveLosses = '[]'
    item.sf6ExpectedPlan = ''
    item.sf6UnifiedResult = ''


def batch_uses_unified_view(item, collection):
    """Detect explicit/stored workflow intent for profile-default callbacks."""
    if not item.path.endswith('.mesh.230110883'):
        return False
    marker = getattr(item, 'sf6ExportWorkflow', '')
    if not marker and collection is not None:
        marker = collection.get(BATCH_WORKFLOW)
    return bool(getattr(item, 'sf6UnifiedOptIn', False) or marker == 'unified_v1')



def apply_batch_new_profile_defaults(item, collection, *, enable_unified=False):
    """Apply approved fresh-profile defaults without overriding current edits.

    Routing/selection history and the ambiguous shared preservation flag do
    not establish a profile. Independent batch choices or any already-saved
    processing control do, including partial profiles. Population and generated
    defaults are not explicit edits.
    """
    import json
    if not item.path.endswith('.mesh.230110883'):
        return ()
    if not (enable_unified or batch_uses_unified_view(item, collection)):
        return ()
    from .sf6_unified_ui import apply_new_profile_defaults, NEW_PROFILE_DEFAULTS
    source = collection if collection is not None else {}
    has_saved_profile = any(key in source for key in
        (BATCH_PRESERVE_SOURCE, BATCH_WORKFLOW, BATCH_LOD_POLICY) +
        tuple('BatchExport_' + key for key in BATCH_PROCESSING_FIELDS))
    try:
        explicit = json.loads(getattr(item, 'sf6BatchExplicitFields', '[]'))
    except (TypeError, ValueError):
        explicit = []
    if not isinstance(explicit, list):
        explicit = []
    # Plain older Python callers supply their attributes explicitly.
    if not hasattr(item, 'bl_rna'):
        explicit = [key for key in tuple(NEW_PROFILE_DEFAULTS) + ('exportBlendShapes',)
                    if hasattr(item, key)]
    # A profile saved before the unified workflow carries no workflow marker.
    # Back then a preserved export copied the source and ignored the processing
    # toggles, so their stored values were never choices about it. Reading them
    # as rebuild requests would make an untouched mesh lose its lower LODs. The
    # first unified export stores the marker, so this applies once per collection.
    legacy_preserve = (has_saved_profile and BATCH_WORKFLOW not in source and
                       bool(getattr(item, 'exportBlendShapes', False)))
    values = {key: getattr(item, key, default) for key, default in NEW_PROFILE_DEFAULTS.items()}
    if 'exportBlendShapes' in explicit or legacy_preserve:
        values['exportBlendShapes'] = item.exportBlendShapes
    resolved = apply_new_profile_defaults(values, has_saved_profile=has_saved_profile and not legacy_preserve,
                                          explicit=explicit, enable_unified=True)
    applied = list(resolved.get('_sf6NewProfileDefaults', ()))
    if not has_saved_profile and 'exportBlendShapes' not in explicit:
        resolved['exportBlendShapes'] = True
        applied.append('exportBlendShapes')
    initializing = getattr(item, 'sf6BatchInitializing', False)
    item.sf6BatchInitializing = True
    try:
        for key in applied:
            setattr(item, key, resolved[key])
    finally:
        item.sf6BatchInitializing = initializing
    return tuple(applied)
