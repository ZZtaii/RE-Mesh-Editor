"""Regression for shaped SF6 parts replaced by tiny C_Hip planes.

Run in background Blender with ``-- --source-file ORIGINAL.mesh.230110883``.
The supplied source needs at least three shaped LOD0 parts and is not bundled.
"""

import argparse
import importlib
import json
from pathlib import Path
import struct
import sys
import tempfile

import addon_utils
import bpy
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

PLANE = ((-.0001, -.0001, 0), (.0001, -.0001, 0),
         (.0001, .0001, 0), (-.0001, .0001, 0))


def part_key(mesh, part):
    return part["group"], mesh.materials[part["material"]]


def lod0_parts(mesh):
    parts = {part_key(mesh, part): part for part in mesh.parts if part["lod"] == 0}
    assert len(parts) == sum(part["lod"] == 0 for part in mesh.parts)
    return parts


def ordinary_geometry_equal(ordinary, hybrid):
    """Keep all streams exact except the old exporter's bounded tangent noise."""
    repo_name = Path(__file__).resolve().parents[1].name
    comparison = importlib.import_module(repo_name + '.tests.test_sf6_hybrid_export')
    comparison.geometry_equal(ordinary, hybrid)


def replace_with_hip_plane(obj):
    """Keep source metadata while replacing the object's mesh as modders do."""
    old_mesh = obj.data
    mesh = bpy.data.meshes.new(obj.name + " C_Hip plane")
    mesh.from_pydata(PLANE, (), ((0, 1, 2, 3),))
    mesh.update()
    for material in old_mesh.materials:
        mesh.materials.append(material)
    for original_layer in old_mesh.uv_layers:
        layer = mesh.uv_layers.new(name=original_layer.name)
        for loop in layer.data:
            loop.uv = (.5, .5)
    for original_color in old_mesh.color_attributes:
        color = mesh.color_attributes.new(
            name=original_color.name, type=original_color.data_type,
            domain=original_color.domain)
        for entry in color.data:
            entry.color = (1, 1, 1, 1)
    obj.data = mesh
    for group in tuple(obj.vertex_groups):
        obj.vertex_groups.remove(group)
    hip = obj.vertex_groups.new(name="C_Hip")
    hip.add((0, 1, 2, 3), 1.0, "REPLACE")
    assert obj.get("SF6SourceMeta") and obj.get("SF6SourceSHA256")
    assert not mesh.shape_keys
    assert not mesh.attributes.get("sf6_source_vertex")
    assert not mesh.attributes.get("sf6_source_face")
    assert all(len(v.groups) == 1 and v.groups[0].weight == 1 for v in mesh.vertices)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)


def compare_shapes(source, output, stub_keys):
    source_parts, output_parts = lod0_parts(source), lod0_parts(output)
    assert source_parts.keys() >= output_parts.keys()
    retained = 0
    for key, new_part in output_parts.items():
        actual = list(output.part_shapes(new_part))
        if key in stub_keys:
            assert not actual, (key, [name for name, _, _ in actual])
            assert (new_part["count"], new_part["faces"]) == (4, 2), key
            continue
        original = list(source.part_shapes(source_parts[key]))
        assert [name for name, _, _ in actual] == [
            name for name, _, _ in original], key
        for (_, new_delta, _), (_, old_delta, _) in zip(actual, original):
            assert np.array_equal(new_delta, old_delta), key
        retained += len(actual)
    return retained


def export_pair(mesh_io, sf6, root, filename, options):
    ordinary_path = root / "ordinary" / filename
    hybrid_path = root / "hybrid" / filename
    ordinary_path.parent.mkdir(parents=True, exist_ok=True)
    hybrid_path.parent.mkdir(parents=True, exist_ok=True)
    assert mesh_io.exportREMeshFile(str(ordinary_path), dict(
        options, exportBlendShapes=False, splitLoopVertices=False))
    hybrid_options = dict(options, exportBlendShapes=True, sf6HybridPreserve=True)
    assert mesh_io.exportREMeshFile(str(hybrid_path), hybrid_options)
    ordinary = sf6.SourceMesh(ordinary_path.read_bytes())
    hybrid = sf6.SourceMesh(hybrid_path.read_bytes())
    ordinary_geometry_equal(ordinary, hybrid)
    assert struct.unpack_from("<Q", hybrid.data, 56)[0], "Missing normal table"
    return hybrid_path, hybrid, hybrid_options["_sf6HybridReport"]


