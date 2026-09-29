"""Synthetic Blender checks for isolated SF6 modifier/key snapshots.

Run with Blender --background --factory-startup --python this_file.py.
No retail fixture or installed addon is required.
"""

import importlib.util
import json
from pathlib import Path
import sys
import types

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_sf6_evaluated_test_mesh'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / 'modules' / 'mesh')]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(
    PACKAGE + '.sf6_evaluated', ROOT / 'modules' / 'mesh' / 'sf6_evaluated.py')
evaluated = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = evaluated
spec.loader.exec_module(evaluated)


def coords(points):
    result = np.empty((len(points), 3), np.float32)
    points.foreach_get('co', result.ravel())
    return result


def modifier(obj, name, kind):
    # Resolve identifiers against the running Blender RNA rather than assuming
    # an enum label is valid in this build.
    types_rna = bpy.types.ObjectModifiers.bl_rna.functions['new'].parameters['type'].enum_items
    assert kind in {item.identifier for item in types_rna}, kind
    return obj.modifiers.new(name, kind)


def enum_set(owner, field, identifier):
    allowed = {item.identifier for item in owner.bl_rna.properties[field].enum_items}
    assert identifier in allowed, (field, identifier, allowed)
    setattr(owner, field, identifier)


VERTICES = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
QUADS = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
         (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
TRIANGLES = [(face[0], face[1], face[2]) for face in QUADS] + [
    (face[0], face[2], face[3]) for face in QUADS]
RESULTS = []


def data_counts():
    return tuple(len(items) for items in (
        bpy.data.objects, bpy.data.meshes, bpy.data.armatures,
        bpy.data.collections, bpy.data.shape_keys, bpy.data.texts))


def state(obj):
    keys = obj.data.shape_keys
    return (
        coords(obj.data.vertices).tobytes(),
        tuple((key.name, key.value, key.mute, key.slider_min, key.slider_max,
               coords(key.data).tobytes()) for key in keys.key_blocks),
        obj.active_shape_key_index, obj.show_only_shape_key,
        tuple((mod.name, mod.type, mod.show_viewport, mod.show_render) for mod in obj.modifiers),
        obj.matrix_world.copy(),
        tuple((fc.data_path, fc.driver.expression) for fc in keys.animation_data.drivers)
        if keys.animation_data else (),
    )


def fixture(name, faces=QUADS):
    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    lod = bpy.data.collections.new(name + ' Main Mesh LOD0')
    lod['LOD Distance'] = 0.125
    collection.children.link(lod)
    text = bpy.data.texts.new(name + ' EmbeddedSource')
    text.write('synthetic source is shared, not decoded by snapshotting')
    collection['SF6SourceText'] = text.name
    collection['SF6SourceSHA256'] = 'synthetic'
    collection['SF6SourceRotate'] = True
    collection['SF6PreserveSource'] = True
    mesh = bpy.data.meshes.new(name + ' Mesh')
    mesh.from_pydata(VERTICES, [], faces)
    # Retail rows already own one normal. Give identity/pose fixtures that same
    # property; dedicated fan fixtures below author genuine split normals.
    for poly in mesh.polygons:
        poly.use_smooth = True
    mesh.update()
    uv = mesh.uv_layers.new(name='UVMap')
    for li, loop in enumerate(uv.data):
        vertex = mesh.vertices[mesh.loops[li].vertex_index]
        loop.uv = ((vertex.co.x + 1) / 2, (vertex.co.y + 1) / 2)
    obj = bpy.data.objects.new('Group_0_Sub_0__' + name, mesh)
    lod.objects.link(obj)
    obj['SF6SourceMeta'] = json.dumps({'lod': 0})
    obj['SF6SourceSHA256'] = 'synthetic'
    vids = mesh.attributes.new('sf6_source_vertex', 'INT', 'POINT')
    vids.data.foreach_set('value', np.arange(len(mesh.vertices), dtype=np.int32))
    fids = mesh.attributes.new('sf6_source_face', 'INT', 'FACE')
    fids.data.foreach_set('value', np.arange(len(mesh.polygons), dtype=np.int32))
    group = obj.vertex_groups.new(name='TestBone')
    group.add(list(range(len(mesh.vertices))), 1.0, 'REPLACE')
    obj.shape_key_add(name='Basis')
    top = obj.shape_key_add(name='TopLift')
    for vi in (4, 5, 6, 7):
        top.data[vi].co.z += 0.25
    right = obj.shape_key_add(name='RightStretch')
    for vi in (1, 2, 5, 6):
        right.data[vi].co.x += 0.15
    top.value = 0.4
    top.mute = True
    right.value = 0.7
    obj.active_shape_key_index = 2
    obj.show_only_shape_key = True
    driver = right.driver_add('value')
    driver.driver.expression = '0.7'
    for selected in tuple(bpy.context.selected_objects):
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()
    return collection, obj


def assert_restored(obj, before, counts):
    assert state(obj) == before, 'original object/key/modifier state changed'
    assert data_counts() == counts, (data_counts(), counts)
    assert bpy.context.selected_objects == [obj]
    assert bpy.context.view_layer.objects.active == obj


def check_snapshot(collection, obj, validate, selected=False):
    before, counts = state(obj), data_counts()
    with evaluated.evaluated_hybrid_collection(
            collection, (obj,) if selected else None) as (snapshot, chosen, report):
        copied = next(item for item in snapshot.all_objects
                      if item.type == 'MESH' and item.get('SF6SourceMeta'))
        assert snapshot['SF6SourceText'] == collection['SF6SourceText']
        assert snapshot.children[0]['LOD Distance'] == 0.125
        assert copied.data.shape_keys != obj.data.shape_keys
        assert not copied.modifiers
        assert all(key.value == 0 for key in copied.data.shape_keys.key_blocks)
        assert not copied.show_only_shape_key
        assert len(copied.data.uv_layers) == 1
        assert copied.vertex_groups[0].name == 'TestBone'
        assert all(vertex.groups and vertex.groups[0].weight > 0 for vertex in copied.data.vertices)
        assert all(len(face.vertices) == 3 for face in copied.data.polygons)
        if selected:
            assert chosen == (copied,)
            assert copied in bpy.context.selected_objects
        else:
            assert chosen is None
        validate(copied, report)
        assert state(obj) == before
    assert_restored(obj, before, counts)


def passed(name):
    RESULTS.append({'test': name, 'passed': True})
    print('PASS ' + name, flush=True)


collection, obj = fixture('Baseline', TRIANGLES)


def baseline(copied, report):
    assert np.array_equal(coords(copied.data.vertices), coords(obj.data.shape_keys.key_blocks[0].data))
    for old, new in zip(obj.data.shape_keys.key_blocks, copied.data.shape_keys.key_blocks):
        assert np.array_equal(coords(old.data), coords(new.data))
    assert [item.value for item in copied.data.attributes['sf6_source_vertex'].data] == list(range(8))
    assert report['parts'][0]['source_provenance_preserved']
    assert report['parts'][0]['source_name'] == obj.name


check_snapshot(collection, obj, baseline)
passed('zero_basis_no_modifiers_keeps_coordinates_keys_and_source_ids')
check_snapshot(collection, obj, baseline, selected=True)
passed('selected_object_maps_to_snapshot_and_selection_restores')


for subdivision_kind in ('SIMPLE', 'CATMULL_CLARK'):
    collection, obj = fixture('Subdivision' + subdivision_kind)
    sub = modifier(obj, 'Subdivision', 'SUBSURF')
    enum_set(sub, 'subdivision_type', subdivision_kind)
    sub.levels = sub.render_levels = 1

    def subdivision(copied, report):
        split_rows = (report['parts'][0]['corner_split_vertices'] +
                      report['parts'][0]['normal_split_vertices'])
        assert len(copied.data.vertices) == 26 + split_rows
        assert len(copied.data.polygons) == 48
        basis = coords(copied.data.shape_keys.key_blocks[0].data)
        for key in list(copied.data.shape_keys.key_blocks)[1:]:
            delta = coords(key.data) - basis
            moved = np.count_nonzero(np.any(delta.astype('<f2') != 0, axis=1))
            assert moved > 8, 'generated subdivision vertices lost corrective motion'
        assert all(item.value == -1 for item in copied.data.attributes['sf6_source_vertex'].data)
        assert all(item.value == -1 for item in copied.data.attributes['sf6_source_face'].data)
        assert report['evaluated_shape_vertices'] == len(copied.data.vertices)
        assert report['modifier_parts'] == 1

    check_snapshot(collection, obj, subdivision)
    passed(subdivision_kind.lower() + '_subdivision_keeps_generated_corrective_motion')


collection, obj = fixture('Deform', TRIANGLES)
twist = modifier(obj, 'Twist', 'SIMPLE_DEFORM')
enum_set(twist, 'deform_method', 'TWIST')
twist.angle = 0.6


def deform(copied, report):
    assert len(copied.data.vertices) == 8 + report['normal_split_vertices']
    assert not np.array_equal(coords(copied.data.vertices), coords(obj.data.shape_keys.key_blocks[0].data))
    basis = coords(copied.data.shape_keys.key_blocks[0].data)
    assert np.any(coords(copied.data.shape_keys.key_blocks[2].data) - basis)
    assert not report['parts'][0]['source_provenance_preserved']
    assert all(item.value == -1 for item in copied.data.attributes['sf6_source_vertex'].data)


check_snapshot(collection, obj, deform)
passed('deformation_modifier_samples_basis_and_keys')


collection, obj = fixture('Normals')
obj.data.normals_split_custom_set([(0, 0, 1)] * len(obj.data.loops))


def normals(copied, report):
    assert len(copied.data.vertices) == 8 and len(copied.data.polygons) == 12
    assert copied.data.has_custom_normals
    assert all(normal.vector.dot((0, 0, 1)) > 0.99999 for normal in copied.data.corner_normals)
    assert not report['parts'][0]['source_provenance_preserved']


check_snapshot(collection, obj, normals)
passed('raw_quads_triangulate_once_and_keep_custom_corner_normals')


collection, obj = fixture('UVCornerSeam', TRIANGLES)
for poly in obj.data.polygons:
    poly.use_smooth = False
obj.data.update()
uv_layer = obj.data.uv_layers[0]
uv_layer.data[0].uv.x += .0001
old_loop_uvs = np.asarray([loop.uv[:] for loop in uv_layer.data], np.float32)
old_corners = np.asarray([obj.data.vertices[loop.vertex_index].co[:] for loop in obj.data.loops], np.float32)
old_normals = np.asarray([normal.vector[:] for normal in obj.data.corner_normals], np.float32)


def uv_seam(copied, report):
    assert report['corner_split_vertices'] > 0
    assert len(copied.data.vertices) == (8 + report['corner_split_vertices'] +
                                        report['normal_split_vertices'])
    assert np.array_equal(np.asarray([loop.uv[:] for loop in copied.data.uv_layers[0].data], np.float32),
                          old_loop_uvs), 'splitting rounded or reordered corner UVs'
    assert np.array_equal(np.asarray([copied.data.vertices[loop.vertex_index].co[:]
                                     for loop in copied.data.loops], np.float32), old_corners)
    assert np.allclose(np.asarray([normal.vector[:] for normal in copied.data.corner_normals], np.float32),
                       old_normals, atol=1e-5)
    source_basis = coords(obj.data.shape_keys.key_blocks[0].data)
    snapshot_basis = coords(copied.data.shape_keys.key_blocks[0].data)
    for vi, point in enumerate(snapshot_basis):
        source_row = np.flatnonzero(np.all(source_basis == point, axis=1))
        assert len(source_row) == 1
        for source_key, copied_key in zip(obj.data.shape_keys.key_blocks,
                                          copied.data.shape_keys.key_blocks):
            assert np.array_equal(np.asarray(copied_key.data[vi].co),
                                  np.asarray(source_key.data[int(source_row[0])].co))
    assert all(item.value == -1 for item in copied.data.attributes['sf6_source_vertex'].data)
    assert not report['parts'][0]['source_provenance_preserved']


check_snapshot(collection, obj, uv_seam)
passed('basis_corner_split_keeps_ordered_uvs_normals_weights_and_corrective_row_map')


collection, obj = fixture('SameUVNormalFans', TRIANGLES)
for poly in obj.data.polygons:
    poly.use_smooth = False
for edge in obj.data.edges:
    edge.use_edge_sharp = False
obj.data.update()
fan_corner_normals = np.asarray([normal.vector[:] for normal in obj.data.corner_normals], np.float32)
fan_corner_coords = np.asarray([obj.data.vertices[loop.vertex_index].co[:]
                               for loop in obj.data.loops], np.float32)
fan_corner_uvs = np.asarray([loop.uv[:] for loop in obj.data.uv_layers[0].data], np.float32)
fan_source_keys = [coords(key.data) for key in obj.data.shape_keys.key_blocks]


def same_uv_normal_fans(copied, report):
    assert not any(edge.use_edge_sharp for edge in obj.data.edges)
    assert report['normal_split_vertices'] > 0
    assert report['corner_split_vertices'] == 0
    assert len(copied.data.vertices) == 8 + report['normal_split_vertices']
    assert not report['parts'][0]['source_provenance_preserved']
    assert np.array_equal(np.asarray([copied.data.vertices[loop.vertex_index].co[:]
                                     for loop in copied.data.loops], np.float32), fan_corner_coords)
    assert np.array_equal(np.asarray([loop.uv[:] for loop in copied.data.uv_layers[0].data], np.float32),
                          fan_corner_uvs)
    assert np.allclose(np.asarray([normal.vector[:] for normal in copied.data.corner_normals], np.float32),
                       fan_corner_normals, atol=1e-5, rtol=0)
    for vi, vertex in enumerate(copied.data.vertices):
        row = np.flatnonzero(np.all(fan_source_keys[0] == vertex.co[:], axis=1))
        assert len(row) == 1
        for ki, key in enumerate(copied.data.shape_keys.key_blocks):
            assert np.array_equal(np.asarray(key.data[vi].co), fan_source_keys[ki][row[0]])
    assert all(item.value == -1 for item in copied.data.attributes['sf6_source_vertex'].data)
    assert np.array_equal(np.asarray([normal.vector[:] for normal in obj.data.corner_normals], np.float32),
                          fan_corner_normals)


check_snapshot(collection, obj, same_uv_normal_fans)
passed('same_uv_implicit_normal_fans_split_rows_and_keep_corrective_correspondence')


point_fan = bpy.data.meshes.new('Disconnected UV Corner Fans')
point_fan.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)],
                     [], [(0, 1, 2), (0, 3, 4)])
