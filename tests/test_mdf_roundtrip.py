"""Read/write/re-read user-supplied MDF fixtures without distributing assets.

Blender --background --factory-startup --python-exit-code 1 --python FILE -- MDF...
"""
import importlib
from pathlib import Path
import sys
import tempfile

import addon_utils

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo.parent))
addon_utils.enable(repo.name, default_set=True)
codec = importlib.import_module(f'{repo.name}.modules.mdf.file_re_mdf')


def snapshot(mdf):
    return [dict(
        name=mat.materialName, hash=mat.matNameHash, mmtr=mat.mmtrPath,
        shader=mat.shaderType, flags=mat.flags.asInt32, flagsB=mat.flagsB.asInt32,
        lod=mat.shaderLODNum, bakeArray=mat.bakeTextureArraySize,
        textures=[(tex.textureType, tex.texturePath) for tex in mat.textureList],
        properties=[(p.propName, p.paramCount, p.unknFlag, p.propValue,
                     p.padding, p.frontPadding) for p in mat.propertyList],
        mmtrs=None if mat.mmtrsData is None else mat.mmtrsData.indexDataList,
        gpbfNames=[p.name for p in mat.gpbfBufferNameList],
        gpbfPaths=[p.name for p in mat.gpbfBufferPathList],
    ) for mat in mdf.materialList]


paths = sys.argv[sys.argv.index('--') + 1:]
assert paths, 'Supply at least one MDF fixture'
with tempfile.TemporaryDirectory() as folder:
    for index, path in enumerate(paths):
        original = codec.readMDF(path)
        before = snapshot(original)
        output = str(Path(folder) / f'{index}.mdf2{Path(path).suffix}')
        codec.writeMDF(original, output)
        after = snapshot(codec.readMDF(output))
        assert before == after, f'MDF semantic round trip failed: {path}'
        print(f'PASS: {path}: {len(before)} materials; properties, textures, flags, MMTRS, GPBF preserved')
