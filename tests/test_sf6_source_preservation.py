"""Run with Blender --background --factory-startup --python-exit-code 1.

Fixtures are user-supplied original SF6 meshes; no game assets are distributed.
See SF6_SOURCE_MODE.md for command-line usage.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
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


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--stub-addon-dir", type=Path)
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)
    fixtures = sorted(args.source_dir.glob("*.mesh.230110883"))
    if not fixtures:
        parser.error("No original .mesh.230110883 fixtures found")

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + ".modules.mesh.blender_re_mesh")
    sf6 = importlib.import_module(repo.name + ".modules.mesh.sf6_source")
    if args.stub_addon_dir:
        sys.path.insert(0, str(args.stub_addon_dir.resolve()))
        addon_utils.enable("game_modding_stub_tools", default_set=True)

    report = []

    def record(**result):
        report.append(result)
        print("PASS " + json.dumps(result), flush=True)

    def import_mesh(path):
        mesh_io.importREMeshFile(str(path), IMPORT_OPTIONS.copy())
        collection = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
        options = dict(targetCollection=collection.name, rotate90=True, exportBlendShapes=True)
        return collection, options

    def check_changes(original, result, allowed):
        assert len(result) == len(original), "Source allocation size changed"
        changes = {i for i, (a, b) in enumerate(zip(original, result)) if a != b}
        assert changes, "Expected the edit to change the exported mesh"
        assert changes <= allowed, "Edit changed unrelated source data"
        return len(changes)

    with tempfile.TemporaryDirectory(prefix="re-mesh-sf6-tests-") as temp:
        temp = Path(temp)
        for source_path in fixtures:
            collection, options = import_mesh(source_path)
            original = source_path.read_bytes()
            output = temp / source_path.name
            mesh_io.exportREMeshFile(str(output), options)
            assert output.read_bytes() == original, "Untouched round trip changed bytes"
            record(test="exact_roundtrip", file=source_path.name, bytes=len(original),
                   sha256=hashlib.sha256(original).hexdigest())

            if source_path.name == "esf033_002_01.mesh.230110883":
                jersey = next(o for o in collection.all_objects
                              if o.type == "MESH" and "Shirts" in o.name)
                keys = jersey.data.shape_keys.key_blocks
                vertex = 100
                saved = [key.data[vertex].co.copy() for key in keys]
                for key in keys:
                    key.data[vertex].co.x += 0.001
                mesh_io.exportREMeshFile(str(output), options)
                src = sf6.SourceMesh(original)
                part = json.loads(jersey[sf6.META])
                index = part["start"] + vertex
                offset = src.elements[0][1] + index * 12
                allowed = set(range(offset, offset + 12))
                # Float32 cancellation can change half-float deltas at this
                # vertex, but no other vertex or metadata may change.
                for _, _, ranges in src.part_shapes(part):
                    for start, delta_offset, count in ranges:
                        if start <= index < start + count:
                            offset = delta_offset + (index - start) * 8
                            allowed.update(range(offset, offset + 6))
                changes = check_changes(original, output.read_bytes(), allowed)
                for key, coordinate in zip(keys, saved):
                    key.data[vertex].co = coordinate
                record(test="jersey_vertex_edit", changed_bytes=changes,
                       unrelated_bytes_changed=0)

                sentinel = temp / "guard.mesh.230110883"
                sentinel.write_bytes(b"UNCHANGED")
                original_name = keys[1].name
                keys[1].name = "renamed_shape"
                try:
                    mesh_io.exportREMeshFile(str(sentinel), options)
                except ValueError:
                    assert sentinel.read_bytes() == b"UNCHANGED"
                else:
                    raise AssertionError("Renamed shape was not rejected")
                finally:
                    keys[1].name = original_name
                record(test="invalid_export_preserves_destination", passed=True)

            marker_names = {
                "esf033_001_01.mesh.230110883": "Group_20_Sub_0__esf_ClothA_HeadAcce",
                "esf033_002_01.mesh.230110883": "Group_0_Sub_8__esf_ClothB_Earrings",
            }
            if args.stub_addon_dir and source_path.name in marker_names:
                marker = next(o for o in collection.all_objects
                              if o.name == marker_names[source_path.name])
                for obj in bpy.context.selected_objects:
                    obj.select_set(False)
                marker.select_set(True)
                bpy.context.view_layer.objects.active = marker
                assert bpy.ops.caan.replace_selected_with_stub() == {"FINISHED"}
                assert sf6._is_hip_stub(marker)
                mesh_io.exportREMeshFile(str(output), options)
                candidate = output.read_bytes()
                src = sf6.SourceMesh(original)
                part = json.loads(marker[sf6.META])
                offset = src.face_offset + part["face_start"] * src.index_size
                allowed = set(range(offset, offset + part["faces"] * 3 * src.index_size))
                for kind in (0, 2, 4):
                    stride, offset = src.elements[kind]
                    offset += part["start"] * stride
                    allowed.update(range(offset, offset + part["count"] * stride))
                changes = check_changes(original, candidate, allowed)
                parsed = sf6.SourceMesh(candidate)
                assert np.array_equal(parsed.faces(part)[:2], ((0, 1, 2), (0, 2, 3)))
                assert np.array_equal(parsed.positions(part)[:4],
                                      sf6._from_blender(sf6._coords(marker.data.vertices), True))
                record(test="actual_C_Hip_stub", file=source_path.name,
                       changed_bytes=changes, unrelated_bytes_changed=0,
                       sha256=hashlib.sha256(candidate).hexdigest())

            if source_path.name == "esf033_002_01.mesh.230110883":
                mesh_io.exportREMeshFile(str(output), options)
                before_save = output.read_bytes()
                blend_path = temp / "source-preserved.blend"
                collection_name = collection.name
                bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
                bpy.ops.wm.open_mainfile(filepath=str(blend_path), load_ui=False)
                options["targetCollection"] = collection_name
                mesh_io.exportREMeshFile(str(output), options)
                assert output.read_bytes() == before_save
                record(test="save_reopen_export", byte_identical=True)

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("ALL_TESTS_PASSED", flush=True)


if __name__ == "__main__":
    main()
