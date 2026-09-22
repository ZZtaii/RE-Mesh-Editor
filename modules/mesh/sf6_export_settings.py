"""Batch preservation is independent of direct and mod-folder export choices."""

BATCH_PRESERVE_SOURCE = 'BatchExport_preserveSource'


def batch_preserve_source(collection):
    # Do not migrate BatchExport_exportBlendShapes: older direct/folder exports
    # also wrote it, so it does not identify an intentional batch choice.
    value = collection.get(BATCH_PRESERVE_SOURCE, False) if collection is not None else False
    return isinstance(value, (bool, int)) and value == 1
