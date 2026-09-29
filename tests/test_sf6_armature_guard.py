"""Regression checks for preserved export with different scene armatures.

Run with Blender --background --factory-startup --python-exit-code 1. The
source fixture is a user-supplied original SF6 mesh, not part of the addon.
"""

import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy
from mathutils import Matrix


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])

    if not args.source_file and not args.source_dir:
        parser.error("Provide --source-file or --source-dir")
    source_path = args.source_file or args.source_dir / "esf033_002_01.mesh.230110883"
    if not source_path.is_file():
        parser.error("Missing original SF6 fixture: " + str(source_path))

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + ".modules.mesh.blender_re_mesh")
    source_mode = importlib.import_module(repo.name + ".modules.mesh.sf6_source")

    mesh_io.importREMeshFile(str(source_path), IMPORT_OPTIONS.copy())
    collection = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
    options = dict(targetCollection=collection.name, rotate90=True,
                   exportBlendShapes=True)
    source_armatures = [obj for obj in collection.all_objects if obj.type == "ARMATURE"]
    assert len(source_armatures) == 1, "Fixture import should make one source armature"
    source_armature = source_armatures[0]
    assert len(source_armature.data.bones) > 0
    assert json.dumps(source_mode._armature_signature(source_armature)) == collection["SF6ArmatureSignature"]

    report = []

    def passed(name, **details):
        result = dict(test=name, passed=True, **details)
        report.append(result)
        print("PASS " + json.dumps(result), flush=True)

    with tempfile.TemporaryDirectory(prefix="sf6-armature-guard-") as temp_dir:
        output = Path(temp_dir) / source_path.name
        original = source_path.read_bytes()
        mesh_io.exportREMeshFile(str(output), options)
        assert output.read_bytes() == original
        passed("baseline_preserved_roundtrip")

        # This armature is unrelated to the imported mesh. Its empty skeleton
        # and nonidentity transform must not replace or invalidate the intact
        # source armature simply because it is linked to the same collection.
        unrelated_data = bpy.data.armatures.new("UnrelatedEmptyArmature")
        unrelated = bpy.data.objects.new("A_UnrelatedEmptyArmature", unrelated_data)
        collection.objects.link(unrelated)
        unrelated.matrix_world = Matrix.Translation((1.0, 0.0, 0.0))
        assert len(unrelated.data.bones) == 0
        assert any(obj == source_armature for obj in collection.all_objects)
        mesh_io.exportREMeshFile(str(output), options)
        assert output.read_bytes() == original
        passed("unrelated_empty_armature_does_not_block_preservation")

        # A source-preserving export writes the embedded source skeleton and
        # weights. A different Blender rig used for preview must not block it.
        # Rename thirty bones on a copy before it has any mesh children, then
        # replace the collection's rig and reparent its meshes to that copy.
        hybrid_data = source_armature.data.copy()
        hybrid = bpy.data.objects.new("HybridArmature", hybrid_data)
        collection.objects.link(hybrid)
        for bone in list(hybrid_data.bones)[:30]:
            bone.name += "_OtherCostume"
        assert json.dumps(source_mode._armature_signature(hybrid)) != collection["SF6ArmatureSignature"]
        source_meshes = [obj for obj in collection.all_objects
                         if obj.type == "MESH" and source_mode.META in obj]
        for obj in source_meshes:
            if obj.parent == source_armature:
                world = obj.matrix_world.copy()
                obj.parent = hybrid
                obj.matrix_world = world
            for modifier in obj.modifiers:
                if modifier.type == "ARMATURE" and modifier.object == source_armature:
                    modifier.object = hybrid
        collection.objects.unlink(source_armature)
        assert not any(json.dumps(source_mode._armature_signature(obj)) == collection["SF6ArmatureSignature"]
                       for obj in collection.all_objects if obj.type == "ARMATURE")
        assert all(obj.matrix_world == Matrix.Identity(4) for obj in source_meshes)
        mesh_io.exportREMeshFile(str(output), options)
        assert output.read_bytes() == original
        passed("hybrid_armature_exports_embedded_original_skeleton_and_weights",
               renamed_bones=min(30, len(hybrid_data.bones)), byte_identical=True)

        # Joining a new triangle into an original part must still be rejected
        # because those vertices/faces have no valid source identity.
        target = next((obj for obj in source_meshes if not obj.data.shape_keys), None)
        assert target is not None, "Fixture needs a source part without shape keys"
        foreign_data = bpy.data.meshes.new("ForeignTriangleData")
        foreign_data.from_pydata(((0.0, 0.0, 0.0), (0.1, 0.0, 0.0), (0.0, 0.1, 0.0)),
                                 (), ((0, 1, 2),))
        foreign_data.update()
        if target.data.materials:
            foreign_data.materials.append(target.data.materials[0])
        foreign = bpy.data.objects.new("ForeignJoinedTriangle", foreign_data)
        foreign_name = foreign.name
        collection.objects.link(foreign)
        for obj in list(bpy.context.selected_objects):
            obj.select_set(False)
        target.hide_set(False)
        target.select_set(True)
        foreign.select_set(True)
        bpy.context.view_layer.objects.active = target
        before_vertices = len(target.data.vertices)
        assert bpy.ops.object.join() == {"FINISHED"}
        assert len(target.data.vertices) == before_vertices + 3
        assert foreign_name not in bpy.data.objects
        output.write_bytes(b"UNCHANGED")
        try:
            mesh_io.exportREMeshFile(str(output), options)
        except ValueError as error:
            reason = str(error)
            assert any(fragment in reason for fragment in
                       ("Source identity", "Added/duplicated vertices",
                        "Added/duplicated faces", "Rewired topology")), reason
        else:
            raise AssertionError("Joined foreign geometry was exported with source preservation")
        assert output.read_bytes() == b"UNCHANGED"
        passed("joined_foreign_geometry_rejected_without_overwrite", reason=reason)

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("ALL_TESTS_PASSED", flush=True)


if __name__ == "__main__":
    main()
