"""Fixture-neutral export check for rebuilt vertices with transferred SF6 shapes.

Run in background Blender with ``-- --source-file ORIGINAL.mesh.230110883``.
The supplied fixture needs a shaped LOD0 part and is not bundled with the addon.
"""

import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy
from mathutils import Vector
import numpy as np


IMPORT_OPTIONS = dict(
    clearScene=True, createCollections=True, loadMaterials=False,
    loadMDFData=False, loadShellFur=False, loadUnusedTextures=False,
    loadUnusedProps=False, useBackfaceCulling=False, reloadCachedTextures=False,
    mdfPath="", importAllLODs=False, importBlendShapes=True, rotate90=True,
    mergeArmature="", importArmatureOnly=False, mergeGroups=False,
    importShadowMeshes=False, importOcclusionMeshes=False,
    importBoundingBoxes=False,
)


def part_key(mesh, part):
    return part["group"], mesh.materials[part["material"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + ".modules.mesh.blender_re_mesh")
    sf6 = importlib.import_module(repo.name + ".modules.mesh.sf6_source")
    hybrid = importlib.import_module(repo.name + ".modules.mesh.sf6_hybrid")

    source_bytes = args.source_file.read_bytes()
    source = sf6.SourceMesh(source_bytes)
    mesh_io.importREMeshFile(str(args.source_file), IMPORT_OPTIONS.copy())
    collection = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
    candidates = []
    for obj in collection.all_objects:
        if obj.type != "MESH" or "SF6SourceMeta" not in obj:
            continue
        part = json.loads(obj["SF6SourceMeta"])
        if part["lod"] == 0 and list(source.part_shapes(part)):
            candidates.append((part["count"], obj, part))
    assert candidates, "Fixture needs a shaped LOD0 mesh part"
    _, target, old_part = max(candidates, key=lambda item: item[0])
    assert target.data.shape_keys and len(target.data.shape_keys.key_blocks) > 1
    old_vertices = len(target.data.vertices)
    old_faces = len(target.data.polygons)
    old_ids = np.empty(old_vertices, np.int32)
    target.data.attributes["sf6_source_vertex"].data.foreach_get("value", old_ids)

    # Joining a new triangle creates three new vertices with zero shape deltas.
    # Alter one key on those vertices, leaving all source-mapped deltas exact.
    origin = target.data.vertices[0].co.copy()
    triangle_mesh = bpy.data.meshes.new("TransferredShapeTestTriangle")
    triangle_mesh.from_pydata(
        (origin + Vector((0, 0, .03)), origin + Vector((.03, 0, .03)),
         origin + Vector((0, .03, .03))), (), ((0, 1, 2),))
    triangle_mesh.update()
    triangle_mesh.materials.append(target.data.materials[0])
    for old_layer in target.data.uv_layers:
        layer = triangle_mesh.uv_layers.new(name=old_layer.name)
        for loop in layer.data:
            loop.uv = (.5, .5)
    triangle = bpy.data.objects.new("TransferredShapeTestTriangle", triangle_mesh)
    collection.objects.link(triangle)
    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    target.hide_set(False)
    target.select_set(True)
    triangle.select_set(True)
    bpy.context.view_layer.objects.active = target
    assert bpy.ops.object.join() == {"FINISHED"}
    assert len(target.data.vertices) == old_vertices + 3
    assert len(target.data.polygons) == old_faces + 1
    new_indices = list(range(old_vertices, old_vertices + 3))
    for group in target.data.vertices[0].groups:
        target.vertex_groups[group.group].add(new_indices, group.weight, "REPLACE")
    target.data.attributes["sf6_source_vertex"].data.foreach_set(
        "value", np.concatenate((old_ids, np.full(3, -1, np.int32))))
    target.data.attributes["sf6_source_face"].data[old_faces].value = -1
    keys = target.data.shape_keys.key_blocks
    moved_key = keys[1]
    for vi in new_indices:
        moved_key.data[vi].co = keys[0].data[vi].co + Vector((.25, 0, 0))

    options = dict(
        targetCollection=collection.name, selectedOnly=False, exportAllLODs=True,
        exportBlendShapes=False, rotate90=bool(collection["SF6SourceRotate"]),
        useBlenderMaterialName=False, preserveBoneMatrices=True,
        exportBoundingBoxes=False, autoSolveRepeatedUVs=True,
        preserveSharpEdges=True,
    )
    with tempfile.TemporaryDirectory(prefix="sf6-hybrid-shape-transfer-") as temp:
        root = Path(temp)
        ordinary_path = root / "ordinary" / args.source_file.name
        ordinary_path.parent.mkdir()
        assert mesh_io.exportREMeshFile(str(ordinary_path), options.copy())
        output_bytes, report = hybrid.build_hybrid_mesh(
            source_bytes, ordinary_path.read_bytes(), collection)
        chosen = part_key(source, old_part)
        part_report = next(part for part in report["parts"]
                           if (part["group"], part["material"]) == chosen)
        assert part_report["source_vertices"] == old_vertices
        assert part_report["rebuilt_vertices"] == 3
        assert part_report["transferred_vertices"] == 3
        assert part_report["transferred_shape_delta_entries"] == 3
        assert part_report["zero_delta_vertices"] == 0
        assert report["shape_target_bounds_expanded"] >= 1

        output = sf6.SourceMesh(output_bytes)
        new_part = next(part for part in output.parts
                        if part["lod"] == 0 and part_key(output, part) == chosen)
        source_shapes = {name: delta for name, delta, _ in source.part_shapes(old_part)}
        output_shapes = {name: delta for name, delta, _ in output.part_shapes(new_part)}
        assert source_shapes.keys() == output_shapes.keys()
        for name, delta in output_shapes.items():
            assert np.array_equal(delta[:old_vertices], source_shapes[name][old_ids])
            expected = np.array((.25, 0, 0) if name == moved_key.name else (0, 0, 0))
            assert np.array_equal(delta[old_vertices:], np.tile(expected, (3, 1)))

        direct_path = root / "hybrid" / args.source_file.name
        direct_path.parent.mkdir()
        assert mesh_io.exportREMeshFile(str(direct_path), dict(
            options, exportBlendShapes=True, sf6HybridPreserve=True))
        assert direct_path.read_bytes() == output_bytes
        mesh_io.importREMeshFile(str(direct_path), IMPORT_OPTIONS.copy())
        imported = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
        roundtrip_path = root / "roundtrip" / args.source_file.name
        roundtrip_path.parent.mkdir()
        assert mesh_io.exportREMeshFile(str(roundtrip_path), dict(
            targetCollection=imported.name, exportBlendShapes=True, rotate90=True))
        assert roundtrip_path.read_bytes() == output_bytes

    result = dict(test="transferred_new_vertices_export_and_roundtrip", passed=True,
                  part=chosen, transferred_vertices=3,
                  shape_target_bounds_expanded=report["shape_target_bounds_expanded"])
    print("PASS " + json.dumps(result), flush=True)
    if args.report_json:
        args.report_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
