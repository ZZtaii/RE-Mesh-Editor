"""Read-only candidate, not installed into the addon.

Compare cached matrices actually serialized by preserveBoneMatrices=True against
verified embedded SF6 matrices. Call alongside, not instead of, rest-rig checks.
Returns status unchanged/changed/unverified plus concrete reasons.
"""
import hashlib
import struct
import numpy as np

FIELDS = ('reMeshLocalMatrix', 'reMeshWorldMatrix', 'reMeshInverseMatrix')

def compare_cached_source_rig(collection, source):
    def result(status, reason, **detail):
        return dict(status=status, reason=reason, **detail)
    if source is None:
        return result('unverified', 'source_unavailable')
    data = source.data
    if hashlib.sha256(data).hexdigest() != collection.get('SF6SourceSHA256'):
        return result('unverified', 'source_hash_mismatch')
    arms = [obj for obj in collection.objects if obj.type == 'ARMATURE']
    if not source.skeleton_offset:
        return result('unchanged' if not arms else 'changed',
                      'no_source_skeleton' if not arms else 'scene_added_skeleton')
    if len(arms) != 1:
        return result('unverified', 'expected_one_root_armature', armatures=len(arms))
    try:
        sk = source.skeleton_offset
        if sk < 0 or sk + 48 > len(data):
            return result('unverified', 'invalid_source_skeleton_header')
        count = struct.unpack_from('<I', data, sk)[0]
        if not 0 < count <= len(data) // 64:
            return result('unverified', 'invalid_source_bone_count')
        offsets = struct.unpack_from('<QQQ', data, sk + 24)
        if any(off <= 0 or off + count * 64 > len(data) for off in offsets):
            return result('unverified', 'invalid_source_matrix_range')
        # This is the same ordered lookup as sf6_hybrid._bone_names(source).
        # The name table indexes the full skeleton, not the weighted-bone remap.
        name_indices = struct.unpack_from('<' + 'H' * count, data, source.bone_names_offset)
        names = [source.names[index] for index in name_indices]
        if len(set(names)) != count:
            return result('unverified', 'duplicate_source_bone_names')
        source_matrices = [np.frombuffer(data, '<f4', count * 16, off).reshape(count, 4, 4)
                           for off in offsets]
        if any(not np.isfinite(values).all() for values in source_matrices):
            return result('unverified', 'nonfinite_source_matrix')
        by_name = {}
        for bone in arms[0].data.bones:
            # Match Ordinary's final exported names, including >63-character
            # names represented in Blender by #HASHED_* + unhashedBoneName.
            name = bone.get('unhashedBoneName', bone.name)
            if not isinstance(name, str) or not name or name in by_name:
                return result('unverified', 'ambiguous_scene_bone_names')
            by_name[name] = bone
        if set(by_name) != set(names):
            return result('changed', 'cached_rig_bone_names_changed',
                          missing=sorted(set(names) - set(by_name)),
                          extra=sorted(set(by_name) - set(names)))
        changes = []
        for index, name in enumerate(names):
            bone = by_name[name]
            for field, matrices in zip(FIELDS, source_matrices):
                value = bone.get(field)
                if value is None:
                    return result('unverified', 'missing_cached_matrix', bone=name, field=field)
                # Blender may expose nested IDPropertyArray rows. Convert
                # explicitly and compare in the writer's float32 precision.
                cached = np.asarray([[float(v) for v in row] for row in value], dtype='<f4')
                if cached.shape != (4, 4) or not np.isfinite(cached).all():
                    return result('unverified', 'invalid_cached_matrix', bone=name, field=field)
                if not np.array_equal(cached, matrices[index]):
                    changes.append(dict(bone=name, field=field))
        if changes:
            return result('changed', 'cached_matrices_differ_from_source', changes=changes)
        return result('unchanged', 'cached_matrices_match_verified_source', bones=count)
    except (ValueError, TypeError, IndexError, KeyError, OverflowError, struct.error):
        return result('unverified', 'cached_rig_comparison_failed')