point_fan.update()
point_fan_uv = point_fan.uv_layers.new(name='UVMap')
point_fan_uv.data[3].uv = (.125, .25)
fan_rows = evaluated._split_basis_uv_corners(point_fan)
assert len(fan_rows) > 5
assert point_fan.loops[0].vertex_index != point_fan.loops[3].vertex_index
assert point_fan.uv_layers[0].name == 'UVMap'
assert point_fan.uv_layers[0].data[3].uv[:] == (.125, .25)
bpy.data.meshes.remove(point_fan)
passed('disconnected_faces_sharing_only_a_uv_ambiguous_point_are_split')


collection, obj = fixture('ChangingTopology')
weld = modifier(obj, 'Weld', 'WELD')
weld.merge_threshold = 0.1
# Separate source vertices coincide on Basis, but split apart for TopLift.
keys = obj.data.shape_keys.key_blocks
keys[0].data[4].co = keys[0].data[0].co
keys[1].data[4].co = keys[0].data[0].co + __import__('mathutils').Vector((0, 0, .25))
keys[2].data[4].co = keys[0].data[0].co
before, counts = state(obj), data_counts()
try:
    with evaluated.evaluated_hybrid_collection(collection):
        raise AssertionError('changing key topology was accepted')
except evaluated.HybridExportError as error:
    assert 'topology or vertex order changes' in str(error), str(error)
