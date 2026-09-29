"""Run in isolated Blender --background --factory-startup --python-exit-code 1.

Uses real Blender shader nodes; no extracted game assets are required. SF6's
StitchMap blue channel must remain a stitch detail mask, while genuine opacity
from another texture and the alpha handling of other games remain intact.
"""
import argparse
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-json', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    nodes = importlib.import_module(repo.name + '.modules.mdf.blender_nodes_re_mdf')
    report = []

    def passed(name):
        report.append(dict(test=name, passed=True))
        print('PASS ' + name, flush=True)

    def property_value(name, values):
        return SimpleNamespace(propName=name, paramCount=len(values), propValue=values)

    def setup(game, texture='StitchMap', properties=None):
        material = bpy.data.materials.new('AlphaRegression_' + game)
        material.use_nodes = True
        tree = material.node_tree
        tree.nodes.clear()
        image = tree.nodes.new('ShaderNodeTexImage')
        image.name = texture
        image.image = bpy.data.images.new(material.name, width=2, height=2, alpha=True)
        # Deliberately different blue/alpha values reproduce the packed-channel
        # distinction that previously made otherwise opaque SF6 clothing vanish.
        image.image.generated_color = (0.5, 0.5, 0.0, 0.75)
        info = dict(gameName=game, blenderMaterial=material, alphaSocket=None,
                    isAlphaBlend=False, mPropDict=properties or {}, currentPropPos=[-500, 0],
                    normalNodeLayerGroup=nodes.dynamicColorMixLayerNodeGroup(tree),
                    albedoNodeLayerGroup=nodes.dynamicColorMixLayerNodeGroup(tree))
        return tree, image, info

    def source_socket(socket):
        assert socket.is_linked, socket.name
        return socket.links[0].from_socket

    def unpacked_normal(tree, image):
        separates = [node for node in tree.nodes if node.bl_idname == 'ShaderNodeSeparateColor']
        combines = [node for node in tree.nodes if node.bl_idname == 'ShaderNodeCombineColor']
        assert len(separates) == len(combines) == 1
        separate, combine = separates[0], combines[0]
        assert source_socket(separate.inputs['Color']) == image.outputs['Color']
        assert source_socket(combine.inputs['Red']) == separate.outputs['Red']
        assert source_socket(combine.inputs['Green']) == separate.outputs['Green']
        assert not combine.inputs['Blue'].is_linked
        assert combine.inputs['Blue'].default_value == 1.0
        return separate, combine

    tree, image, info = setup('SF6')
    assert nodes.newNAMNode(tree, 'StitchMap', info) == image
    assert info['alphaSocket'] is None
    assert info['isAlphaBlend'] is False
    _, combine = unpacked_normal(tree, image)
    assert info['normalNodeLayerGroup'].currentOutSocket == combine.outputs['Color']
    passed('sf6_stitch_map_does_not_enable_opacity_or_blending')

    # A material can already have genuine alpha before its StitchMap is handled.
    for blending in (False, True):
        tree, image, info = setup('SF6')
        alpha = tree.nodes.new('ShaderNodeValue').outputs['Value']
        alpha.default_value = 0.5
        info.update(alphaSocket=alpha, isAlphaBlend=blending)
        nodes.newNAMNode(tree, 'StitchMap', info)
        assert info['alphaSocket'] == alpha
        assert info['isAlphaBlend'] is blending
    passed('sf6_stitch_map_preserves_existing_alpha_and_blend_state')

    tree, image, info = setup('SF6')
    albedo = tree.nodes.new('ShaderNodeTexImage')
    albedo.name = 'BaseAlphaMap'
    nodes.newALBANode(tree, 'BaseAlphaMap', info)
    nodes.newNAMNode(tree, 'StitchMap', info)
    assert info['alphaSocket'] == albedo.outputs['Alpha']
    assert info['albedoNodeLayerGroup'].currentOutSocket == albedo.outputs['Color']
    passed('sf6_real_base_alpha_texture_survives_stitch_processing')

    for game in ('RE4', 'MHWILDS'):
        tree, image, info = setup(game)
        nodes.newNAMNode(tree, 'StitchMap', info)
        separate, combine = unpacked_normal(tree, image)
        assert info['alphaSocket'] == separate.outputs['Blue']
        assert info['isAlphaBlend'] is True
        assert info['normalNodeLayerGroup'].currentOutSocket == combine.outputs['Color']
    passed('other_games_keep_stitch_blue_opacity_and_blending')

    for game in ('SF6', 'RE4'):
        tree, image, info = setup(game, texture='NormalAlphaMap')
        nodes.newNAMNode(tree, 'NormalAlphaMap', info)
        assert info['alphaSocket'] == image.outputs['Alpha']
        assert info['isAlphaBlend'] is False
        unpacked_normal(tree, image)
    passed('normal_alpha_maps_still_use_image_alpha')

    properties = {
        'Stitch_Color': property_value('Stitch_Color', (0.2, 0.3, 0.4, 1.0)),
        'Stitch_Scale': property_value('Stitch_Scale', (2.0,)),
        'Stitch_U_offset': property_value('Stitch_U_offset', (0.1,)),
        'Stitch_V_offset': property_value('Stitch_V_offset', (0.2,)),
        'Stitch_Brightness': property_value('Stitch_Brightness', (0.0,)),
        'Stitch_Contrast': property_value('Stitch_Contrast', (0.0,)),
        'Stitch_Normal_Rate': property_value('Stitch_Normal_Rate', (0.75,)),
    }
    tree, image, info = setup('SF6', properties=properties)
    nodes.newNAMNode(tree, 'StitchMap', info)
    separate, combine = unpacked_normal(tree, image)
    assert all(name in tree.nodes for name in properties)
    brightness = next(node for node in tree.nodes if node.bl_idname == 'ShaderNodeBrightContrast')
    assert source_socket(brightness.inputs['Color']) == separate.outputs['Blue']
    albedo_mix = info['albedoNodeLayerGroup'].currentOutSocket.node
    assert albedo_mix.blend_type == 'MULTIPLY'
    assert source_socket(albedo_mix.inputs['Color2']) == brightness.outputs['Color']
    assert source_socket(albedo_mix.inputs['Color1']) == tree.nodes['Stitch_Color'].outputs['Color']
    normal_mix = info['normalNodeLayerGroup'].currentOutSocket.node
    assert normal_mix.blend_type == 'MULTIPLY'
    assert source_socket(normal_mix.inputs['Color1']) == combine.outputs['Color']
    assert source_socket(normal_mix.inputs['Color2']) == tree.nodes['Stitch_Normal_Rate'].outputs['Value']
    mapping = source_socket(image.inputs['Vector']).node
    assert mapping.bl_idname == 'ShaderNodeMapping'
    assert source_socket(mapping.inputs['Scale']) == tree.nodes['Stitch_Scale'].outputs['Value']
    offsets = source_socket(mapping.inputs['Location']).node
    assert source_socket(offsets.inputs['X']) == tree.nodes['Stitch_U_offset'].outputs['Value']
    assert source_socket(offsets.inputs['Y']) == tree.nodes['Stitch_V_offset'].outputs['Value']
    assert info['alphaSocket'] is None
    assert info['isAlphaBlend'] is False
    passed('sf6_stitch_color_blue_detail_uv_and_normal_controls_remain_connected')

    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('MATERIAL_ALPHA_TESTS_PASSED ' + str(len(report)), flush=True)


if __name__ == '__main__':
    main()
