"""Synthetic SF6 range-allocation regressions; no game fixture or Blender needed."""

import hashlib
import importlib.util
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace
import unittest

import numpy as np


def load_modules():
    directory = Path(__file__).resolve().parents[1] / 'modules' / 'mesh'
    package_name = '_sf6_hybrid_range_tests'
    package = ModuleType(package_name)
    package.__path__ = [str(directory)]
    sys.modules[package_name] = package
    result = []
    for name in ('sf6_source', 'sf6_hybrid'):
        spec = importlib.util.spec_from_file_location(
            package_name + '.' + name, directory / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        result.append(module)
    return result


sf6, hybrid = load_modules()


def fixture(targets=None):
    """Two parts, with source target and zero intervals split within each part."""
    a = dict(lod=0, group=0, sub=0, material=0, start=0, count=4)
    b = dict(lod=0, group=0, sub=1, material=1, start=4, count=4)
    if targets is None:
        targets = [('Lift', [(0, [(.125, 0, 0, 3)]),
                             (2, [(.25, 0, 0, 5)])]),
                   ('Stretch', [(0, [(0, .5, 0, 7)]),
                                (2, [(0, 1, 0, 9)])])]
    # Each tuple above is a shape link in its own target, including a repeated
    # range list. This also exercises independent links and names.
    raw = bytearray(4096)
    blend, table, lod, descriptors = 256, 288, 320, 384
    bounds, target_map, shape_map = 640, 832, 864
    range_cursor, delta_cursor, delta_offset = 1024, 0, 2048
    struct.pack_into('<QQ', raw, blend, 1, table)
    struct.pack_into('<Q', raw, table, lod)
    struct.pack_into('<HH', raw, lod, len(targets), 2)
    struct.pack_into('<QQQQ', raw, lod + 16, descriptors, bounds, target_map, shape_map)
    shapes = []
    for ti, (name, ranges) in enumerate(targets):
        entries = []
        shapes.append((name, []))
        for start, values in ranges:
            values = np.asarray(values, dtype='<f2').reshape((-1, 4))
            entries.append((start, delta_cursor, len(values), 0))
            at = delta_offset + delta_cursor * 8
            raw[at:at + values.nbytes] = values.tobytes()
            shapes[-1][1].append((start, at, len(values)))
            delta_cursor += len(values)
        struct.pack_into('<HHHBBQ', raw, descriptors + ti * 16,
                         ti, 1, ti, len(entries), 1, range_cursor)
        for entry in entries:
            struct.pack_into('<IIII', raw, range_cursor, *entry)
            range_cursor += 16
        struct.pack_into('<8f', raw, bounds + ti * 32, -1, -1, -1, 13, 1, 1, 1, 17)
        struct.pack_into('<I', raw, target_map + ti * 4, 0xffffffff)
        struct.pack_into('<I', raw, shape_map + ti * 4, 0xffffffff)
    for ei, ranges in enumerate(([(1, 0, 1, 0), (4, 0, 2, 0)],
                                 [(3, 0, 1, 0), (6, 0, 2, 0)])):
        struct.pack_into('<HHHBBQ', raw, descriptors + (len(targets) + ei) * 16,
                         0, 0, 0, len(ranges), 0, range_cursor)
        for entry in ranges:
            struct.pack_into('<IIII', raw, range_cursor, *entry)
            range_cursor += 16
    source = SimpleNamespace(data=bytes(raw), blend_offset=blend, lod_count=1,
                             delta_offset=delta_offset, face_offset=len(raw),
                             shapes=[shapes], materials=['A', 'B'], parts=[a, b])
    source.part_shapes = lambda part: sf6.SourceMesh.part_shapes(source, part)
    return source, {(0, 'A'): a, (0, 'B'): b}


def build(source, old, counts=(6, 3), identity=False, identity_b=False):
    a, b = (0, 'A'), (0, 'B')
    new = {key: dict(part) for key, part in old.items()}
    new[a]['count'], new[b]['count'] = counts
    new[b]['start'] = counts[0]
    mapped = {key: ({i: i for i in range(part['count'])} if identity else {}, {})
              for key, part in new.items()}
    if not identity:
        mapped[a] = ({0: 0, 1: 2}, {})
        if identity_b:
            mapped[b] = ({i: i for i in range(counts[1])}, {})
    transferred = {a: {}, b: {}}
    for name, (delta, _) in hybrid._part_shapes(source, old[a]).items():
        full = np.zeros((counts[0], 3), np.float32)
        if identity:
            full[:] = delta
        else:
            full[0], full[1] = delta[0], delta[2]
            full[2:, 2] = np.arange(1, counts[0] - 1) * .0625
        transferred[a][name] = full
    edited = {a: {}, b: {}}
    out = bytearray(168)
    result = hybrid._build_shape_table(source, SimpleNamespace(blend_offset=0),
                                       old, new, mapped, transferred, edited, out)
    return out, result, transferred, mapped


def table(out):
    header = struct.unpack_from('<Q', out, 64)[0]
    pointer = struct.unpack_from('<Q', out, header + 8)[0]
    lod = struct.unpack_from('<Q', out, pointer)[0]
    targets, extras = struct.unpack_from('<HH', out, lod)
    descriptors, bounds, tm, sm = struct.unpack_from('<QQQQ', out, lod + 16)
    records = []
    for i in range(targets + extras):
        record = struct.unpack_from('<HHHBBQ', out, descriptors + i * 16)
        ranges = [struct.unpack_from('<IIII', out, record[-1] + j * 16)
                  for j in range(record[3])]
        records.append((record[:5], ranges))
    return targets, extras, records, bounds, tm, sm


class ShapeRangeTests(unittest.TestCase):
    def test_rebuilt_split_ranges_are_written_once(self):
        source, parts = fixture()
        out, result, transferred, _ = build(source, parts)
        targets, extras, records, _, tm, sm = table(out)
        self.assertEqual((targets, extras), (2, 1))
        self.assertEqual([r[1] for r in records],
                         [[(0, 0, 6, 0)], [(0, 6, 6, 0)], [(6, 0, 3, 0)]])
        values = np.frombuffer(result[0], '<f2').reshape((2, 6, 4))
        for i, name in enumerate(('Lift', 'Stretch')):
            np.testing.assert_array_equal(values[i, :, :3], transferred[(0, 'A')][name])
            np.testing.assert_array_equal(values[i, :2, 3], (3, 5) if i == 0 else (7, 9))
        self.assertEqual(result[4:7], (4, 8, 0))
        self.assertEqual(out[tm:tm + 8], source.data[832:840])
        self.assertEqual(out[sm:sm + 8], source.data[864:872])

    def test_identity_keeps_original_ranges_and_zero_descriptors(self):
        source, parts = fixture()
        out, result, _, _ = build(source, parts, (4, 4), identity=True)
        targets, extras, records, _, _, _ = table(out)
        self.assertEqual((targets, extras), (2, 2))
        self.assertEqual([r[1] for r in records],
                         [[(0, 0, 1, 0), (2, 1, 1, 0)],
                          [(0, 2, 1, 0), (2, 3, 1, 0)],
                          [(1, 0, 1, 0), (4, 0, 2, 0)],
                          [(3, 0, 1, 0), (6, 0, 2, 0)]])
        self.assertEqual(result[0], source.data[2048:2080])
        self.assertEqual(result[4:7], (4, 0, 0))
        # Golden hash was independently compared with the accepted 0.68
        # writer; source identity must keep its entire allocation byte-for-byte.
        self.assertEqual(hashlib.sha256(out).hexdigest(),
                         'b2b0cf947176a8066a57c622ad6190065b6c7466602b6157a1b03fb0ece8a89a')

    def test_empty_zero_descriptors_are_removed(self):
        source, parts = fixture()
        # Both source zero descriptors first address A and then B. The first
        # rebuilt B range covers it completely, making the second one empty.
        out, result, _, _ = build(source, parts, (6, 4))
        self.assertEqual(table(out)[1], 1)
        self.assertEqual(result[3], 1)

    def test_identity_zero_part_keeps_both_intervals(self):
        source, parts = fixture()
        out, _, _, _ = build(source, parts, (6, 4), identity_b=True)
        self.assertEqual([r[1] for r in table(out)[2][2:]],
                         [[(6, 0, 2, 0)], [(8, 0, 2, 0)]])

    def test_multiple_shapes_share_the_consolidated_range_stride(self):
        source, parts = fixture()
        raw = bytearray(source.data)
        # Pack both existing links into one two-shape target. GPU entries were
        # already packed shape-major; each original shape has two source rows.
        struct.pack_into('<HH', raw, 320, 1, 2)
        struct.pack_into('<HHHBBQ', raw, 384, 0, 2, 0, 2, 1, 1024)
        raw[400:432] = source.data[416:448]
        source.data = bytes(raw)
        out, result, transferred, _ = build(source, parts)
        targets, extras, records, _, _, _ = table(out)
        self.assertEqual((targets, extras), (1, 1))
        self.assertEqual(records[0], ((0, 2, 0, 1, 1), [(0, 0, 6, 0)]))
        values = np.frombuffer(result[0], '<f2').reshape((2, 6, 4))
        for i, name in enumerate(('Lift', 'Stretch')):
            np.testing.assert_array_equal(values[i, :, :3], transferred[(0, 'A')][name])
        self.assertEqual(result[4:7], (4, 8, 0))

    def test_repeated_names_with_different_part_deltas_fail(self):
        source, parts = fixture([('Lift', [(0, [(.125, 0, 0, 0)])]),
                                 ('Lift', [(2, [(.25, 0, 0, 0)])])])
        with self.assertRaisesRegex(hybrid.HybridExportError, 'ambiguous repeated shape name'):
            build(source, parts)

    def test_source_range_crossing_parts_is_sliced_without_losing_rows(self):
        source, parts = fixture([('Lift', [(3, [(.125, 0, 0, 3),
                                                (.25, 0, 0, 5)])])])
        raw = bytearray(source.data)
        struct.pack_into('<H', raw, 322, 1)
        struct.pack_into('<HHHBBQ', raw, 400, 0, 0, 0, 2, 0, 1040)
        struct.pack_into('<IIIIIIII', raw, 1040, 0, 0, 3, 0, 5, 0, 3, 0)
        source.data = bytes(raw)
        out, result, _, _ = build(source, parts, (4, 4), identity=True)
        self.assertEqual(table(out)[2][0][1], [(3, 0, 1, 0), (4, 1, 1, 0)])
        self.assertEqual(result[0], source.data[2048:2064])
        rebuilt_out, rebuilt_result, _, _ = build(source, parts)
        self.assertEqual(table(rebuilt_out)[:2], (1, 0))
        self.assertEqual(table(rebuilt_out)[2][0][1], [(0, 0, 6, 0), (6, 6, 3, 0)])
        self.assertEqual(len(rebuilt_result[0]), 9 * 8)

    def test_source_range_with_missing_or_overlapping_parts_fails(self):
        _, parts = fixture()
        parts[(0, 'B')]['start'] = 5
        with self.assertRaisesRegex(hybrid.HybridExportError, 'missing or overlapping'):
            list(hybrid._shape_range_parts(parts, [(3, 0, 3, 0)]))

    def test_repeated_names_keep_linkage_when_deltas_match(self):
        ranges = [(0, [(.125, 0, 0, 0)]), (2, [(.25, 0, 0, 0)])]
        source, parts = fixture([('Lift', ranges), ('Lift', ranges)])
        out, result, _, _ = build(source, parts)
        self.assertEqual([r[0][:3] for r in table(out)[2][:2]], [(0, 1, 0), (1, 1, 1)])
        values = np.frombuffer(result[0], '<f2').reshape((2, 6, 4))
        np.testing.assert_array_equal(values[0], values[1])


if __name__ == '__main__':
    unittest.main()
