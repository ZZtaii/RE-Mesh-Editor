"""Opt-in original material ordering for ordinary mesh rebuilds."""
import json
KEY = 'REOriginalMaterialTableV1'

def decode_table(value):
    if value is None:
        raise ValueError('Preserve Original Material Table requires a fresh import with material-table metadata. Reimport the source or turn the option off.')
    try:
        names = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Original material-table metadata is invalid.') from exc
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n or '\x00' in n for n in names):
        raise ValueError('Original material-table metadata must be a nonempty list of valid names.')
    if len(names) != len(set(names)):
        raise ValueError('Original material table contains duplicate names; preservation is ambiguous.')
    if len(names) > 255:
        raise ValueError('Original material table exceeds the supported 255-slot limit.')
    return names

def preserve_table(parsed, original):
    """Validate first, then remap all parsed submesh references atomically."""
    current = list(parsed.materialNameList)
    if len(current) != len(set(current)):
        raise ValueError('Exported material names are ambiguous.')
    additions = sorted(set(current) - set(original))
    final = list(original) + additions
    if len(final) > 255:
        raise ValueError('Preserved material table exceeds the supported 255-slot limit.')
    index = {n: i for i, n in enumerate(final)}
    updates = []
    seen = set()
    for attr in ('mainMeshLODList', 'shadowMeshLODList', 'occlusionMeshLODList'):
        for lod in getattr(parsed, attr, []):
            for group in lod.visconGroupList:
                for sub in group.subMeshList:
                    if id(sub) in seen:
                        continue
                    seen.add(id(sub))
                    if not 0 <= sub.materialIndex < len(current):
                        raise ValueError('Submesh material index is outside the exported material table.')
                    updates.append((sub, index[current[sub.materialIndex]]))
    for sub, material_index in updates:
        sub.materialIndex = material_index
    parsed.materialNameList = final
    return additions
