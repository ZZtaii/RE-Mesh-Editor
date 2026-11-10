"""Run with Blender --background --factory-startup --python-exit-code 1 --python FILE."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest

import addon_utils
import bpy
from mathutils import Matrix, Vector

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo.parent))
addon_utils.enable(repo.name, default_set=True)
bridge = importlib.import_module(f"{repo.name}.modules.fbxskel.blender_re_fbxskel")
codec = importlib.import_module(f"{repo.name}.modules.fbxskel.file_re_fbxskel")
operators = importlib.import_module(f"{repo.name}.modules.fbxskel.re_fbxskel_operators")


class FBXSkelRegressionTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        data = bpy.data.armatures.new('Fixture')
        self.obj = bpy.data.objects.new('Fixture', data)
        bpy.context.scene.collection.objects.link(self.obj)
        bpy.context.view_layer.objects.active = self.obj
        self.obj.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        root = data.edit_bones.new('root')
        root.head, root.tail = (1, 2, 3), (1, 3, 3)
        child = data.edit_bones.new('child')
        child.head, child.tail = (1, 4, 3), (1, 5, 3)
        child.parent = root
        bpy.ops.object.mode_set(mode='OBJECT')

    def test_root_transform_applied_once(self):
        transforms = [Matrix.Identity(4), Matrix.Translation((4, 5, 6)),
                      Matrix.Translation((4, 5, 6)) @ Matrix.Rotation(0.6, 4, 'Z')]
        with tempfile.TemporaryDirectory() as folder:
            for pose in (False, True):
                for world in transforms:
                    with self.subTest(pose=pose, world=world):
                        self.obj.matrix_world = world
                        self.obj.pose.bones['root'].location = (0.2, 0.3, 0.4)
                        bpy.context.view_layer.update()
                        expected = bridge.rotateNeg90Matrix @ world @ self.obj.data.bones['root'].matrix_local
                        if pose:
                            expected = expected @ self.obj.pose.bones['root'].matrix_basis
                        path = str(Path(folder) / 'test.fbxskel.7')
                        armatures_before = len(bpy.data.armatures)
                        self.assertTrue(bridge.exportFBXSkelFile(path, self.obj.name, pose))
                        self.assertEqual(len(bpy.data.armatures), armatures_before)
                        result = codec.readFBXSkel(path)
                        root = result.boneEntryList[0]
                        from mathutils import Quaternion
                        actual = Matrix.LocRotScale(Vector(root.translation),
                            Quaternion((root.rotation[3], *root.rotation[:3])), Vector(root.scale))
                        self.assertLess(max(abs(actual[r][c]-expected[r][c]) for r in range(4) for c in range(4)), 1e-5)
                        self.assertEqual(result.boneEntryList[1].parentIndex, 0)

    def test_link_no_armatures(self):
        self.assertEqual(operators.linkArmatures(self.obj, []), 0)


suite = unittest.defaultTestLoader.loadTestsFromTestCase(FBXSkelRegressionTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