assert_restored(obj, before, counts)
passed('per_key_topology_change_refuses_and_cleans_private_data')


collection, obj = fixture('FailureCleanup')
before, counts = state(obj), data_counts()
try:
    with evaluated.evaluated_hybrid_collection(collection):
        raise RuntimeError('caller failed after snapshot')
except RuntimeError as error:
    assert str(error) == 'caller failed after snapshot'
assert_restored(obj, before, counts)
passed('caller_exception_restores_selection_and_cleans_private_data')


collection, obj = fixture('PoseIsolation', TRIANGLES)
armature_data = bpy.data.armatures.new('PoseIsolation Rig')
armature = bpy.data.objects.new('PoseIsolation Rig', armature_data)
collection.objects.link(armature)
obj.select_set(False)
armature.select_set(True)
bpy.context.view_layer.objects.active = armature
bpy.ops.object.mode_set(mode='EDIT')
bone = armature_data.edit_bones.new('TestBone')
bone.head = (0, 0, 0)
bone.tail = (0, 0, 1)
bpy.ops.object.mode_set(mode='OBJECT')
armature.pose.bones['TestBone'].location.z = 2
skin = modifier(obj, 'Armature preview', 'ARMATURE')
skin.object = armature
obj.parent = armature
armature.select_set(False)
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.context.view_layer.update()
pose_before = armature.pose.bones['TestBone'].matrix.copy()


