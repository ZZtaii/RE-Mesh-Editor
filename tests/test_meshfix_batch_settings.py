"""Asset-free checks for batch mesh-fix controls in factory-startup Blender.

Run Blender --background --factory-startup --python-exit-code 1 --python
tests/test_meshfix_batch_settings.py. No live scene or game assets are used.
"""

import importlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import addon_utils
import bpy


DEFAULTS = dict(autoSolveRepeatedUVs=True, splitLoopVertices=True, normalizeWeights=True, limitTotal=False,
                limitTotalCount=8, shapeKeyExportMode="MODE0")


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    ops = importlib.import_module(repo.name + ".modules.mesh.re_mesh_operators")
    properties = ops.ExporterNodePropertyGroup.bl_rna.properties
    assert all(properties[name].default == value for name, value in DEFAULTS.items())
    assert properties["exportBlendShapes"].default is False
    print("PASS batch_meshfix_rna_defaults")

    collection = bpy.data.collections.new("MeshFixSettings.mesh")
    collection["~TYPE"] = "RE_MESH_COLLECTION"
    bpy.context.scene.collection.children.link(collection)

    class Items(list):
        def add(self):
            item = SimpleNamespace(path="")
            self.append(item)
            return item

    for version, count in ((230110883, 6), (241111606, 12), (240423143, 16), ("invalid", 8)):
        collection["BatchExport_path"] = "defaults.mesh." + str(version)
        items = Items()
        ops.populateCollectionList(items, collection, 0, "")
        assert items[0].limitTotalCount == count, (version, items[0].limitTotalCount)
    collection["BatchExport_limitTotalCount"] = 4
    collection["BatchExport_path"] = "custom.mesh.241111606"
    items = Items()
    ops.populateCollectionList(items, collection, 0, "")
    assert items[0].limitTotalCount == 4
    print("PASS batch_game_weight_defaults_and_saved_override")

    # Real RNA callbacks keep a fresh path's default current without resetting
    # user edits, even when the user enters the currently displayed default.
    bpy.types.Scene.meshfix_test_items = bpy.props.CollectionProperty(type=ops.ExporterNodePropertyGroup)
    try:
        real_items = bpy.context.scene.meshfix_test_items
        fresh = real_items.add()
        fresh.path = "fresh.mesh.230110883"
        fresh.exportType = "MESH"
        assert ops.getBatchMeshWeightLimit(fresh) == 6
        fresh.path = "fresh.mesh.241111606"
        assert fresh.limitTotalCount == 12
        fresh.limitTotalCount = 12  # Explicit choice equal to the automatic value.
        fresh.path = "fresh.mesh.240423143"
        assert fresh.limitTotalCount == 12
        ops.populateCollectionList(real_items, collection, 0, "")
        saved_item = real_items[-1]
        assert saved_item.limitTotalCount == 4
        saved_item.path = "custom.mesh.230110883"
        assert saved_item.limitTotalCount == 4
    finally:
        del bpy.types.Scene.meshfix_test_items
    print("PASS batch_path_defaults_keep_manual_and_saved_weight_limits")

    # Exercise Blender's real dictionary-to-RNA conversion. Update callbacks
    # must use registered properties rather than adding custom ID-property keys
    # while bpy.ops is constructing the collection from caller-supplied fields.
    values = {prop.identifier: prop.default for prop in properties
              if prop.identifier != "rna_type" and hasattr(prop, "default")}
    values.update(name=collection.name, path="conversion.mesh.230110883",
                  exportType="MESH", enabled=True, hasChild=False, invalid=True)
    assert bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[values]) == {"CANCELLED"}
    print("PASS batch_real_operator_dictionary_conversion")

    del collection["BatchExport_path"]
    # Saved new fields load independently of the old exportAllLODs sentinel.
    collection["BatchExport_autoSolveRepeatedUVs"] = False
    collection["BatchExport_normalizeWeights"] = False
    collection["BatchExport_limitTotalCount"] = 12
    collection["BatchExport_shapeKeyExportMode"] = "MODE1"
    items = Items()
    ops.populateCollectionList(items, collection, 0, "")
    assert items[0].splitLoopVertices is True and items[0].limitTotal is False
    assert items[0].autoSolveRepeatedUVs == False
    assert items[0].normalizeWeights == False and items[0].limitTotalCount == 12
    assert items[0].shapeKeyExportMode == "MODE1"
    assert items[0].exportBlendShapes is False
    print("PASS sparse_saved_batch_settings")

    with tempfile.TemporaryDirectory(prefix="meshfix-batch-") as temporary:
        basic = dict(name=collection.name, path=str(Path(temporary) / "output.mesh.230110883"),
                     exportType="MESH", enabled=True, invalid=False, hasChild=False,
                     exportAllLODs=True, exportBlendShapes=True, autoSolveRepeatedUVs=True,
                     preserveSharpEdges=True, rotate90=True, useBlenderMaterialName=False,
                     preserveBoneMatrices=False, exportBoundingBoxes=False)
        capture = []
        result = {"FINISHED"}

        class MeshOps:
            def exportfile(self, **kwargs):
                capture.append(kwargs)
                return result

        proxy = SimpleNamespace(context=bpy.context, data=bpy.data, app=bpy.app,
                                ops=SimpleNamespace(re_mesh=MeshOps()))

        def batch(item):
            return SimpleNamespace(skipPrompt=False, itemList_items=[SimpleNamespace(**item)],
                                   report=lambda *_args: None)

        selected = dict(autoSolveRepeatedUVs=False, splitLoopVertices=False, normalizeWeights=False, limitTotal=True,
                        limitTotalCount=6, shapeKeyExportMode="NO")
        with patch.object(ops, "bpy", proxy):
            assert ops.WM_OT_REBatchExporter.execute(batch(dict(basic, **selected)), bpy.context) == {"FINISHED"}
        assert all(capture[-1][name] == value for name, value in selected.items())
        assert capture[-1]["exportBlendShapes"] is True, "Wilds mode changed SF6 batch preservation"
        assert collection["BatchExport_preserveSource"] == True
        assert all(collection["BatchExport_" + name] == value for name, value in selected.items())
        reloaded = Items()
        ops.populateCollectionList(reloaded, collection, 0, "")
        assert reloaded[0].autoSolveRepeatedUVs == False
        print("PASS batch_forwards_and_saves_meshfix_controls")

        # A failed export retains the previous successfully saved choices.
        result = {"CANCELLED"}
        with patch.object(ops, "bpy", proxy):
            assert ops.WM_OT_REBatchExporter.execute(batch(dict(basic, **DEFAULTS)), bpy.context) == {"CANCELLED"}
        assert all(collection["BatchExport_" + name] == value for name, value in selected.items())
        print("PASS cancelled_batch_keeps_saved_meshfix_controls")

        # Legacy callers lack these attributes: their ordinary defaults still work.
        result = {"FINISHED"}
        with patch.object(ops, "bpy", proxy):
            assert ops.WM_OT_REBatchExporter.execute(batch(basic), bpy.context) == {"FINISHED"}
        expected_defaults = dict(DEFAULTS, limitTotalCount=6)
        assert all(capture[-1][name] == value for name, value in expected_defaults.items())
        print("PASS legacy_sparse_batch_uses_meshfix_defaults")


if __name__ == "__main__":
    main()