def select(objects):
    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


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

    source = sf6.SourceMesh(args.source_file.read_bytes())
    assert source.blend_offset and source.shapes[0]
    mesh_io.importREMeshFile(str(args.source_file), IMPORT_OPTIONS.copy())
    collection = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
    assert collection.get("SF6PreserveSource")
    source_parts = lod0_parts(source)
    objects = {}
    for obj in collection.all_objects:
        if obj.type != "MESH" or "SF6SourceMeta" not in obj:
            continue
        part = json.loads(obj["SF6SourceMeta"])
        if part["lod"] == 0:
            objects[part_key(source, part)] = obj
    assert source_parts.keys() == objects.keys()
    shaped = [key for key, part in source_parts.items()
              if part["count"] >= 4 and list(source.part_shapes(part))]
    assert len(shaped) >= 3, "Fixture needs three shaped LOD0 parts"
    shaped.sort(key=lambda key: source_parts[key]["count"], reverse=True)
    survivor_key = shaped[0]
    stub_keys = set(shaped[1:3])
    for key in stub_keys:
        replace_with_hip_plane(objects[key])

    options = dict(
        targetCollection=collection.name, selectedOnly=False, exportAllLODs=True,
        rotate90=bool(collection["SF6SourceRotate"]),
        useBlenderMaterialName=False, preserveBoneMatrices=True,
        exportBoundingBoxes=False, autoSolveRepeatedUVs=True,
        preserveSharpEdges=True,
    )
    results = []
    with tempfile.TemporaryDirectory(prefix="sf6-hybrid-stub-shapes-") as temp:
        root = Path(temp)
        output_path, output, report = export_pair(
            mesh_io, sf6, root / "all", args.source_file.name, options)
        assert lod0_parts(output).keys() == source_parts.keys()
        retained = compare_shapes(source, output, stub_keys)
        assert retained > 0
        assert report["output_shape_links"] == len(output.shapes[0])
        for key in stub_keys:
            part_report = next(part for part in report["parts"]
                               if (part["group"], part["material"]) == key)
            assert part_report["shape_links"] == 0, (key, part_report)
            assert part_report["source_vertices"] == 0, (key, part_report)
            assert part_report["rebuilt_vertices"] == 4, (key, part_report)
        results.append(dict(test="full_export_omits_stub_shapes",
                            stubs=sorted(stub_keys), retained_shape_links=retained))

        for case, keys in (("stub_and_shaped", [*sorted(stub_keys), survivor_key]),
                           ("stub_only", sorted(stub_keys))):
            select([objects[key] for key in keys])
            _, selected, selected_report = export_pair(
                mesh_io, sf6, root / case, args.source_file.name,
                dict(options, selectedOnly=True))
            assert lod0_parts(selected).keys() == set(keys)
            selected_retained = compare_shapes(source, selected, stub_keys)
            assert selected_report["selected_only"] is True
            assert selected_report["selected_parts"] == len(keys)
            assert selected_report["output_shape_links"] == len(selected.shapes[0])
            assert bool(selected.blend_offset) == bool(selected_retained)
            results.append(dict(test="selected_" + case,
                                parts=len(keys), retained_shape_links=selected_retained))

        mesh_io.importREMeshFile(str(output_path), IMPORT_OPTIONS.copy())
        imported = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
        roundtrip_path = root / "roundtrip" / args.source_file.name
        roundtrip_path.parent.mkdir()
        assert mesh_io.exportREMeshFile(str(roundtrip_path), dict(
            targetCollection=imported.name, exportBlendShapes=True, rotate90=True))
        assert roundtrip_path.read_bytes() == output_path.read_bytes()
        results.append(dict(test="fresh_import_preserve_roundtrip_is_byte_identical"))

    for result in results:
        print("PASS " + json.dumps(result), flush=True)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