def pose(copied, report):
    assert np.array_equal(coords(copied.data.vertices), coords(obj.data.shape_keys.key_blocks[0].data))
    assert report['parts'][0]['source_provenance_preserved']
    assert not report['parts'][0]['active_modifiers']
    assert copied.parent == armature  # Exact serialized source transform hierarchy.
    lod = copied.users_collection[0]
    root = next(item for item in bpy.data.collections if lod in item.children[:])
    exported_rig = next(item for item in root.objects if item.type == 'ARMATURE')
    assert exported_rig != armature and exported_rig.data != armature_data


check_snapshot(collection, obj, pose)
assert armature.pose.bones['TestBone'].matrix == pose_before
passed('animated_armature_preview_is_not_baked_and_original_pose_is_retained')


collection, obj = fixture('Transformed', TRIANGLES)
obj.location = (2, -3, .5)
bpy.context.view_layer.update()


def transformed(copied, report):
    assert copied.matrix_world == obj.matrix_world
    assert np.array_equal(coords(copied.data.vertices), coords(obj.data.shape_keys.key_blocks[0].data))
    assert not report['parts'][0]['source_provenance_preserved']
    assert all(item.value == -1 for item in copied.data.attributes['sf6_source_vertex'].data)


check_snapshot(collection, obj, transformed)
passed('object_transform_is_retained_without_false_exact_source_deltas')


