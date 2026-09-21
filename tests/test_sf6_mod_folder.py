import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'modules/mesh/sf6_mod_folder.py'
SPEC = importlib.util.spec_from_file_location('sf6_mod_folder_tested', MODULE)
package = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = package
SPEC.loader.exec_module(package)


class ModFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.asset = package.parse_asset_name('esf033_002_01.mesh.230110883')
        self.values = dict(name='Variant A', version='v1', author='Tester',
                           category=self.asset.category, description='Example')

    def export(self, folder='Variant A', callback=None, preview=''):
        def write_mesh(path):
            Path(path).write_bytes(b'MESH test output')
            return True
        return package.export_mod_folder(self.root, folder, self.asset, self.values,
                                         preview, callback or write_mesh)

    def test_routes_character_costume_and_slot_without_repadding(self):
        self.assertEqual(self.asset.relative_path.as_posix(),
                         'natives/stm/product/model/esf/esf033/002/01/esf033_002_01.mesh.230110883')
        self.assertEqual(self.asset.category, '!Characters > Yasmine')
        for slot, label in (('01', 'Head'), ('02', 'Hair'), ('00', 'Mesh 00')):
            asset = package.parse_asset_name(f'esf032_001_{slot}.mesh.230110883')
            self.assertEqual(asset.character_name, 'Ingrid')
            self.assertEqual(asset.costume, '001')
            self.assertEqual(asset.slot_name, label)
            self.assertEqual(asset.relative_path.parent.name, slot)
        self.assertEqual(len(package.CHARACTERS), 31)
        self.assertEqual(package.parse_asset_name('esf001_003_02.mesh.230110883').character_name, 'Ryu')

    def test_renamed_and_legacy_collections(self):
        class Collection(dict):
            name = 'Renamed collection'
        collection = Collection(SF6SourceFilename=self.asset.filename)
        self.assertEqual(package.asset_from_collection(collection), self.asset)
        del collection['SF6SourceFilename']
        collection.name = 'esf033_002_01.mesh.004'
        self.assertEqual(package.asset_from_collection(collection), self.asset)
        collection.name = 'Renamed legacy collection'
        collection['~ASSETPATH'] = 'product/model/esf/esf033/002/01/esf033_002_01.mesh'
        self.assertEqual(package.asset_from_collection(collection), self.asset)

    def test_rejects_unrecognized_asset_names(self):
        for name in ('mesh.mesh.230110883', 'esf33_2_1.mesh.230110883',
                     'esf033_002_01.mesh.1808312334', 'esf033_002_01.mesh.230110883.bak'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                package.parse_asset_name(name)

    def test_rejects_invalid_folder_names(self):
        for name in ('', '..', '../elsewhere', 'nested/folder', 'C:\\elsewhere',
                     'CON', 'aux.txt', 'com1', 'bad.', 'bad ', 'a\nb'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.export(folder=name)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_ini_preserves_unknown_settings_and_comments(self):
        previous = '; retained comment\nName=old\nname=duplicate\nAddonFor=base\nscreenshot=old.png\n'
        text = package.render_modinfo(self.values, previous)
        self.assertIn('; retained comment\n', text)
        self.assertIn('AddonFor=base\n', text)
        self.assertEqual(text.count('name='), 1)
        self.assertNotIn('screenshot=', text)
        self.assertIn('category=!Characters > Yasmine\n', text)

    def test_ini_rejects_newline_injection(self):
        for key in ('name', 'author', 'description'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                package.render_modinfo(dict(self.values, **{key: 'a\nscreenshot=elsewhere'}))

    def test_creates_mod_folder_and_copies_preview(self):
        preview = self.root / 'chosen image.png'
        preview.write_bytes(b'preview image bytes')
        result = self.export(preview=str(preview))
        self.assertEqual(result, self.root / 'Variant A' / self.asset.relative_path)
        self.assertEqual(result.read_bytes(), b'MESH test output')
        info = package.read_modinfo(self.root / 'Variant A/modinfo.ini')
        self.assertEqual(info['name'], 'Variant A')
        self.assertEqual(info['screenshot'], 'screenshot.png')
        self.assertEqual((self.root / 'Variant A/screenshot.png').read_bytes(), preview.read_bytes())

    def test_existing_folder_keeps_other_mod_files(self):
        result = self.export()
        untouched = result.parent / 'texture.tex.28'
        untouched.write_bytes(b'keep texture')
        info_path = self.root / 'Variant A/modinfo.ini'
        info_path.write_text(info_path.read_text() + 'AddonFor=base\n')
        self.values['version'] = 'v2'
        self.export()
        self.assertEqual(untouched.read_bytes(), b'keep texture')
        info = package.read_modinfo(info_path)
        self.assertEqual(info['version'], 'v2')
        self.assertEqual(info['addonfor'], 'base')

    def test_export_failure_does_not_touch_existing_mod(self):
        result = self.export()
        old_mesh = result.read_bytes()
        info_path = self.root / 'Variant A/modinfo.ini'
        old_info = info_path.read_bytes()
        def fail(path):
            Path(path).write_bytes(b'partial export')
            raise ValueError('invalid edit')
        with self.assertRaises(ValueError):
            self.export(callback=fail)
        self.assertEqual(result.read_bytes(), old_mesh)
        self.assertEqual(info_path.read_bytes(), old_info)
        self.assertFalse(list(self.root.glob('.re-mesh-export-*')))

    def test_false_export_does_not_create_mod(self):
        with self.assertRaises(ValueError):
            self.export(callback=lambda path: False)
        self.assertFalse((self.root / 'Variant A').exists())

    def test_failed_file_install_rolls_back_mesh_and_ini(self):
        preview = self.root / 'preview.png'
        preview.write_bytes(b'old preview')
        result = self.export(preview=str(preview))
        info_path = self.root / 'Variant A/modinfo.ini'
        previous_info = info_path.read_bytes()
        previous_mesh = result.read_bytes()
        preview.write_bytes(b'new preview')
        self.values['version'] = 'v2'
        real_replace = package.os.replace
        failed = False
        def fail_once(source, target):
            nonlocal failed
            if Path(target).name == 'screenshot.png' and not failed:
                failed = True
                raise OSError('simulated locked preview')
            return real_replace(source, target)
        with patch.object(package.os, 'replace', side_effect=fail_once), self.assertRaises(OSError):
            self.export(preview=str(preview))
        self.assertEqual(result.read_bytes(), previous_mesh)
        self.assertEqual(info_path.read_bytes(), previous_info)
        self.assertEqual((self.root / 'Variant A/screenshot.png').read_bytes(), b'old preview')

    def test_rejects_invalid_preview_before_export(self):
        with self.assertRaises(ValueError):
            self.export(preview=str(self.root / 'missing.png'))
        self.assertFalse((self.root / 'Variant A').exists())

    def test_locked_rollback_keeps_original_files_for_recovery(self):
        result = self.export()
        previous_mesh = result.read_bytes()
        info_path = self.root / 'Variant A/modinfo.ini'
        previous_info = info_path.read_bytes()
        real_replace, real_copy = package.os.replace, package.shutil.copy2
        def fail_install(source, target):
            if Path(target) == info_path:
                raise OSError('locked modinfo')
            return real_replace(source, target)
        def fail_restore(source, target):
            if 'backup' in Path(source).parts:
                raise OSError('locked during rollback')
            return real_copy(source, target)
        with patch.object(package.os, 'replace', side_effect=fail_install), \
                patch.object(package.shutil, 'copy2', side_effect=fail_restore), \
                self.assertRaises(package.RecoveryRequiredError) as raised:
            self.export()
        recovery = list(self.root.glob('.re-mesh-export-*/backup'))
        self.assertEqual(len(recovery), 1)
        self.assertIn(str(recovery[0]), str(raised.exception))
        self.assertEqual((recovery[0] / self.asset.relative_path).read_bytes(), previous_mesh)
        self.assertEqual((recovery[0] / 'modinfo.ini').read_bytes(), previous_info)

    def test_defaults_survive_reload_and_ignore_unknown_keys(self):
        path = self.root / 'config/defaults.json'
        settings = dict(parent_directory=str(self.root), folder_name='Variant A',
                        mod_name='Variant A', mod_author='Tester', last_character='033', other='ignored')
        package.save_defaults(path, settings)
        self.assertEqual(package.load_defaults(path), {k: v for k, v in settings.items() if k != 'other'})
        path.write_text('invalid JSON')
        self.assertEqual(package.load_defaults(path), {})


if __name__ == '__main__':
    unittest.main()
