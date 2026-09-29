"""Blender regression for sculpting a source-mapped SF6 part with shape keys.

Run in background Blender with ``-- --source-file ORIGINAL.mesh.230110883``.
The original game mesh is a local test fixture and is not distributed here.
"""

import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy
import numpy as np
from mathutils import Vector


IMPORT_OPTIONS = dict(
    clearScene=True, createCollections=True, loadMaterials=False,
    loadMDFData=False, loadShellFur=False, loadUnusedTextures=False,
    loadUnusedProps=False, useBackfaceCulling=False, reloadCachedTextures=False,
    mdfPath="", importAllLODs=False, importBlendShapes=True, rotate90=True,
    mergeArmature="", importArmatureOnly=False, mergeGroups=False,
    importShadowMeshes=False, importOcclusionMeshes=False,
    importBoundingBoxes=False,
)


def coords(data):
    values = np.empty(len(data) * 3, np.float32)
    data.foreach_get("co", values)
    return values.reshape((-1, 3))


def find_rounding_sculpt(sf6, source, collection):
    """Find a same-vector sculpt that crosses the old 1e-6 guard."""
    offsets = (
        (.013, -.027, .041), (.071, -.053, .019),
        (.17, -.11, .23), (1.13, -.79, .47),
        (8.13, -3.79, 2.47), (16.13, -8.79, 4.47),
        (32.13, -16.79, 8.47), (64.13, -32.79, 16.47),
    )
    rotate = bool(collection["SF6SourceRotate"])
    for obj in collection.all_objects:
        if obj.type != "MESH" or "SF6SourceMeta" not in obj:
            continue
        old = json.loads(obj["SF6SourceMeta"])
        if old["lod"] != 0 or not obj.data.shape_keys:
            continue
        source_shapes = list(source.part_shapes(old))
        if not source_shapes:
            continue
        keys = obj.data.shape_keys.key_blocks
        basis = coords(keys[0].data)
        ids = np.empty(len(basis), np.int32)
        obj.data.attributes["sf6_source_vertex"].data.foreach_get("value", ids)
        for name, source_delta, _ in source_shapes:
            key = coords(keys[name].data)
            for offset in offsets:
                move = np.asarray(offset, dtype=np.float32)
                moved_basis = (basis + move).astype(np.float32)
                moved_key = (key + move).astype(np.float32)
                actual = sf6._from_blender(moved_key - moved_basis, rotate)
                error = np.max(np.abs(actual - source_delta[ids]), axis=1)
                candidates = np.flatnonzero(
                    (error > 1e-6) & np.any(source_delta[ids] != 0, axis=1))
                if len(candidates):
                    # Reproduce the discrepancy at one known retail vertex,
                    # without deforming an entire part for this regression.
                    vi = int(candidates[0])
                    return obj, old, name, vi, move, float(error[vi])
    raise AssertionError("Fixture has no same-vector f32 sculpt crossing the old guard")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + ".modules.mesh.blender_re_mesh")
    sf6 = importlib.import_module(repo.name + ".modules.mesh.sf6_source")
    hybrid = importlib.import_module(repo.name + ".modules.mesh.sf6_hybrid")

    source_bytes = args.source_file.read_bytes()
    source = sf6.SourceMesh(source_bytes)
    assert mesh_io.importREMeshFile(str(args.source_file), IMPORT_OPTIONS.copy())
    collection = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
    target, old_part, shape_name, vi, move, old_guard_error = find_rounding_sculpt(
        sf6, source, collection)
    keys = target.data.shape_keys.key_blocks
    source_id = int(target.data.attributes["sf6_source_vertex"].data[vi].value)
    original_position = source.positions(old_part)[source_id].copy()
    original_key_positions = [key.data[vi].co.copy() for key in keys]

    # Model a sculpt propagated equally to Basis and every shape key. The
    # relative shape is unchanged mathematically; only f32 rounding differs.
    for key in keys:
        key.data[vi].co = Vector(key.data[vi].co) + Vector(move)
    rotate = bool(collection["SF6SourceRotate"])
    raw_delta = sf6._from_blender(
        (coords(keys[shape_name].data) - coords(keys[0].data))[vi:vi + 1], rotate)[0]
    source_delta = dict((name, delta) for name, delta, _ in source.part_shapes(old_part))
    assert np.max(np.abs(raw_delta - source_delta[shape_name][source_id])) > 1e-6

    options = dict(
        targetCollection=collection.name, selectedOnly=False, exportAllLODs=True,
        exportBlendShapes=False, rotate90=rotate, splitLoopVertices=False,
        useBlenderMaterialName=False, preserveBoneMatrices=True,
        exportBoundingBoxes=False, autoSolveRepeatedUVs=True,
        preserveSharpEdges=True,
    )
    with tempfile.TemporaryDirectory(prefix="sf6-hybrid-sculpt-rounding-") as temp:
        ordinary_path = Path(temp) / "ordinary" / args.source_file.name
        ordinary_path.parent.mkdir()
        assert mesh_io.exportREMeshFile(str(ordinary_path), options.copy())
        ordinary_bytes = ordinary_path.read_bytes()
        output_bytes, report = hybrid.build_hybrid_mesh(
            source_bytes, ordinary_bytes, collection)
        ordinary = sf6.SourceMesh(ordinary_bytes)
        output = sf6.SourceMesh(output_bytes)
        part_key = old_part["group"], source.materials[old_part["material"]]
        ordinary_part = next(part for part in ordinary.parts if part["lod"] == 0 and
                             (part["group"], ordinary.materials[part["material"]]) == part_key)
        output_part = next(part for part in output.parts if part["lod"] == 0 and
                           (part["group"], output.materials[part["material"]]) == part_key)
        assert np.array_equal(ordinary.positions(ordinary_part), output.positions(output_part))
        assert not np.array_equal(output.positions(output_part)[vi], original_position)
        output_shapes = {name: delta for name, delta, _ in output.part_shapes(output_part)}
        for name, delta in source_delta.items():
            assert np.array_equal(output_shapes[name][vi], delta[source_id]), name
        assert report["source_vertices"] > 0
        assert report["edited_source_shape_delta_entries"] == 0
        sculpt_part_report = next(part for part in report["parts"]
                                  if (part["group"], part["material"]) == part_key)
        assert sculpt_part_report["edited_source_shape_delta_entries"] == 0

        # Restore the source Basis and all keys, then edit one named key alone.
        # A fresh ordinary mesh isolates the deliberate key edit from the
        # rounding-only sculpt above.
        for key, position in zip(keys, original_key_positions):
            key.data[vi].co = position
        baseline_path = Path(temp) / "baseline" / args.source_file.name
        baseline_path.parent.mkdir()
        assert mesh_io.exportREMeshFile(str(baseline_path), options.copy())
        keys[shape_name].data[vi].co.x += .001
        edited_delta = sf6._from_blender(
            (coords(keys[shape_name].data) - coords(keys[0].data))[vi:vi + 1],
            rotate)[0]
        expected_encoded = edited_delta.astype("<f2").astype("<f4")
        assert not np.array_equal(expected_encoded, source_delta[shape_name][source_id])
        edited_bytes, edited_report = hybrid.build_hybrid_mesh(
            source_bytes, baseline_path.read_bytes(), collection)
        edited_mesh = sf6.SourceMesh(edited_bytes)
        edited_part = next(part for part in edited_mesh.parts if part["lod"] == 0 and
                           (part["group"], edited_mesh.materials[part["material"]]) == part_key)
        edited_shapes = {name: delta for name, delta, _ in edited_mesh.part_shapes(edited_part)}
        assert np.array_equal(edited_shapes[shape_name][vi], expected_encoded)
        retail_delta = source_delta[shape_name][source_id]
        changed_axes = np.flatnonzero(expected_encoded != retail_delta)
        assert len(changed_axes) == 1, (retail_delta, expected_encoded)
        untouched_axes = [axis for axis in range(3) if axis != changed_axes[0]]
        assert np.array_equal(edited_shapes[shape_name][vi][untouched_axes],
                              retail_delta[untouched_axes])
        assert edited_report["source_vertices"] > 0
        assert edited_report["edited_source_shape_delta_entries"] >= 1
        edited_part_report = next(part for part in edited_report["parts"]
                                  if (part["group"], part["material"]) == part_key)
        assert edited_part_report["edited_source_shape_delta_entries"] >= 1
        assert edited_part_report["edited_source_shape_vertices"] >= 1
        direct_path = Path(temp) / "direct" / args.source_file.name
        direct_path.parent.mkdir()
        direct_options = dict(options, exportBlendShapes=True, sf6HybridPreserve=True)
        assert mesh_io.exportREMeshFile(str(direct_path), direct_options)
        comparison = importlib.import_module(repo.name + '.tests.test_sf6_hybrid_export')
        comparison.hybrid_files_equal(edited_mesh, sf6.SourceMesh(direct_path.read_bytes()),
                                      sf6.SourceMesh(baseline_path.read_bytes()))

        # Push that same mapped key well beyond the retail shape target's
        # bounds. The new target bounds must include the decoded edited delta.
        keys[shape_name].data[vi].co.x += 2.0
        large_delta = sf6._from_blender(
            (coords(keys[shape_name].data) - coords(keys[0].data))[vi:vi + 1],
            rotate)[0]
        large_encoded = large_delta.astype("<f2").astype("<f4")
        large_bytes, large_report = hybrid.build_hybrid_mesh(
            source_bytes, baseline_path.read_bytes(), collection)
        large_mesh = sf6.SourceMesh(large_bytes)
        large_part = next(part for part in large_mesh.parts if part["lod"] == 0 and
                          (part["group"], large_mesh.materials[part["material"]]) == part_key)
        large_shapes = {name: delta for name, delta, _ in large_mesh.part_shapes(large_part)}
        assert np.array_equal(large_shapes[shape_name][vi], large_encoded)
        assert large_report["shape_target_bounds_expanded"] >= 1
        assert np.array_equal(large_shapes[shape_name][vi][untouched_axes],
                              retail_delta[untouched_axes])

    print("PASS " + json.dumps(dict(
        test="same_vector_sculpt_rounding_and_edited_key_delta",
        object=target.name, shape=shape_name, source_vertex=source_id,
        sculpt_vector=move.tolist(), old_guard_error=old_guard_error)), flush=True)


if __name__ == "__main__":
    main()