collection, obj = fixture('LODFiltering', TRIANGLES)
higher = obj.copy()
higher.data = obj.data.copy()
higher['SF6SourceMeta'] = json.dumps({'lod': 1})
high_lod = bpy.data.collections.new('LODFiltering Main Mesh LOD1')
collection.children.link(high_lod)
high_lod.objects.link(higher)
boxes = bpy.data.collections.new('LODFiltering Bounding Boxes')
boxes['~TYPE'] = 'RE_MESH_BOUNDING_BOX_COLLECTION'
collection.children.link(boxes)
box_mesh = bpy.data.meshes.new('LODFiltering Bone Bounds')
box_mesh.from_pydata([(0, 0, 0), (1, 1, 1)], [], [])
box = bpy.data.objects.new('LODFiltering Bone Bounds', box_mesh)
boxes.objects.link(box)
box['~TYPE'] = 'RE_MESH_BONE_BOUNDING_BOX'
box['MeshExportExclude'] = 1
constraint = box.constraints.new('CHILD_OF')
constraint.name = 'BoneName'
constraint.target = armature
constraint.subtarget = 'TestBone'
for selected in tuple(bpy.context.selected_objects):
    selected.select_set(False)
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
before, counts = state(obj), data_counts()
with evaluated.evaluated_hybrid_collection(collection) as (snapshot, chosen, report):
    assert len(report['parts']) == 1
    copied_boxes = next(child for child in snapshot.children
                        if child.get('~TYPE') == 'RE_MESH_BOUNDING_BOX_COLLECTION')
    copied_box = copied_boxes.objects[0]
    assert copied_box.constraints['BoneName'].subtarget == 'TestBone'
    assert copied_box.constraints['BoneName'].mute
    assert box.constraints['BoneName'].mute is False
assert_restored(obj, before, counts)
passed('only_lod0_geometry_is_captured_and_bound_metadata_is_retained')


collection, obj = fixture('HipStub')
stub_mesh = bpy.data.meshes.new('HipStub Mesh')
stub_mesh.from_pydata([(-.0001, -.0001, 0), (.0001, -.0001, 0),
                      (.0001, .0001, 0), (-.0001, .0001, 0)], [], [(0, 1, 2, 3)])
obj.data = stub_mesh
obj.vertex_groups.clear()
obj.vertex_groups.new(name='C_Hip').add([0, 1, 2, 3], 1, 'REPLACE')
counts = data_counts()
with evaluated.evaluated_hybrid_collection(collection) as (snapshot, chosen, report):
    copied = next(item for item in snapshot.all_objects if item.type == 'MESH')
    assert evaluated._is_hip_stub(copied)
    assert report['parts'][0]['retained_hip_stub']
assert data_counts() == counts
passed('exact_legacy_c_hip_quad_remains_recognizable')


