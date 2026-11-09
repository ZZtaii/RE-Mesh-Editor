"""Asset-free batch-control checks using real RNA and the public review workflow.

Run Blender --background --factory-startup --python-exit-code 1 --python
 tests/test_meshfix_batch_settings.py. SF6_ADDON_TEST_ROOT may select a frozen
addon when this test is copied outside its normal tests directory.

Only the expensive mesh preview and writer are controlled test doubles. The
native operators, RNA callbacks, settings resolver, review controller, option
forwarding, result accounting and success-only persistence are real. Valid mesh
bytes and GUI interactions are covered by the separate integration suites.
"""

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
import addon_utils
import bpy


DEFAULTS = dict(autoSolveRepeatedUVs=True, splitLoopVertices=True, normalizeWeights=True, limitTotal=False,
                limitTotalCount=8, shapeKeyExportMode="MODE0")


def main():
    repo = Path(os.environ.get('SF6_ADDON_TEST_ROOT', Path(__file__).resolve().parents[1])).resolve()
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=False)
    addon = importlib.import_module(repo.name)
    assert Path(addon.__file__).resolve() == repo / '__init__.py'
    ops = importlib.import_module(repo.name + ".modules.mesh.re_mesh_operators")
    io = importlib.import_module(repo.name + ".modules.mesh.blender_re_mesh")
    plans = importlib.import_module(repo.name + ".modules.mesh.sf6_review_plan")
    properties = ops.ExporterNodePropertyGroup.bl_rna.properties
    assert all(properties[name].default == value for name, value in DEFAULTS.items())
    assert properties["exportBlendShapes"].default is False
    print("PASS batch_meshfix_rna_defaults")

    collection = bpy.data.collections.new("MeshFixSettings.mesh")
    collection["~TYPE"] = "RE_MESH_COLLECTION"
    bpy.context.scene.collection.children.link(collection)
    bpy.types.Scene.meshfix_test_items = bpy.props.CollectionProperty(type=ops.ExporterNodePropertyGroup)
    try:
        items = bpy.context.scene.meshfix_test_items

        def load():
            items.clear()
            ops.populateCollectionList(items, collection, 0, "")
            return items[0]

        for version, count in ((230110883, 6), (241111606, 12), (240423143, 16), ("invalid", 8)):
            collection["BatchExport_path"] = "defaults.mesh." + str(version)
            assert load().limitTotalCount == count
        collection["BatchExport_limitTotalCount"] = 4
        collection["BatchExport_path"] = "custom.mesh.241111606"
        assert load().limitTotalCount == 4
        print("PASS batch_game_weight_defaults_and_saved_override")

        items.clear()
        fresh = items.add()
        fresh.path = "fresh.mesh.230110883"
        fresh.exportType = "MESH"
        assert ops.getBatchMeshWeightLimit(fresh) == 6
        fresh.path = "fresh.mesh.241111606"
        assert fresh.limitTotalCount == 12
        fresh.limitTotalCount = 12  # Explicit choice equal to the automatic value.
        fresh.path = "fresh.mesh.240423143"
        assert fresh.limitTotalCount == 12
        saved_item = load()
        assert saved_item.limitTotalCount == 4
        saved_item.path = "custom.mesh.230110883"
        assert saved_item.limitTotalCount == 4
        print("PASS batch_path_defaults_keep_manual_and_saved_weight_limits")

        # Dictionary-to-RNA conversion is still real. A blocked headless review
        # returns INTERFACE, not an already completed or cancelled export.
        values = {prop.identifier: prop.default for prop in properties
                  if prop.identifier != "rna_type" and hasattr(prop, "default")}
        values.update(name=collection.name, path="conversion.mesh.230110883",
                      exportType="MESH", enabled=True, hasChild=False, invalid=True)
        before = dict(collection.items())
        assert bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[values]) == {"INTERFACE"}
        request = ops._LAST_BATCH_REVIEW
        assert request.state == 'blocked' and not request.approve(request.revision)
        assert request.review['report']['failed_count'] == 1
        assert dict(collection.items()) == before
        assert request.cancel()
        print("PASS batch_real_operator_dictionary_conversion")

        del collection["BatchExport_path"]
        collection["BatchExport_autoSolveRepeatedUVs"] = False
        collection["BatchExport_normalizeWeights"] = False
        collection["BatchExport_limitTotalCount"] = 12
        collection["BatchExport_shapeKeyExportMode"] = "MODE1"
        item = load()
        assert item.splitLoopVertices is True and item.limitTotal is False
        assert item.autoSolveRepeatedUVs is False
        assert item.normalizeWeights is False and item.limitTotalCount == 12
        assert item.shapeKeyExportMode == "MODE1" and item.exportBlendShapes is False
        print("PASS sparse_saved_batch_settings")

        with tempfile.TemporaryDirectory(prefix="meshfix-batch-") as temporary:
            destination = Path(temporary) / "output.mesh.230110883"
            basic = dict(name=collection.name, path=str(destination), exportType="MESH",
                         enabled=True, invalid=False, hasChild=False, exportAllLODs=True,
                         exportBlendShapes=True, autoSolveRepeatedUVs=True, preserveSharpEdges=True,
                         rotate90=True, useBlenderMaterialName=False, preserveBoneMatrices=False,
                         exportBoundingBoxes=False)
            capture = []
            preview_capture = []
            writer_succeeds = True
            test_output = b'BATCH_UNIT_TEST_WRITER_OUTPUT'

            def identity(path, options):
                # Review-only metadata must not change the test writer's plan.
                stable = {key: options[key] for key in ops._BATCH_OPTION_DEFAULTS}
                stable['targetCollection'] = options['targetCollection']
                stable['sf6LodPolicy'] = options.get('sf6LodPolicy')
                return hashlib.sha256(json.dumps([path, stable], sort_keys=True).encode()).hexdigest()

            def preview(path, options, exporter, *, staging_directory=None):
                assert exporter is writer
                preview_capture.append(dict(options))
                return dict(status='ready', complete=True, plan_token=identity(path, options),
                            output_sha256=hashlib.sha256(test_output).hexdigest(), losses=[],
                            approved_loss_tokens=[], requires_loss_approval=False,
                            duplicate_groups=[], retained_sections=[], report={'status': 'validated'})

            def writer(path, options):
                capture.append(dict(options))
                assert options['_sf6UnifiedExpectedPlan'] == identity(path, options)
                assert options['_sf6UnifiedExpectedOutputSHA256'] == hashlib.sha256(test_output).hexdigest()
                assert options['_sf6ApprovedCorrectiveLosses'] == []
                if not writer_succeeds:
                    options['_sf6UnifiedReport'] = dict(status='not_committed', reason='test_writer_cancelled')
                    return False
                Path(path).write_bytes(test_output)
                options['_sf6UnifiedReport'] = dict(status='committed', publication_started=True)
                return True

            def start(row, *, legacy_plain_object=False):
                if legacy_plain_object:
                    # Keep compatibility coverage for the old execute helper
                    # callers, without pretending a sparse object is real RNA.
                    operator = SimpleNamespace(skipPrompt=False,
                        itemList_items=[SimpleNamespace(**row)], report=lambda *_: None)
                    returned = ops.WM_OT_REBatchExporter.execute(operator, bpy.context)
                else:
                    # Blender's collection conversion requires every RNA key.
                    values = {prop.identifier: prop.default for prop in properties
                              if prop.identifier != 'rna_type' and hasattr(prop, 'default')}
                    values.update(row)
                    if 'limitTotalCount' not in row:
                        values['limitTotalCount'] = ops.getBatchMeshWeightLimitDefault(row['path'])
                    returned = bpy.ops.re_mesh.batch_exporter('EXEC_DEFAULT', itemList_items=[values])
                assert returned == {'INTERFACE'}
                request = ops._LAST_BATCH_REVIEW
                assert request.state == 'ready', request.review
                return request

            selected = dict(autoSolveRepeatedUVs=False, splitLoopVertices=False, normalizeWeights=False,
                            limitTotal=True, limitTotalCount=6, shapeKeyExportMode="NO")
            with patch.object(plans, 'prepare_review', preview), patch.object(io, 'exportREMeshFile', writer):
                before = dict(collection.items())
                request = start(dict(basic, **selected))
                assert not destination.exists() and not capture and dict(collection.items()) == before
                assert all(preview_capture[-1][name] == value for name, value in selected.items())
                assert request.approve(request.revision), request.result
                assert destination.read_bytes() == test_output and request.result['succeeded_count'] == 1
                assert all(capture[-1][name] == value for name, value in selected.items())
                assert capture[-1]['exportBlendShapes'] is True, 'Wilds mode changed SF6 batch preservation'
                assert collection['BatchExport_preserveSource'] is True
                assert all(collection['BatchExport_' + name] == value for name, value in selected.items())
                assert load().autoSolveRepeatedUVs is False
                print("PASS batch_forwards_and_saves_meshfix_controls")

                # The writer fails after approval. Neither the settings nor the
                # last-success destination may be changed by the failed attempt.
                writer_succeeds = False
                failed_path = Path(temporary) / 'failed.mesh.230110883'
                failed_path.write_bytes(b'PREVIOUS_OUTPUT')
                before = dict(collection.items())
                request = start(dict(basic, path=str(failed_path), **DEFAULTS))
                assert dict(collection.items()) == before
                assert not request.approve(request.revision)
                assert request.result['failed_count'] == 1 and request.result['succeeded_count'] == 0
                assert dict(collection.items()) == before and failed_path.read_bytes() == b'PREVIOUS_OUTPUT'
                print("PASS cancelled_batch_keeps_saved_meshfix_controls")

                # Retain the original sparse execute-caller compatibility check.
                # This is distinct from Blender's full-key RNA conversion above.
                writer_succeeds = True
                request = start(basic, legacy_plain_object=True)
                assert request.approve(request.revision), request.result
                expected_defaults = dict(DEFAULTS, limitTotalCount=6)
                assert all(capture[-1][name] == value for name, value in expected_defaults.items())
                print("PASS legacy_sparse_batch_uses_meshfix_defaults")

                before = dict(collection.items())
                old_bytes = destination.read_bytes()
                writes = len(capture)
                request = start(dict(basic, **selected))
                assert request.cancel()
                assert len(capture) == writes and destination.read_bytes() == old_bytes
                assert dict(collection.items()) == before and not ops._LAST_BATCH_RESULT['publication_started']
                print("PASS review_cancel_never_calls_writer_or_saves_settings")
    finally:
        del bpy.types.Scene.meshfix_test_items


if __name__ == "__main__":
    main()
