"""Blender integration regression for TEX batch-conversion destinations."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest

import addon_utils

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo.parent))
addon_utils.enable(repo.name, default_set=True)
bridge = importlib.import_module(f'{repo.name}.modules.tex.blender_re_tex')
codec = importlib.import_module(f'{repo.name}.modules.tex.file_re_tex')


class TexDestinationTests(unittest.TestCase):
    def test_conversion_respects_destination(self):
        for count in (1, 2):
            with self.subTest(images=count), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / 'source.tex.assets'
                source.mkdir()
                output = Path(folder) / 'output'
                name = 'fixture.tex.28'
                texture = codec.RE_TexFile()
                header = texture.tex.header
                header.version = 28
                header.width = header.height = 4
                header.depth = header.mipCount = 1
                header.imageMipHeaderSize = 16
                header.imageCount = count
                header.format = 28  # R8G8B8A8_UNORM
                for index in range(count):
                    mip = codec.MipData()
                    mip.mipOffset = 40 + count * 16 + index * 64
                    mip.scanlineLength = 16
                    mip.uncompressedSize = 64
                    mip.textureData = bytes([20 + index, 30, 40, 255]) * 16
                    texture.tex.imageMipDataList.append([mip])
                texture.write(str(source / name))
                sentinel = source / 'fixture.dds'
                sentinel.write_bytes(b'preserve original DDS')
                self.assertEqual(bridge.convertTexDDSList([name], str(source), str(output), 'SF6'), (1, 0))
                expected = ['fixture.dds'] if count == 1 else [f'fixture #ARRAY_{i:02}.dds' for i in range(count)]
                for filename in expected:
                    self.assertTrue((output / filename).is_file(), filename)
                    self.assertEqual((output / filename).read_bytes()[:4], b'DDS ')
                self.assertEqual(sentinel.read_bytes(), b'preserve original DDS')
                self.assertEqual(set(p.name for p in source.iterdir()), {name, sentinel.name})


result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TexDestinationTests))
if not result.wasSuccessful():
    raise SystemExit(1)
