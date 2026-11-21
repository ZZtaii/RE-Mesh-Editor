"""SF6 material preview: opacity follows the alpha test flag, and MDF panel edits reach the preview.

Run inside Blender with the addon installed or on the user scripts path:
blender -b --factory-startup --python tests/test_sf6_material_preview.py -- --source-dir <folder that holds esf\\esfNNN\\...>

Needs the retail files esf030_004_01 (sheer cloth), esf006_003_00 (glasses) and esf006_004_01 (lights).
"""
import argparse
import os
import sys

import addon_utils
import bpy

ADDON = "RE-Mesh-Editor-main"


def bsdf_of(material):
    return next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")


def mdf_object(material_name):
    for obj in bpy.data.objects:
        if obj.get("~TYPE") == "RE_MDF_MATERIAL" and obj.re_mdf_material.materialName == material_name:
            return obj
    raise AssertionError("No MDF material object for " + material_name)


def mdf_prop(obj, name):
    for prop in obj.re_mdf_material.propertyList_items:
        if prop.prop_name == name:
            return prop
    raise AssertionError(name + " missing on " + obj.name)


def import_mesh(source_dir, character, costume, part):
    folder = os.path.join(source_dir, character, costume, part)
    name = "%s_%s_%s.mesh.230110883" % (character, costume, part)
    result = bpy.ops.re_mesh.importfile(directory=folder + os.sep, files=[{"name": name}], loadMaterials=True, loadMDFData=True, clearScene=True)
    assert "FINISHED" in result, result


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--texture-cache", default="")
    args = parser.parse_args(argv)
    addon_utils.enable(ADDON, default_set=True)
    if args.texture_cache:
        bpy.context.preferences.addons[ADDON].preferences.textureCachePath = args.texture_cache

    # The list of properties the panel can update must still hold the general ones
    groups = sys.modules[ADDON + ".modules.mdf.re_mdf_propertyGroups"]
    for name in ("BaseColor", "Roughness", "CustomizeColor_0", "CustomizeRoughness_0", "AlphaValue", "Glass_Alpha"):
        assert name in groups.editablePropsSet, name + " is not editable in the preview"

    # Sheer cloth: the alpha test flag is on, the preview is see-through and follows the flag
    import_mesh(args.source_dir, "esf030", "004", "01")
    sheer = bpy.data.materials["esf_ClothA_Stocking"]
    assert bsdf_of(sheer).inputs["Alpha"].is_linked, "sheer cloth has no opacity"
    flag_node = sheer.node_tree.nodes["MDF_BaseAlphaTestEnable"]
    assert flag_node.outputs[0].default_value == 1.0
    sheer_obj = mdf_object("esf_ClothA_Stocking")
    sheer_obj.re_mdf_material.flags.BaseAlphaTestEnable = False
    assert flag_node.outputs[0].default_value == 0.0, "flag change did not reach the preview"
    sheer_obj.re_mdf_material.flags.BaseAlphaTestEnable = True
    assert flag_node.outputs[0].default_value == 1.0
    edge = mdf_prop(sheer_obj, "SeeThrough_EdgeAlpha")
    edge.float_value = 0.25
    assert abs(sheer.node_tree.nodes["SeeThrough_EdgeAlpha"].outputs[0].default_value - 0.25) < 1e-6

    # AlphaValue fades the whole material and is editable
    fade = mdf_prop(sheer_obj, "AlphaValue")
    fade.float_value = 0.5
    assert abs(sheer.node_tree.nodes["AlphaValue"].outputs[0].default_value - 0.5) < 1e-6
    fade.float_value = 1.0

    # Colour slots: the nodes hold what the panel shows (white and 1 stand for "no change")
    belt_obj = mdf_object("esf_ClothA_Belt")
    belt_nodes = bpy.data.materials["esf_ClothA_Belt"].node_tree.nodes
    assert abs(belt_nodes["CustomizeColor_0"].inputs["Color"].default_value[0] - mdf_prop(belt_obj, "CustomizeColor_0").color_value[0]) < 1e-4
    rough = mdf_prop(belt_obj, "CustomizeRoughness_0")
    assert abs(belt_nodes["CustomizeRoughness_0"].outputs[0].default_value - rough.float_value) < 1e-6
    before = rough.float_value
    rough.float_value = 0.3
    assert abs(belt_nodes["CustomizeRoughness_0"].outputs[0].default_value - 0.3) < 1e-6
    rough.float_value = before

    # Stitched cloth without the flag stays solid although its texture holds alpha
    belt = bpy.data.materials["esf_ClothA_Belt"]
    assert belt.node_tree.nodes["MDF_BaseAlphaTestEnable"].outputs[0].default_value == 0.0
    assert belt.surface_render_method == "DITHERED"

    # Glasses: blended, and the glass values are editable
    import_mesh(args.source_dir, "esf006", "003", "00")
    lens = bpy.data.materials["esf_Cloth_lens"]
    assert lens.surface_render_method == "BLENDED", lens.surface_render_method
    assert bsdf_of(lens).inputs["Alpha"].is_linked
    lens_obj = mdf_object("esf_Cloth_lens")
    glass = mdf_prop(lens_obj, "Glass_Alpha")
    glass.float_value = 0.9
    assert abs(lens.node_tree.nodes["Glass_Alpha"].outputs[0].default_value - 0.9) < 1e-6
    # Hair: dithered opacity with its own values
    hair = bpy.data.materials["esf_Hair00"]
    assert bsdf_of(hair).inputs["Alpha"].is_linked
    assert "DissolveRange" in hair.node_tree.nodes and "EdgeAlphaMask_Rate" in hair.node_tree.nodes
    hair_range = mdf_prop(mdf_object("esf_Hair00"), "DissolveRange")
    hair_range.float_value = 0.6
    assert abs(hair.node_tree.nodes["DissolveRange"].outputs[0].default_value - 0.6) < 1e-6
    # Eye shadow keeps the general blended setup
    assert bpy.data.materials["esf_EyeShadow00"].surface_render_method == "BLENDED"

    # Lights: the glow is connected and its intensity is editable
    import_mesh(args.source_dir, "esf006", "004", "01")
    light = bpy.data.materials["esf_light"]
    assert bsdf_of(light).inputs["Emission Strength"].is_linked, "no glow"
    light_obj = mdf_object("esf_light")
    intensity = mdf_prop(light_obj, "Emissive_Intensity")
    intensity.float_value = 3.0
    assert abs(light.node_tree.nodes["Emissive_Intensity"].outputs[0].default_value - 3.0) < 1e-6
    # A material of the same costume without a glow mask stays dark
    assert not bsdf_of(bpy.data.materials["esf_armor"]).inputs["Emission Strength"].is_linked

    print("SF6_MATERIAL_PREVIEW_OK")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
