"""Fixture-neutral Blender integration checks for evaluated SF6 correctives.

Run in background Blender with ``-- --source-file ORIGINAL.mesh.230110883``.
The local retail fixture is supplied by the tester and is never distributed.
Expected modifier results are sampled independently from private Blender clones.
"""

import argparse
from contextlib import contextmanager
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


def coords(data):
    values = np.empty((len(data), 3), np.float32)
    data.foreach_get("co", values.ravel())
    return values


def part_key(mesh, part):
    return part["group"], mesh.materials[part["material"]]


def lod0_parts(mesh):
    result = {part_key(mesh, part): part for part in mesh.parts if part["lod"] == 0}
    assert len(result) == sum(part["lod"] == 0 for part in mesh.parts)
    return result


def geometry_equal(ordinary, hybrid, compare_shading=True):
    before, after = lod0_parts(ordinary), lod0_parts(hybrid)
    assert before.keys() == after.keys() and ordinary.materials == hybrid.materials
    for key, old in before.items():
        new = after[key]
        assert (old["count"], old["faces"]) == (new["count"], new["faces"]), key
        assert np.array_equal(ordinary.faces(old), hybrid.faces(new)), key
        for kind, (stride, offset) in ordinary.elements.items():
            if kind == 1 and not compare_shading:
                continue
            new_stride, new_offset = hybrid.elements[kind]
            assert new_stride == stride
            assert (ordinary.data[offset + old["start"] * stride:
                                  offset + (old["start"] + old["count"]) * stride]
                    == hybrid.data[new_offset + new["start"] * stride:
                                   new_offset + (new["start"] + new["count"]) * stride]), (key, kind)


def validate_target_bounds(mesh):
    """Every decoded half-float target delta must lie in its stored AABB."""
    _, table = struct.unpack_from("<QQ", mesh.data, mesh.blend_offset)
    lod = struct.unpack_from("<Q", mesh.data, table)[0]
    targets = struct.unpack_from("<H", mesh.data, lod)[0]
    target_ptr, aabb_ptr = struct.unpack_from("<QQ", mesh.data, lod + 16)
    for ti in range(targets):
        ss, number, _, _, _, _ = struct.unpack_from("<HHHBBQ", mesh.data, target_ptr + ti * 16)
        bounds = np.frombuffer(mesh.data, "<f4", 8, aabb_ptr + ti * 32).reshape(2, 4)
        assert np.isfinite(bounds).all()
        for _, ranges in mesh.shapes[0][ss:ss + number]:
            for _, offset, length in ranges:
                delta = np.frombuffer(mesh.data, "<f2", length * 4, offset).reshape(-1, 4)[:, :3].astype("<f4")
                assert np.all(delta >= bounds[0, :3]) and np.all(delta <= bounds[1, :3]), (ti, bounds)
    return targets


def output_row_mapping(mesh, part, basis, triangles, rotate, sf6):
    """Recover correspondence from preserved triangle corners, never proximity."""
    faces = mesh.faces(part)
    assert faces.shape == triangles.shape
    mapping = np.full(part["count"], -1, np.int32)
    for original, output in zip(triangles.ravel(), faces.ravel()):
        prior = mapping[output]
        assert prior in (-1, original), "A split output row mixes evaluated source rows"
        mapping[output] = original
    assert np.all(mapping >= 0), "Fixture contains an unreferenced exported vertex"
    expected = sf6._from_blender(basis[mapping], rotate)
    assert np.allclose(mesh.positions(part), expected, atol=1e-6, rtol=0)
    return mapping


def validate_corner_uvs(mesh, part, layers):
    """Each exported triangle corner must keep its independently sampled UV."""
    faces = mesh.faces(part)
    for layer_index, expected in enumerate(layers[:2]):
        stride, offset = mesh.elements[2 + layer_index]
        assert stride == 4
        actual = np.frombuffer(mesh.data, "<f2", part["count"] * 2,
                               offset + part["start"] * stride).reshape(-1, 2)
        wanted = expected.astype("<f2")
        wanted[:, :, 1] *= -1
        wanted[:, :, 1] += 1
        assert np.array_equal(actual[faces], wanted), "Export changed a corner UV"


