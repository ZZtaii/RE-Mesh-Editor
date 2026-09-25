"""Small Blender integration checks for topology-changing body shape transfer.

Run with Blender --background --factory-startup --python-exit-code 1 --python
tests/test_sf6_shape_transfer.py.  This test uses no private game assets.
"""

import importlib
from pathlib import Path
import sys

import addon_utils
import bpy


def mesh_object(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (2, 3, 0)
    return obj


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    transfer = importlib.import_module(repo.name + '.modules.mesh.sf6_shape_transfer')

    donor = mesh_object('donor', [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
    donor.shape_key_add(name='Basis')
    knee = donor.shape_key_add(name='BS_Body00.R_Knee_90_0_0')
    knee.data[1].co.z = 1
    hip = donor.shape_key_add(name='BS_Body00.R_Hip_90_0_0')
    hip.data[2].co.z = .5

    target = mesh_object('edited body', [(0, 0, 0), (1, 0, 0), (0, 1, 0),
                                         (.25, .25, 0), (10, 10, 0)],
                         [(0, 1, 3), (1, 2, 3), (2, 0, 3)])
    target.shape_key_add(name='Basis')
    target_knee = target.shape_key_add(name='BS_Body37.R_Knee_90_0_0')
    target_knee.data[0].co.z = .4  # This mapped source edit must survive.
    target_thigh = target.shape_key_add(name='BS_Body37.R_Thigh_90_0_0')
    target_thigh.data[3].co.z = .2  # Unmatched keys must stay untouched.
    before = [(tuple(v.co) for v in key.data) for key in
              target.data.shape_keys.key_blocks]
    before = [list(values) for values in before]

    options = dict(max_distance=.1, min_normal_dot=0,
                   target_vertex_indices=(3, 4), require_donor_source=False)
    preview = transfer.transfer_shape_keys(donor, target, dry_run=True, **options)
    assert preview['verified_source_vertices'] == 3, preview
    assert preview['candidate_vertices'] == 2 and preview['mapped_vertices'] == 1, preview
    assert preview['rejected']['distance'] == 1, preview
    assert len(preview['matched_shapes']) == 1, preview
    assert preview['unmatched_target_shapes'] == [target_thigh.name], preview
    assert [list(tuple(v.co) for v in key.data) for key in
            target.data.shape_keys.key_blocks] == before, 'Preview modified shape keys'

    result = transfer.transfer_shape_keys(donor, target, **options)
    assert result['mapped_vertices'] == 1, result
    assert abs(target_knee.data[3].co.z - .25) < 1e-6
    assert abs(target_knee.data[0].co.z - .4) < 1e-6
    assert abs(target_knee.data[4].co.z) < 1e-6
    assert abs(target_thigh.data[3].co.z - .2) < 1e-6
    print('PASS semantic_match_and_added_only_transfer')

    explicit = {target_thigh.name: hip.name}
    preview = transfer.transfer_shape_keys(donor, target, shape_name_map=explicit,
                                           dry_run=True, **options)
    assert len(preview['matched_shapes']) == 2, preview
    assert preview['skipped_existing_shape_vertices'] == 2, preview
    assert abs(target_thigh.data[3].co.z - .2) < 1e-6
    transfer.transfer_shape_keys(donor, target, shape_name_map=explicit, **options)
    assert abs(target_thigh.data[3].co.z - .2) < 1e-6
    print('PASS preexisting_added_deltas_are_preserved')
    target_thigh.data[3].co.z = 0  # Explicit reset allows a new shape to fill it.
    transfer.transfer_shape_keys(donor, target, shape_name_map=explicit, **options)
    assert abs(target_thigh.data[3].co.z - .125) < 1e-6
    print('PASS explicit_anatomical_mapping_and_preview')

    zero = transfer.transfer_shape_keys(donor, target, max_distance=.001,
                                        target_vertex_indices=(4,),
                                        require_donor_source=False)
    assert zero['mapped_vertices'] == 0 and zero['rejected']['distance'] == 1
    print('PASS distance_gate')


if __name__ == '__main__':
    main()
