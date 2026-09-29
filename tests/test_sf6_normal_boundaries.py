"""Fixture-free regressions for SF6 normal references and row boundary flags."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

import numpy as np


directory = Path(__file__).resolve().parents[1] / 'modules' / 'mesh'
package_name = '_sf6_normal_boundary_tests'
package = ModuleType(package_name)
package.__path__ = [str(directory)]
sys.modules[package_name] = package
spec = importlib.util.spec_from_file_location(
    package_name + '.sf6_hybrid', directory / 'sf6_hybrid.py')
hybrid = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hybrid
spec.loader.exec_module(hybrid)


def geometry(start=0, separate_normal=False):
    # Rows 0 and 5 coincide and normally share a normal reference. Row 0
    # belongs to an open triangle; row 5 is the interior of a four-face fan.
    # Their welded reference is shared, but only row 0 witnesses an open edge.
    positions = np.array(((0, 0, 0), (-1, -1, 0), (1, -1, 0),
                          (1, 1, 0), (-1, 1, 0), (0, 0, 0),
                          (2, 0, 0), (0, 2, 0)), dtype='<f4')
    triangles = np.array(((5, 1, 2), (5, 2, 3), (5, 3, 4), (5, 4, 1),
                          (0, 6, 7)), dtype=np.int32)
    normals = np.tile(np.array((0, 0, 127, 0, 127, 0, 0, 0), dtype=np.int8), (8, 1))
    if separate_normal:
        normals[5, :3] = (0, 127, 0)
    # A tangent difference does not split a normal reference.
    normals[5, 4:7] = (0, 127, 0)
    position_data = bytes(start * 12) + positions.tobytes()
    normal_data = bytes(start * 8) + normals.tobytes()
    mesh = SimpleNamespace(data=position_data + normal_data,
                           elements={0: (12, 0), 1: (8, len(position_data))},
                           faces=lambda part: triangles)
    part = dict(group=0, sub=0, start=start, count=8, faces=len(triangles))
    return mesh, part, triangles


class NormalBoundaryTests(unittest.TestCase):
    def test_boundary_flag_is_witnessed_by_the_actual_row(self):
        mesh, part, _ = geometry()
        vertex, faces = hybrid._synthesize_normals(mesh, part, 0)
        self.assertEqual(int(vertex[0]), 0x80000000)
        self.assertEqual(int(vertex[5]), 0)
        np.testing.assert_array_equal(vertex & 0x7fffffff, (0, 1, 2, 3, 4, 0, 6, 7))
        np.testing.assert_array_equal(vertex >> 31, (1, 1, 1, 1, 1, 0, 1, 1))
        hybrid._validate_palette(faces, part)
        self.assertEqual(len(faces), 16)
        self.assertEqual(int(faces[-1]), 0)

    def test_reference_offset_and_face_palette_use_the_same_welded_rows(self):
        mesh, part, triangles = geometry(start=4)
        vertex, faces = hybrid._synthesize_normals(mesh, part, 1)
        self.assertEqual(int(vertex[0]), 0x80000003)
        self.assertEqual(int(vertex[5]), 3)
        canonical = np.array((0, 1, 2, 3, 4, 0, 6, 7), np.uint32)
        expected = canonical[triangles].reshape(-1) + 3
        np.testing.assert_array_equal(faces[:15] & 0x3fffff, expected)
        np.testing.assert_array_equal(faces[:15] >> 22,
                                      np.searchsorted(np.unique(expected), expected))
        hybrid._validate_palette(faces, part)

    def test_coincident_rows_with_different_normals_keep_separate_references(self):
        mesh, part, _ = geometry(separate_normal=True)
        vertex, faces = hybrid._synthesize_normals(mesh, part, 0)
        self.assertEqual(int(vertex[0]), 0x80000000)
        self.assertEqual(int(vertex[5]), 5)
        hybrid._validate_palette(faces, part)


if __name__ == '__main__':
    unittest.main()