def validate_evaluated_corner_normals(mesh, part, evaluated_normals, rotate, sf6):
    """Preserve modifier shading through triangulation and split-normal encoding."""
    stride, offset = mesh.elements[1]
    assert stride == 8
    lanes = np.frombuffer(mesh.data, "<i1", part["count"] * 8,
                          offset + part["start"] * stride).reshape(-1, 8)
    desired = sf6._from_blender(evaluated_normals.reshape(-1, 3), rotate)
    wanted = np.floor(desired * 127).astype(np.int16).reshape(evaluated_normals.shape)
    actual = lanes[mesh.faces(part), :3].astype(np.int16)
    # Blender compresses normals_split_custom_set into its own normal spaces.
    # At the game's coarser SNORM8 grid this can cross one rounding boundary.
    error = np.abs(actual - wanted)
    assert np.max(error, initial=0) <= 1, "Export changed evaluated corner shading"
    assert np.all(lanes[:, 3] == 0)
    return int(np.max(error, initial=0))


def select(objects):
    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def scene_state(collection):
    """Key/mesh/UI preservation is observable even after rejected exports."""
    result = dict(selected=sorted(obj.name for obj in bpy.context.selected_objects),
                  active=bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
                  objects={})
    for obj in collection.all_objects:
        if obj.type != "MESH":
            continue
        keys = obj.data.shape_keys
        uv_layers = []
        for layer in obj.data.uv_layers:
            values = np.empty(len(layer.data) * 2, np.float32)
            layer.data.foreach_get("uv", values)
            uv_layers.append((layer.name, values.tobytes()))
        result["objects"][obj.name] = dict(
            mesh=obj.data.name, vertices=coords(obj.data.vertices).tobytes(),
            uv_layers=uv_layers,
            weights=[tuple((group.group, group.weight) for group in vertex.groups) for vertex in obj.data.vertices],
            group_names=tuple(group.name for group in obj.vertex_groups),
            active_shape=obj.active_shape_key_index, show_only=obj.show_only_shape_key,
            modifiers=[(m.name, m.type, m.show_viewport, m.show_render) for m in obj.modifiers],
            keys=[(key.name, key.value, key.mute, key.slider_min, key.slider_max,
                   key.vertex_group, key.relative_key.name, coords(key.data).tobytes())
                  for key in keys.key_blocks] if keys else None)
    return result


@contextmanager
def zero_values(collection):
    stored = []
    armatures = []
    for obj in collection.all_objects:
        if obj.type == "MESH":
            # Preserve the legacy unmodified rest-armature bytes. A genuinely
            # modified part is sampled without baking its preview pose.
            if any(modifier.type != "ARMATURE" and modifier.show_viewport for modifier in obj.modifiers):
                for modifier in obj.modifiers:
                    if modifier.type == "ARMATURE":
                        armatures.append((modifier, modifier.show_viewport))
                        modifier.show_viewport = False
        if obj.type != "MESH" or not obj.data.shape_keys:
            continue
        keys = obj.data.shape_keys.key_blocks
        stored.append((obj, obj.active_shape_key_index, obj.show_only_shape_key,
                       [(key, key.value, key.mute) for key in keys]))
        obj.active_shape_key_index = 0
        obj.show_only_shape_key = False
        for key in keys:
            key.value = 0
            key.mute = False
    bpy.context.view_layer.update()
    try:
        yield
    finally:
        for obj, active, show_only, keys in stored:
            obj.active_shape_key_index = active
            obj.show_only_shape_key = show_only
            for key, value, mute in keys:
                key.value, key.mute = value, mute
        for modifier, show_viewport in armatures:
            modifier.show_viewport = show_viewport
        bpy.context.view_layer.update()