collection, obj = fixture('OrphanCleanup')
user_orphan = bpy.data.meshes.new('Existing User Orphan')
user_armature_orphan = bpy.data.armatures.new('Existing User Armature Orphan')
before, counts = state(obj), data_counts()
with evaluated.evaluated_hybrid_collection(collection):
    bpy.data.meshes.new('New Exporter Orphan')
    bpy.data.armatures.new('New Exporter Armature Orphan')
assert_restored(obj, before, counts)
assert bpy.data.meshes.get('Existing User Orphan') == user_orphan
assert bpy.data.meshes.get('New Exporter Orphan') is None
assert bpy.data.armatures.get('Existing User Armature Orphan') == user_armature_orphan
assert bpy.data.armatures.get('New Exporter Armature Orphan') is None
passed('new_exporter_orphans_are_removed_and_existing_user_orphans_are_retained')


collection, obj = fixture('ExporterException')
existing_clone = bpy.data.objects.new('CLN_Existing User Object', None)
bpy.context.scene.collection.objects.link(existing_clone)
before, counts = state(obj), data_counts()
try:
    with evaluated.evaluated_hybrid_collection(collection):
        exporter_mesh = bpy.data.meshes.new('Failed Exporter Clone Mesh')
        exporter_clone = bpy.data.objects.new('CLN_Failed Exporter Clone', exporter_mesh)
        exporter_collection = bpy.data.collections.new('clonedMeshes')
        bpy.context.scene.collection.children.link(exporter_collection)
        exporter_collection.objects.link(exporter_clone)
        raise RuntimeError('ordinary writer failed during geometry processing')
except RuntimeError as error:
    assert str(error) == 'ordinary writer failed during geometry processing'
assert_restored(obj, before, counts)
assert bpy.data.objects.get('CLN_Existing User Object') == existing_clone
assert bpy.data.collections.get('clonedMeshes') is None
passed('ordinary_writer_exception_cleans_new_clones_without_removing_existing_user_objects')


collection, obj = fixture('MalformedMetadata')
obj['SF6SourceMeta'] = '{broken metadata'
before, counts = state(obj), data_counts()
try:
    with evaluated.evaluated_hybrid_collection(collection):
        raise AssertionError('malformed original source metadata accepted')
except evaluated.HybridExportError as error:
    assert 'invalid source part metadata' in str(error)
assert_restored(obj, before, counts)
passed('malformed_original_metadata_refuses_before_private_snapshot_creation')


collection, obj = fixture('InvalidRelativeKeys')
obj.data.shape_keys.key_blocks[1].vertex_group = 'TestBone'
before, counts = state(obj), data_counts()
try:
    with evaluated.evaluated_hybrid_collection(collection):
        raise AssertionError('masked original corrective key accepted')
except evaluated.HybridExportError as error:
    assert 'unmasked Basis-relative deltas' in str(error)
assert_restored(obj, before, counts)
assert obj.data.shape_keys.key_blocks[1].vertex_group == 'TestBone'
passed('masked_original_keys_are_rejected_without_rewriting_them_as_unmasked_keys')


collection, obj = fixture('SharpEdgeRows', TRIANGLES)
for edge in obj.data.edges:
    edge.use_edge_sharp = True
# Keep shading equal so this specifically exercises requested sharp geometry
# splitting after automatic normal-fan splitting has had no reason to act.
obj.data.normals_split_custom_set([(0, 0, 1)] * len(obj.data.loops))
source_key_coordinates = [coords(key.data) for key in obj.data.shape_keys.key_blocks]
before, counts = state(obj), data_counts()
with evaluated.evaluated_hybrid_collection(collection, preserve_sharp_edges=True) as (snapshot, selected, report):
    copied = next(item for item in snapshot.all_objects if item.type == 'MESH')
    assert report['sharp_split_vertices'] > 0
    assert len(copied.data.vertices) == (8 + report['normal_split_vertices'] +
                                        report['sharp_split_vertices'])
    assert not report['parts'][0]['source_provenance_preserved']
    for vi, vertex in enumerate(copied.data.vertices):
        source_row = np.flatnonzero(np.all(source_key_coordinates[0] == vertex.co[:], axis=1))
        assert len(source_row) == 1
        for key_index, key in enumerate(copied.data.shape_keys.key_blocks):
            assert np.array_equal(np.asarray(key.data[vi].co), source_key_coordinates[key_index][source_row[0]])
