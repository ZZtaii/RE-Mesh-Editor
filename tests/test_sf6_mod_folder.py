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

    def export(self, folder='Variant A', callback=None, preview='', **options):
        def write_mesh(path):
            Path(path).write_bytes(b'MESH test output')
            return True
        return package.export_mod_folder(self.root, folder, self.asset, self.values,
                                         preview, callback or write_mesh, **options)

    def test_routes_character_costume_and_slot_without_repadding(self):
        self.assertEqual(self.asset.relative_path.as_posix(),
                         'natives/stm/product/model/esf/esf033/002/01/esf033_002_01.mesh.230110883')
        self.assertEqual(self.asset.category, '!Characters > Yasmine')
        for slot, label in (('00', 'Head'), ('01', 'Body'), ('02', 'Hair'), ('03', 'Mesh 03')):
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
        for key in ('name', 'author', 'addonfor', 'nameasbundle'):
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
        self.assertEqual((recovery[0] / 'Variant A' / self.asset.relative_path).read_bytes(), previous_mesh)
        self.assertEqual((recovery[0] / 'Variant A/modinfo.ini').read_bytes(), previous_info)

    def test_multiple_categories_and_description_linebreaks(self):
        self.values.update(category=['!Characters > Ingrid', 'Colours', 'Hair', 'Colours'],
                           description='First line\r\nSecond line\nname=not a new field')
        self.export()
        info_path = self.root / 'Variant A/modinfo.ini'
        info = package.read_modinfo(info_path)
        self.assertEqual(info['categories'], ['!Characters > Ingrid', 'Colours', 'Hair'])
        self.assertEqual(info['name'], 'Variant A')
        self.assertEqual(info['description'], r'First line\nSecond line\nname=not a new field')
        self.assertEqual(package.categories_from_text(' Colours; Hair ;Colours '), ['Colours', 'Hair'])
        text = package.render_modinfo(self.values, 'category=Old\nCategory=Old2\n')
        self.assertNotIn('Old', text)
        self.assertEqual(text.count('category='), 3)

    def test_shared_bundle_and_standalone_switch(self):
        self.values.update(nameasbundle='Hair Choices', addonfor='', dummymod='')
        self.export(folder='Hair A')
        self.values['name'] = 'Variant B'
        self.export(folder='Hair B')
        for folder in ('Hair A', 'Hair B'):
            self.assertEqual(package.read_modinfo(self.root / folder / 'modinfo.ini')['nameasbundle'], 'Hair Choices')
        self.values['nameasbundle'] = ''
        self.export(folder='Hair B')
        self.assertNotIn('nameasbundle', package.read_modinfo(self.root / 'Hair B/modinfo.ini'))
        self.assertTrue((self.root / 'Hair A/modinfo.ini').exists())

    def test_create_parent_menu_and_reuse_it_for_variants(self):
        self.values.update(addonfor='Character Choices', nameasbundle='', dummymod='')
        self.export(create_dummy_parent=True, parent_folder_name='000 Menu',
                    parent_categories='!Characters > Multiple; Colours')
        parent = self.root / '000 Menu/modinfo.ini'
        info = package.read_modinfo(parent)
        self.assertEqual(info['name'], 'Character Choices')
        self.assertEqual(info['dummymod'], 'True')
        self.assertEqual(info['categories'], ['!Characters > Multiple', 'Colours'])
        self.assertFalse((parent.parent / 'natives').exists())
        parent.write_text(parent.read_text() + 'description=Keep parent description\n')
        before = parent.read_bytes()
        self.values['name'] = 'Variant B'
        self.export(folder='Variant B', create_dummy_parent=True, parent_folder_name='000 Menu')
        self.assertEqual(parent.read_bytes(), before)
        self.assertEqual(package.read_modinfo(self.root / 'Variant B/modinfo.ini')['addonfor'], info['name'])

    def test_nested_dummy_menus_and_leaf_options(self):
        for folder, name, parent_name in (('000 Root', 'Character Pack', ''),
                                         ('030 Hair', 'Hair Variations', 'Character Pack')):
            result = package.export_mod_folder(self.root, folder, None,
                dict(name=name, addonfor=parent_name, dummymod='True'), '', None)
            self.assertEqual(result.name, 'modinfo.ini')
            self.assertFalse((result.parent / 'natives').exists())
        self.values['addonfor'] = 'Hair Variations'
        self.export(folder='031 Braid')
        self.assertEqual(package.read_modinfo(self.root / '030 Hair/modinfo.ini')['addonfor'], 'Character Pack')
        self.assertEqual(package.read_modinfo(self.root / '031 Braid/modinfo.ini')['addonfor'], 'Hair Variations')

    def test_dummy_menu_does_not_replace_real_mesh_mod(self):
        self.export()
        ini = self.root / 'Variant A/modinfo.ini'
        before = ini.read_bytes()
        with self.assertRaises(ValueError):
            package.export_mod_folder(self.root, 'Variant A', None,
                                      dict(name='Menu', dummymod='True'), '', None)
        self.assertEqual(ini.read_bytes(), before)

    def test_parent_name_and_folder_collisions_fail_before_export(self):
        self.values.update(addonfor='Variant A')
        with self.assertRaises(ValueError):
            self.export(create_dummy_parent=True)
        self.values['addonfor'] = 'Parent'
        with self.assertRaises(ValueError):
            self.export(create_dummy_parent=True, parent_folder_name='Variant A')
        root = self.root / '000 Other'
        root.mkdir()
        (root / 'modinfo.ini').write_text('name=Other\nDummyMod=True\n')
        with self.assertRaises(ValueError):
            self.export(create_dummy_parent=True, parent_folder_name=root.name)
        self.assertFalse((self.root / 'Variant A').exists())

    def test_failed_mesh_creates_no_parent_menu(self):
        self.values['addonfor'] = 'Parent'
        with self.assertRaises(ValueError):
            self.export(callback=lambda path: False, create_dummy_parent=True)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_parent_install_failure_rolls_back_existing_variant(self):
        result = self.export()
        previous = result.read_bytes()
        old_info = (self.root / 'Variant A/modinfo.ini').read_bytes()
        self.values.update(addonfor='Parent', version='v2')
        real_replace = package.os.replace
        def fail_parent(source, target):
            if Path(target).parent.name == '00 Parent':
                raise OSError('locked parent')
            return real_replace(source, target)
        with patch.object(package.os, 'replace', side_effect=fail_parent), self.assertRaises(OSError):
            self.export(create_dummy_parent=True)
        self.assertEqual(result.read_bytes(), previous)
        self.assertEqual((self.root / 'Variant A/modinfo.ini').read_bytes(), old_info)
        self.assertFalse((self.root / '00 Parent').exists())

    def test_parent_install_failure_removes_new_variant(self):
        self.values['addonfor'] = 'Parent'
        real_replace = package.os.replace
        def fail_parent(source, target):
            if Path(target).parent.name == '00 Parent':
                raise OSError('locked parent')
            return real_replace(source, target)
        with patch.object(package.os, 'replace', side_effect=fail_parent), self.assertRaises(OSError):
            self.export(create_dummy_parent=True)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_bundle_fields_survive_settings_reload(self):
        path = self.root / 'settings.json'
        values = dict(bundle_name='Variants', parent_mod_name='Character Menu',
                      create_dummy_parent=True, parent_folder_name='000 Menu',
                      parent_categories='!Characters > Multiple; Colours',
                      extra_categories='Hair; Colours', export_content='MENU')
        package.save_defaults(path, values)
        self.assertEqual(package.load_defaults(path), values)
        self.assertNotIn('export_content', package.filter_defaults(dict(export_content='invalid')))

    def test_destination_notice_ignores_empty_and_unrelated_folders(self):
        self.assertEqual(package.destination_conflicts(self.root, 'New', self.asset), [])
        root = self.root / 'Named Ingrid C1'
        empty = root / 'natives/stm/product/model/esf/esf032/001/02'
        empty.mkdir(parents=True)
        (root / 'modinfo.ini').write_text('name=Ingrid C1\n')
        (root / 'readme.txt').write_text('unrelated')
        self.assertEqual(package.destination_conflicts(self.root, root.name, self.asset), [])

    def test_destination_notice_allows_same_costume_other_parts(self):
        self.export()
        hair = package.SF6Asset('033', '002', '02')
        self.assertEqual(package.destination_conflicts(self.root, 'Variant A', hair), [])

    def test_destination_notice_finds_other_characters_and_costumes(self):
        for asset in (self.asset, package.SF6Asset('033', '001', '01'),
                      package.SF6Asset('032', '001', '02'), package.SF6Asset('032', '001', '00')):
            path = self.root / 'Combined' / asset.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'payload')
        self.assertEqual(package.destination_conflicts(self.root, 'Combined', self.asset),
                         [('032', '001'), ('033', '001')])

    def test_destination_notice_includes_textures_and_ignores_siblings(self):
        path = self.root / 'Combined/natives/stm/product/model/esf/esf099/003/02/texture/hair.tex.28'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'texture')
        self.assertEqual(package.destination_conflicts(self.root, 'Combined', self.asset), [('099', '003')])
        self.assertEqual(package.destination_conflicts(self.root, 'Another Option', self.asset), [])

    def test_destination_notice_reports_scan_failure(self):
        self.export()
        with patch.object(Path, 'iterdir', side_effect=PermissionError('cannot inspect')):
            with self.assertRaises(PermissionError):
                package.destination_conflicts(self.root, 'Variant A', self.asset)

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
