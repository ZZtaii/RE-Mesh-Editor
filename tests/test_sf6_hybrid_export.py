"""Fixture-neutral integration checks for SF6 hybrid export.

Run with Blender --background --factory-startup --python-exit-code 1 and
--source-file pointing to a local original SF6 mesh with LOD0 shapes. The
fixture is supplied by the tester and is never distributed with the addon.
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


def key(mesh, part):
    return part["group"], mesh.materials[part["material"]]


def lod0_parts(mesh):
    parts = {key(mesh, part): part for part in mesh.parts if part["lod"] == 0}
    assert len(parts) == sum(part["lod"] == 0 for part in mesh.parts)
    return parts


def geometry_equal(a, b):
    """The hybrid writer must not alter the ordinary export's actual mesh."""
    aa, bb = lod0_parts(a), lod0_parts(b)
    assert aa.keys() == bb.keys()
    assert a.materials == b.materials
    assert a.elements.keys() <= b.elements.keys()
    for part_key in aa:
        old, new = aa[part_key], bb[part_key]
        assert (old["count"], old["faces"]) == (new["count"], new["faces"])
        assert np.array_equal(a.faces(old), b.faces(new)), part_key
        for kind, (stride, offset) in a.elements.items():
            new_stride, new_offset = b.elements[kind]
            assert new_stride == stride
            start, count = old["start"], old["count"]
            new_start = new["start"]
            assert (a.data[offset + start * stride:offset + (start + count) * stride]
                    == b.data[new_offset + new_start * stride:
                              new_offset + (new_start + count) * stride]), (part_key, kind)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    assert args.source_file.is_file()
    source_bytes = args.source_file.read_bytes()

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + ".modules.mesh.blender_re_mesh")
    sf6 = importlib.import_module(repo.name + ".modules.mesh.sf6_source")
    hybrid = importlib.import_module(repo.name + ".modules.mesh.sf6_hybrid")
    source = sf6.SourceMesh(source_bytes)
    assert source.blend_offset and source.shapes[0], "Fixture needs LOD0 shapes"
    mesh_io.importREMeshFile(str(args.source_file), IMPORT_OPTIONS.copy())
    collection = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
    assert collection.get("SF6PreserveSource")
    results = []

    def passed(test, **details):
        results.append(dict(test=test, passed=True, **details))
        print("PASS " + json.dumps(results[-1]), flush=True)

    with tempfile.TemporaryDirectory(prefix="sf6-hybrid-tests-") as temporary:
        root = Path(temporary)
        original = root / args.source_file.name
        preserve = dict(targetCollection=collection.name, exportBlendShapes=True,
                        rotate90=bool(collection["SF6SourceRotate"]))
        assert mesh_io.exportREMeshFile(str(original), preserve)
        assert original.read_bytes() == source_bytes
        passed("strict_preserve_unchanged_for_original_import")

        ordinary_path = root / "ordinary" / args.source_file.name
        ordinary_path.parent.mkdir()
        ordinary_options = dict(preserve, exportBlendShapes=False,
                                selectedOnly=False, exportAllLODs=True,
                                useBlenderMaterialName=False,
                                preserveBoneMatrices=True,
                                exportBoundingBoxes=False,
                                autoSolveRepeatedUVs=True,
                                preserveSharpEdges=True)
        assert mesh_io.exportREMeshFile(str(ordinary_path), ordinary_options)
        ordinary_bytes = ordinary_path.read_bytes()
        ordinary = sf6.SourceMesh(ordinary_bytes)
        assert ordinary.lod_count == 1 and not ordinary.blend_offset
        passed("ordinary_mode_still_exports_without_shapes")

        output_bytes, report = hybrid.build_hybrid_mesh(
            source_bytes, ordinary_bytes, collection)
        output = sf6.SourceMesh(output_bytes)
        geometry_equal(ordinary, output)
        assert output.lod_count == 1
        assert [name for name, _ in output.shapes[0]] == [
            name for name, _ in source.shapes[0]]
        assert struct.unpack_from("<Q", output.data, 56)[0], "Missing normal table"
        source_parts, output_parts = lod0_parts(source), lod0_parts(output)
        assert source_parts.keys() == output_parts.keys()
        for part_key in source_parts:
            original_shapes = list(source.part_shapes(source_parts[part_key]))
            generated_shapes = list(output.part_shapes(output_parts[part_key]))
            assert [name for name, _, _ in generated_shapes] == [
                name for name, _, _ in original_shapes], part_key
            for (_, original_delta, _), (_, generated_delta, _) in zip(
                    original_shapes, generated_shapes):
                assert np.array_equal(original_delta, generated_delta), part_key
        assert report["mode"] == "hybrid_lod0"
        assert report["full_source_preservation"] is False
        assert report["output_shape_links"] == len(source.shapes[0])
        assert report["source_vertices"] + report["rebuilt_vertices"] == sum(
            part["count"] for part in output.parts)
        assert report["source_triangles"] + report["rebuilt_triangles"] == sum(
            part["faces"] for part in output.parts)
        assert len(report["parts"]) == len(output.parts)
        passed("hybrid_keeps_original_shapes_and_ordinary_geometry",
               parts=len(output.parts), shape_links=len(output.shapes[0]))

        target = root / "hybrid" / args.source_file.name
        target.parent.mkdir()
        assert mesh_io.exportREMeshFile(str(target), dict(
            ordinary_options, exportBlendShapes=True, sf6HybridPreserve=True))
        assert target.read_bytes() == output_bytes
        passed("opt_in_direct_export_matches_core")

        # Selection must use the selected retail parts alone, including a
        # shape-free result with a valid LOD0 normal table.  Pick parts by
        # source structure so this test contains no bundled game fixture.
        source_parts = lod0_parts(source)
        shaped = [part_key for part_key, part in source_parts.items()
                  if list(source.part_shapes(part))]
        plain = [part_key for part_key, part in source_parts.items()
                 if not list(source.part_shapes(part)) and part["count"] > 100]
        by_part = {}
        for obj in collection.all_objects:
            if obj.type != "MESH" or "SF6SourceMeta" not in obj:
                continue
            part = json.loads(obj["SF6SourceMeta"])
            if part["lod"] == 0:
                by_part[(part["group"], source.materials[part["material"]])] = obj
        assert source_parts.keys() == by_part.keys()
        assert shaped and plain, "Fixture needs shaped and unshaped LOD0 parts"
        primary = max(shaped, key=lambda part_key: source_parts[part_key]["count"])
        secondary = next((part_key for part_key in shaped
                          if part_key[0] != primary[0]), None)
        selected_cases = {"one_shaped": [primary],
                          "one_plain": [min(plain, key=lambda part_key:
                                            source_parts[part_key]["count"])]}
        if secondary is not None:
            selected_cases["two_shaped_groups"] = [primary, secondary]
        for case, chosen in selected_cases.items():
            for obj in tuple(bpy.context.selected_objects):
                obj.select_set(False)
            for part_key in chosen:
                by_part[part_key].select_set(True)
            ordinary_selected = root / (case + "-ordinary") / args.source_file.name
            hybrid_selected = root / (case + "-hybrid") / args.source_file.name
            ordinary_selected.parent.mkdir()
            hybrid_selected.parent.mkdir()
            selected_options = dict(ordinary_options, selectedOnly=True)
            assert mesh_io.exportREMeshFile(str(ordinary_selected), selected_options)
            hybrid_options = dict(selected_options, exportBlendShapes=True,
                                  sf6HybridPreserve=True)
            assert mesh_io.exportREMeshFile(str(hybrid_selected), hybrid_options)
            ordinary_part = sf6.SourceMesh(ordinary_selected.read_bytes())
            result = sf6.SourceMesh(hybrid_selected.read_bytes())
            geometry_equal(ordinary_part, result)
            assert lod0_parts(result).keys() == set(chosen)
            expected_links = 0
            for part_key in chosen:
                expected = list(source.part_shapes(source_parts[part_key]))
                actual = list(result.part_shapes(lod0_parts(result)[part_key]))
                assert [name for name, _, _ in actual] == [
                    name for name, _, _ in expected]
                for (_, old_delta, _), (_, new_delta, _) in zip(expected, actual):
                    assert np.array_equal(old_delta, new_delta), (case, part_key)
                expected_links += len(expected)
            assert len(result.shapes[0]) == expected_links
            assert bool(result.blend_offset) == bool(expected_links)
            assert struct.unpack_from("<Q", result.data, 56)[0]
            summary = hybrid_options["_sf6HybridReport"]
            assert summary["selected_only"] is True
            assert summary["selected_parts"] == len(chosen)
            assert summary["output_shape_links"] == expected_links
            passed("selected_hybrid_" + case, parts=len(chosen),
                   shape_links=expected_links)

        mesh_io.importREMeshFile(str(target), IMPORT_OPTIONS.copy())
        imported = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
        roundtrip = root / "roundtrip" / args.source_file.name
        roundtrip.parent.mkdir()
        assert mesh_io.exportREMeshFile(str(roundtrip), dict(
            targetCollection=imported.name, exportBlendShapes=True, rotate90=True))
        assert roundtrip.read_bytes() == output_bytes
        passed("fresh_hybrid_import_strict_preserve_is_byte_identical")

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("ALL_HYBRID_TESTS_PASSED " + str(len(results)), flush=True)


if __name__ == "__main__":
    main()