def evaluate_oracle(obj, mesh_io):
    """Use private evaluated clones, independent of the new export adapter."""
    clone = obj.copy()
    original_mesh = obj.data.copy()
    clone.data = original_mesh
    bpy.context.scene.collection.objects.link(clone)
    meshes = []
    try:
        keys = clone.data.shape_keys
        if keys:
            keys.animation_data_clear()
            for key in keys.key_blocks:
                key.value = 0
                key.mute = False
        for modifier in clone.modifiers:
            if modifier.type == "ARMATURE":
                modifier.show_viewport = False
        clone.show_only_shape_key = True
        samples, signatures = {}, {}
        for index, key in enumerate(keys.key_blocks):
            clone.active_shape_key_index = index
            clone.data.update()
            bpy.context.view_layer.update()
            evaluated = clone.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True,
                                                 depsgraph=bpy.context.evaluated_depsgraph_get())
            meshes.append(mesh)
            samples[key.name] = coords(mesh.vertices)
            signatures[key.name] = tuple(tuple(poly.vertices) for poly in mesh.polygons)
        basis = meshes[0]
        polygon_count = len(basis.polygons)
        original_normals = {
            (poly.index, basis.loops[loop].vertex_index):
                tuple(basis.corner_normals[loop].vector)
            for poly in basis.polygons for loop in poly.loop_indices
        }
        provenance = basis.attributes.new("__oracle_polygon", "INT", "FACE")
        provenance.data.foreach_set("value", np.arange(polygon_count, dtype=np.int32))
        mesh_io.triangulateMesh(basis)
        triangles = np.asarray([tuple(poly.vertices) for poly in basis.polygons], dtype=np.int32)
        corner_uvs = [np.asarray([[tuple(layer.data[loop].uv) for loop in poly.loop_indices]
                                 for poly in basis.polygons], np.float32) for layer in basis.uv_layers]
        provenance = basis.attributes["__oracle_polygon"]
        corner_normals = np.asarray([
            [original_normals[(provenance.data[poly.index].value, basis.loops[loop].vertex_index)]
             for loop in poly.loop_indices] for poly in basis.polygons], np.float32)
        return samples, signatures, triangles, polygon_count, corner_uvs, corner_normals
    finally:
        bpy.data.objects.remove(clone, do_unlink=True)
        for mesh in meshes + [original_mesh]:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)