assert_restored(obj, before, counts)
passed('requested_sharp_edge_splits_keep_basis_and_corrective_row_correspondence')


collection, obj = fixture('SharpModeException', TRIANGLES)
before, counts = state(obj), data_counts()
update_edit_mesh = evaluated.bmesh.update_edit_mesh


def fail_private_edit_update(*args, **kwargs):
    raise RuntimeError('private sharp preprocessing failed')


evaluated.bmesh.update_edit_mesh = fail_private_edit_update
try:
    try:
        with evaluated.evaluated_hybrid_collection(collection, preserve_sharp_edges=True):
            raise AssertionError('sharp preprocessing failure was not raised')
    except RuntimeError as error:
        assert str(error) == 'private sharp preprocessing failed'
finally:
    evaluated.bmesh.update_edit_mesh = update_edit_mesh
assert bpy.context.mode == 'OBJECT'
assert_restored(obj, before, counts)
passed('sharp_edit_mode_exception_returns_to_object_mode_and_removes_private_data')


collection, obj = fixture('FakeUserIsolation', TRIANGLES)
obj.data.use_fake_user = True
obj.data.shape_keys.use_fake_user = True
before, counts = state(obj), data_counts()
with evaluated.evaluated_hybrid_collection(collection) as (snapshot, selected, report):
    copied = next(item for item in snapshot.all_objects if item.type == 'MESH')
    assert not copied.data.use_fake_user
    assert not copied.data.shape_keys.use_fake_user
    assert obj.data.use_fake_user and obj.data.shape_keys.use_fake_user
assert_restored(obj, before, counts)
assert obj.data.use_fake_user and obj.data.shape_keys.use_fake_user
passed('private_mesh_key_fake_users_are_cleared_and_original_fake_users_are_retained')