def modifier_case(obj, name):
    if name == "subdivision_smooth_stack":
        return (modifier_case(obj, "subdivision_simple"), modifier_case(obj, "smooth"))
    if name.startswith("subdivision_"):
        modifier = obj.modifiers.new(name, "SUBSURF")
        modifier.subdivision_type = "SIMPLE" if name.endswith("simple") else "CATMULL_CLARK"
        modifier.levels = modifier.render_levels = 1
    elif name == "simple_deform":
        modifier = obj.modifiers.new(name, "SIMPLE_DEFORM")
        modifier.deform_method = "TWIST"
        modifier.deform_axis = "Z"
        modifier.angle = .3
    elif name == "smooth":
        modifier = obj.modifiers.new(name, "SMOOTH")
        modifier.factor, modifier.iterations = .25, 2
    elif name == "displace":
        modifier = obj.modifiers.new(name, "DISPLACE")
        modifier.direction, modifier.strength, modifier.mid_level = "X", .02, 0
    elif name == "mirror":
        modifier = obj.modifiers.new(name, "MIRROR")
        modifier.use_axis = (True, False, False)
        modifier.use_mirror_merge = False
    elif name == "array":
        modifier = obj.modifiers.new(name, "ARRAY")
        modifier.count = 2
        modifier.use_relative_offset = False
        modifier.use_constant_offset = True
        modifier.constant_offset_displace = (.4, 0, 0)
        modifier.use_merge_vertices = False
    elif name == "solidify":
        modifier = obj.modifiers.new(name, "SOLIDIFY")
        modifier.thickness, modifier.offset = .005, 0
        modifier.use_even_offset = False
        # Dense, disconnected rim triangles may exceed SF6's ten-bit normal
        # palette. This valid no-rim setting isolates stable modifier sampling.
        modifier.use_rim = False
    else:
        raise AssertionError(name)
    return modifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    mesh_io = importlib.import_module(repo.name + ".modules.mesh.blender_re_mesh")
    sf6 = importlib.import_module(repo.name + ".modules.mesh.sf6_source")
    hybrid = importlib.import_module(repo.name + ".modules.mesh.sf6_hybrid")
    source_bytes = args.source_file.read_bytes()
    source = sf6.SourceMesh(source_bytes)
    assert source.blend_offset and source.shapes[0], "Fixture needs LOD0 shapes"
    assert mesh_io.importREMeshFile(str(args.source_file), IMPORT_OPTIONS.copy())
    collection = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
    source_parts = lod0_parts(source)
    objects = {}
    for obj in collection.all_objects:
        if obj.type == "MESH" and obj.get("SF6SourceMeta"):
            part = json.loads(obj["SF6SourceMeta"])
            if part["lod"] == 0:
                objects[part_key(source, part)] = obj
    assert objects.keys() == source_parts.keys()
    vp, fp = hybrid._normal_pointers(source)
    first = next(part for part in source.parts if part["lod"] == 0)
    supported = []
    for key, part in source_parts.items():
        if not list(source.part_shapes(part)):
            continue
        expected_v, expected_f = hybrid._synthesize_normals(source, part, first["start"])
        original_v = np.frombuffer(source.data, "<u4", part["count"], vp + (part["start"] - first["start"]) * 4)
        original_f = np.frombuffer(source.data, "<u4", hybrid._padded_indices(part), fp + (part["face_start"] - first["face_start"]) * 4)
        if np.array_equal(expected_v, original_v) and np.array_equal(expected_f, original_f):
            supported.append(key)
    assert supported, "Fixture needs a shaped part with proven normal encoding"
    chosen = min(supported, key=lambda key: source_parts[key]["count"])
    target, old_part = objects[chosen], source_parts[chosen]
    rotate = bool(collection["SF6SourceRotate"])
    options = dict(targetCollection=collection.name, rotate90=rotate, selectedOnly=False,
                   exportAllLODs=False, splitLoopVertices=False, useBlenderMaterialName=False,
                   preserveBoneMatrices=True, exportBoundingBoxes=False,
                   autoSolveRepeatedUVs=False, preserveSharpEdges=False,
                   limitTotal=True, limitTotalCount=6)
    results = []

    def passed(test, **details):
        result = dict(test=test, passed=True, **details)
        results.append(result)
        print("PASS " + json.dumps(result), flush=True)
        if args.report_json:
            args.report_json.parent.mkdir(parents=True, exist_ok=True)
            args.report_json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf8")

    def export_pair(root, name, selected=True, compare_ordinary=True, compare_shading=True):
        case = root / name
        case.mkdir(parents=True, exist_ok=True)
        ordinary_path = case / "ordinary.mesh.230110883"
        output_path = case / "hybrid.mesh.230110883"
        chosen_options = dict(options, selectedOnly=selected)
        before = scene_state(collection)
        if compare_ordinary:
            with zero_values(collection):
                assert mesh_io.exportREMeshFile(str(ordinary_path), dict(chosen_options, exportBlendShapes=False))
            assert scene_state(collection) == before, "Ordinary oracle changed editable data"
        hybrid_options = dict(chosen_options, exportBlendShapes=True, sf6HybridPreserve=True)
        assert mesh_io.exportREMeshFile(str(output_path), hybrid_options)
        assert scene_state(collection) == before, "Hybrid export changed editable scene state"
        result = sf6.SourceMesh(output_path.read_bytes())
        if compare_ordinary:
            ordinary = sf6.SourceMesh(ordinary_path.read_bytes())
            geometry_equal(ordinary, result, compare_shading=compare_shading)
        assert struct.unpack_from("<Q", result.data, 56)[0], "Missing normal table"
        return output_path, ordinary_path, result, hybrid_options["_sf6HybridReport"]

    with tempfile.TemporaryDirectory(prefix="sf6-hybrid-modifier-tests-") as temporary:
        root = args.output_dir or Path(temporary)
        root.mkdir(parents=True, exist_ok=True)
        # Unchanged paths must still have the same bytes as the proven core.
        select([target])
        for name, selected in (("unchanged_full", False), ("unchanged_subset", True)):
            path, ordinary_path, result, _ = export_pair(root, name, selected)
            expected, _ = hybrid.build_hybrid_mesh(source_bytes, ordinary_path.read_bytes(), collection,
                                                   selected_objects=(target,) if selected else None)
            assert path.read_bytes() == expected
            for key, part in lod0_parts(result).items():
                actual = list(result.part_shapes(part))
                original = list(source.part_shapes(source_parts[key]))
                assert [name for name, _, _ in actual] == [name for name, _, _ in original]
                assert all(np.array_equal(new, old) for (_, new, _), (_, old, _) in zip(actual, original))
            passed(name + "_bytes_and_deltas_unchanged", parts=len(result.parts))

        # Nonzero active/muted keys and an active-key preview must not influence
        # export Basis or be overwritten. Deliberately leave state unusual.
        keys = target.data.shape_keys.key_blocks
        keys[1].value, keys[1].mute = .35, True
        keys[1].slider_min, keys[1].slider_max = -.5, .75
        target.active_shape_key_index, target.show_only_shape_key = 1, True
        path, _, _, _ = export_pair(root, "active_keys")
        assert path.read_bytes() == (root / "unchanged_subset/hybrid.mesh.230110883").read_bytes()
        passed("active_muted_key_preview_exports_basis_and_restores_state")

        for name in ("subdivision_simple", "subdivision_catmull", "subdivision_smooth_stack", "simple_deform", "smooth",
                     "displace", "mirror", "array", "solidify"):
            modifier_or_stack = modifier_case(target, name)
            modifiers = modifier_or_stack if isinstance(modifier_or_stack, tuple) else (modifier_or_stack,)
            try:
                samples, signatures, triangles, polygons, corner_uvs, corner_normals = evaluate_oracle(target, mesh_io)
                basis = samples[keys[0].name]
                assert all(signature == signatures[keys[0].name] for signature in signatures.values())
                # Catmull-Clark UV interpolation can produce corner float32
                # rounding disagreement on a shared row. Its independent
                # geometry/key oracle remains exact, while the legacy raw
                # ordinary path rejects those UVs before snapshot processing.
                path, _, result, report = export_pair(root, name,
                    compare_ordinary=name != "subdivision_catmull",
                    compare_shading=name != "subdivision_smooth_stack")
                part = lod0_parts(result)[chosen]
                assert part["count"] >= len(basis)
                mapping = output_row_mapping(result, part, basis, triangles, rotate, sf6)
                validate_corner_uvs(result, part, corner_uvs)
                normal_quantization_error = (validate_evaluated_corner_normals(
                    result, part, corner_normals, rotate, sf6)
                    if name == "subdivision_smooth_stack" else None)
                actual = {key:delta for key,delta,_ in result.part_shapes(part)}
                assert actual.keys() == set(samples) - {keys[0].name}
                for shape, positions in samples.items():
                    if shape == keys[0].name:
                        continue
                    expected = sf6._from_blender((positions - basis)[mapping], rotate).astype("<f2").astype("<f4")
                    assert np.array_equal(actual[shape], expected), (name, shape, float(np.max(np.abs(actual[shape]-expected))))
                assert report["source_vertices"] == 0, "Evaluated provenance must be conservative"
                assert report["transferred_vertices"] > 0, name
                assert report["output_shape_links"] == len(actual)
                validate_target_bounds(result)
                passed("modifier_" + name + "_geometry_and_correctives", vertices=part["count"],
                       triangles=part["faces"], evaluated_polygons=polygons, shape_links=len(actual),
                       modifier_types=[modifier.type for modifier in modifiers],
                       max_evaluated_normal_quantization_error=normal_quantization_error,
                       corner_split_vertices=part["count"] - len(basis), moved_vertices=report["transferred_vertices"])
                if name == "subdivision_simple":
                    full_path, _, full, full_report = export_pair(root, "subdivision_full", False)
                    assert len(full.parts) == len(source_parts)
                    assert np.array_equal(full.positions(lod0_parts(full)[chosen]), result.positions(part))
                    assert len(full.shapes[0]) == len(source.shapes[0])
                    passed("full_scene_subdivision_retains_other_parts_and_shapes", parts=len(full.parts),
                           shape_links=full_report["output_shape_links"])
                    saved_reimport = path
            finally:
                for modifier in reversed(modifiers):
                    target.modifiers.remove(modifier)

        # A large authored key must expand target bounds after subdivision,
        # including movement on generated vertices.
        original_co = keys[1].data[0].co.copy()
        keys[1].data[0].co.x += 2
        modifier = modifier_case(target, "subdivision_simple")
        try:
            samples, _, triangles, _, _, _ = evaluate_oracle(target, mesh_io)
            basis = samples[keys[0].name]
            _, _, result, report = export_pair(root, "expanded_bounds")
            part = lod0_parts(result)[chosen]
            actual = {name:delta for name,delta,_ in result.part_shapes(part)}
            mapping = output_row_mapping(result, part, basis, triangles, rotate, sf6)
            wanted = sf6._from_blender((samples[keys[1].name] - basis)[mapping], rotate).astype("<f2").astype("<f4")
            assert np.array_equal(actual[keys[1].name], wanted)
            assert report["shape_target_bounds_expanded"] >= 1
            targets = validate_target_bounds(result)
            passed("subdivided_edited_key_expands_decoded_target_bounds", targets=targets,
                   expanded=report["shape_target_bounds_expanded"])
        finally:
            target.modifiers.remove(modifier)
            keys[1].data[0].co = original_co

        # Deliberately make a Weld modifier merge a pair in exactly one key.
        # The independent oracle must prove different output topology first.
        original_co = keys[1].data[0].co.copy()
        keys[1].data[0].co = keys[1].data[1].co
        modifier = target.modifiers.new("dynamic_weld", "WELD")
        modifier.merge_threshold = 1e-8
        try:
            samples, signatures, _, _, _, _ = evaluate_oracle(target, mesh_io)
            assert signatures[keys[0].name] != signatures[keys[1].name] or len(samples[keys[0].name]) != len(samples[keys[1].name]), "Dynamic test did not change topology"
            destination = root / "dynamic-topology.mesh.230110883"
            sentinel = b"existing destination must survive rejected hybrid export"
            destination.write_bytes(sentinel)
            before = scene_state(collection)
            try:
                mesh_io.exportREMeshFile(str(destination), dict(options, selectedOnly=True,
                    exportBlendShapes=True, sf6HybridPreserve=True))
            except ValueError as error:
                assert "topology" in str(error).lower() or "vertex" in str(error).lower(), str(error)
            else:
                raise AssertionError("Dynamic modifier topology should be rejected")
            assert destination.read_bytes() == sentinel
            assert scene_state(collection) == before
            assert not tuple(root.glob(".sf6-hybrid-*")), "Staging directory leaked"
            passed("dynamic_topology_rejected_destination_and_scene_preserved")
        finally:
            target.modifiers.remove(modifier)
            keys[1].data[0].co = original_co

        # Reimport a genuinely changed modifier export and prove strict source
        # preservation has all corrective names and encoded target bounds.
        expected_bytes = saved_reimport.read_bytes()
        expected = sf6.SourceMesh(expected_bytes)
        validate_target_bounds(expected)
        assert mesh_io.importREMeshFile(str(saved_reimport), IMPORT_OPTIONS.copy())
        imported = bpy.data.collections[bpy.context.scene["REMeshLastImportedCollection"]]
        imported_parts = [obj for obj in imported.all_objects if obj.type == "MESH" and obj.data.shape_keys]
        assert len(imported_parts) == 1
        assert set(imported_parts[0].data.shape_keys.key_blocks.keys()) - {"Basis"} == {
            name for name, _ in expected.shapes[0]}
        roundtrip = root / "modifier-roundtrip.mesh.230110883"
        assert mesh_io.exportREMeshFile(str(roundtrip), dict(targetCollection=imported.name,
                                                           exportBlendShapes=True, rotate90=rotate))
        assert roundtrip.read_bytes() == expected_bytes
        passed("modified_export_reimports_shape_names_and_strict_roundtrips_bytes")

    print("ALL_HYBRID_MODIFIER_TESTS_PASSED " + str(len(results)), flush=True)


if __name__ == "__main__":
    main()