if '--source-file' in sys.argv:
    import addon_utils
    import importlib

    source_file = Path(sys.argv[sys.argv.index('--source-file') + 1])
    sys.path.insert(0, str(ROOT.parent))
    addon_utils.enable(ROOT.name, default_set=False)
    mesh_io = importlib.import_module(ROOT.name + '.modules.mesh.blender_re_mesh')
    mesh_io.importREMeshFile(str(source_file), dict(
        clearScene=True, createCollections=True, loadMaterials=False,
        loadMDFData=False, loadShellFur=False, loadUnusedTextures=False,
        loadUnusedProps=False, useBackfaceCulling=False, reloadCachedTextures=False,
        mdfPath='', importAllLODs=False, importBlendShapes=True, rotate90=True,
        mergeArmature='', importArmatureOnly=False, mergeGroups=False,
        importShadowMeshes=False, importOcclusionMeshes=False, importBoundingBoxes=False))
    source_collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
    legacy = {}
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for original in tuple(source_collection.all_objects):
        if original.type != 'MESH' or original.get('~TYPE') or original.get('MeshExportExclude'):
            continue
        old_mesh = bpy.data.meshes.new_from_object(original.evaluated_get(depsgraph))
        try:
            legacy[original.name] = (
                coords(old_mesh.vertices),
                np.asarray([normal.vector[:] for normal in old_mesh.corner_normals], np.float32),
                np.asarray(original.matrix_world, np.float32))
        finally:
            bpy.data.meshes.remove(old_mesh)
    with evaluated.evaluated_hybrid_collection(source_collection) as (snapshot, selected, report):
        by_name = {item.name: item for item in snapshot.all_objects}
        for part in report['parts']:
            copied = by_name[part['snapshot_name']]
            old_coordinates, old_normals, old_world = legacy[part['source_name']]
            assert np.array_equal(np.asarray(copied.matrix_world, np.float32), old_world), (
                'snapshot world transform changed', part['source_name'],
                np.asarray(copied.matrix_world, np.float32).tolist(), old_world.tolist())
            assert np.array_equal(coords(copied.data.vertices), old_coordinates), (
                'legacy rest-armature position bytes changed', part['source_name'],
                float(np.max(np.abs(coords(copied.data.vertices) - old_coordinates))))
            new_normals = np.asarray([normal.vector[:] for normal in copied.data.corner_normals], np.float32)
            assert np.array_equal(new_normals, old_normals), (
                'legacy rest-armature corner normal bytes changed', part['source_name'])
            bpy.context.view_layer.update()
            second_mesh = bpy.data.meshes.new_from_object(copied.evaluated_get(
                bpy.context.evaluated_depsgraph_get()))
            try:
                second_normals = np.asarray([normal.vector[:] for normal in second_mesh.corner_normals], np.float32)
                assert np.array_equal(coords(second_mesh.vertices), old_coordinates), (
                    'second-evaluation legacy position bytes changed', part['source_name'])
                assert np.array_equal(second_normals, old_normals), (
                    'second-evaluation legacy corner normal bytes changed', part['source_name'],
                    int(np.count_nonzero(second_normals != old_normals)),
                    float(np.max(np.abs(second_normals - old_normals))))
            finally:
                bpy.data.meshes.remove(second_mesh)
    passed('retail_unmodified_snapshot_positions_and_corner_normals_match_legacy_evaluation_exactly')
    if '--debug-export-dir' in sys.argv:
        debug_directory = Path(sys.argv[sys.argv.index('--debug-export-dir') + 1])
        debug_directory.mkdir(parents=True, exist_ok=True)
        source_objects = [item for item in source_collection.all_objects
                          if item.type == 'MESH' and item.data.shape_keys and
                          len(item.data.shape_keys.key_blocks) > 1]
        body = max(source_objects, key=lambda item: len(item.data.vertices))
        body_group = json.loads(body['SF6SourceMeta'])['group']
        other = next(item for item in source_objects
                     if json.loads(item['SF6SourceMeta'])['group'] != body_group)
        chosen = (body, other)
        for item in tuple(bpy.context.selected_objects):
            item.select_set(False)
        for item in chosen:
            item.select_set(True)
        options = dict(targetCollection=source_collection.name,
                       exportBlendShapes=False, exportAllLODs=False,
                       selectedOnly=True, splitLoopVertices=False,
                       rotate90=bool(source_collection['SF6SourceRotate']),
                       useBlenderMaterialName=False, preserveBoneMatrices=True,
                       exportBoundingBoxes=False, autoSolveRepeatedUVs=True,
                       preserveSharpEdges=True)
        old_path = debug_directory / 'legacy.mesh.230110883'
        new_path = debug_directory / 'snapshot.mesh.230110883'
        assert mesh_io.exportREMeshFile(str(old_path), options.copy())
        with evaluated.evaluated_hybrid_collection(
                source_collection, chosen, preserve_sharp_edges=True) as (snapshot, selected, report):
            assert mesh_io.exportREMeshFile(str(new_path), dict(
                options, targetCollection=snapshot.name, selectedOnly=False,
                preserveSharpEdges=False, autoSolveRepeatedUVs=False))
        sf6 = importlib.import_module(ROOT.name + '.modules.mesh.sf6_source')
        old_file, new_file = sf6.SourceMesh(old_path.read_bytes()), sf6.SourceMesh(new_path.read_bytes())
        differences = []
        for old_part in old_file.parts:
            material = old_file.materials[old_part['material']]
            new_part = next(part for part in new_file.parts if part['group'] == old_part['group']
                            and new_file.materials[part['material']] == material)
            old_stride, old_offset = old_file.elements[1]
            new_stride, new_offset = new_file.elements[1]
            old_packed = np.frombuffer(old_file.data, np.int8, old_part['count'] * old_stride,
                                       old_offset + old_part['start'] * old_stride).reshape(-1, old_stride)
            new_packed = np.frombuffer(new_file.data, np.int8, new_part['count'] * new_stride,
                                       new_offset + new_part['start'] * new_stride).reshape(-1, new_stride)
            changed = np.argwhere(old_packed != new_packed)
            differences.append(dict(material=material, group=old_part['group'],
                                    changed_lanes=len(changed),
                                    lane_counts=[int(np.count_nonzero(old_packed[:, i] != new_packed[:, i]))
                                                 for i in range(old_stride)],
                                    examples=[dict(row=int(row), lane=int(lane),
                                                   old=int(old_packed[row, lane]), new=int(new_packed[row, lane]))
                                              for row, lane in changed[:40]]))
        (debug_directory / 'normal-tangent-difference.json').write_text(
            json.dumps(differences, indent=2) + '\n', encoding='utf-8')
        print('SF6_NORMAL_TANGENT_DIFFERENCE ' + json.dumps(differences), flush=True)


print('SF6_EVALUATED_RESULT ' + json.dumps({
    'blender': bpy.app.version_string, 'checks': len(RESULTS), 'results': RESULTS}), flush=True)
