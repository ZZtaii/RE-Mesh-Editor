"""Blender UI for SF6 mod folders, shared bundles, and dummy add-on menus."""
import json
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import sf6_mod_folder as package
from .sf6_hybrid_report import report_hybrid_export


def settings_path():
    return Path(bpy.utils.user_resource('CONFIG')) / 're_mesh_editor' / 'sf6_mod_folder_export.json'


def choose_collection(context):
    active = context.active_object
    if active:
        for collection in bpy.data.collections:
            if active.name in collection.all_objects:
                try:
                    package.asset_from_collection(collection)
                    return collection
                except ValueError:
                    pass
    for key in ('REMeshLastImportedCollection', 'REMeshLastExportedCollection'):
        collection = bpy.data.collections.get(context.scene.get(key, ''))
        if collection:
            return collection
    return next((c for c in bpy.data.collections if c.get('SF6PreserveSource')), None)


def existing_mod_defaults(collection, context):
    candidates = [collection.get('BatchExport_path', '')] if collection else []
    candidates.append(context.scene.re_mdf_toolpanel.modDirectory)
    for candidate in candidates:
        parts = Path(bpy.path.abspath(candidate)).parts if candidate else ()
        native_index = next((i for i, part in enumerate(parts) if part.lower() == 'natives'), None)
        if native_index is None:
            continue
        root = Path(*parts[:native_index])
        try:
            info = package.read_modinfo(root / 'modinfo.ini')
        except (OSError, ValueError):
            continue
        preview = root / info.get('screenshot', '')
        if not preview.resolve().is_relative_to(root.resolve()) or not preview.is_file():
            preview = ''
        return dict(parent_directory=str(root.parent), folder_name=root.name,
                    mod_name=info.get('name', root.name), mod_version=info.get('version', 'v1'),
                    mod_author=info.get('author', ''), mod_category=info.get('category', ''),
                    mod_description=info.get('description', ''), preview_path=str(preview),
                    extra_categories='; '.join(info.get('categories', [])[1:]),
                    bundle_name=info.get('nameasbundle', ''), parent_mod_name=info.get('addonfor', ''),
                    create_dummy_parent=False)
    return {}


def load_operator_defaults(context, collection):
    values = package.load_defaults(settings_path())
    if not values:
        try:
            values = json.loads(context.scene.get('SF6ModFolderDefaults', '{}'))
        except (ValueError, TypeError):
            values = {}
    if not isinstance(values, dict):
        values = {}
    if not values:
        values = existing_mod_defaults(collection, context)
    return package.filter_defaults(values)


def update_destination_notice(self, context):
    self.destination_notice = ''
    if self.export_content != 'MESH' or not self.parent_directory.strip() or not self.folder_name:
        return
    collection = bpy.data.collections.get(self.targetCollection)
    if collection is None:
        return
    try:
        asset = package.asset_from_collection(collection)
        package.validate_folder_name(self.folder_name)
    except ValueError:
        return
    try:
        conflicts = package.destination_conflicts(bpy.path.abspath(self.parent_directory), self.folder_name, asset)
    except (OSError, ValueError):
        self.destination_notice = 'Existing files could not be checked.\nCheck the destination before exporting.'
        return
    if conflicts:
        names = [f'{package.CHARACTERS.get(character, "esf" + character)} C{int(costume)}'
                 for character, costume in conflicts[:3]]
        existing = ', '.join(names)
        if len(conflicts) > 3:
            existing += f' (+{len(conflicts) - 3} more)'
        self.destination_notice = '\n'.join((
            'This mod folder already contains another character or costume:', existing,
            f'Exporting: {asset.character_name} C{int(asset.costume)} / {asset.slot_name}.',
            'Keep this folder to combine them, or choose a new Mod Folder Name.',
            'Export also updates this folder\'s mod information.'))


def update_collection(self, context):
    update_destination_notice(self, context)
    collection = bpy.data.collections.get(self.targetCollection)
    if collection is None:
        return
    try:
        asset = package.asset_from_collection(collection)
    except ValueError:
        return
    previous_category = '!Characters > ' + package.CHARACTERS.get(self.last_character, 'esf' + self.last_character)
    known_categories = {'!Characters > ' + name for name in package.CHARACTERS.values()}
    if (not self.mod_category or self.mod_category == previous_category or
            (not self.last_character and self.mod_category in known_categories)):
        self.mod_category = asset.category
    self.last_character = asset.character
    self.rotate90 = collection.get('SF6SourceRotate', True)


def destination_preview(self):
    try:
        if self.export_content == 'MENU':
            if not self.parent_directory or not self.folder_name:
                return ''
            package.validate_folder_name(self.folder_name)
            return str(Path(bpy.path.abspath(self.parent_directory)) / self.folder_name / 'modinfo.ini')
        collection = bpy.data.collections.get(self.targetCollection)
        asset = package.asset_from_collection(collection) if collection else None
        if not asset or not self.parent_directory or not self.folder_name:
            return ''
        package.validate_folder_name(self.folder_name)
        return str(Path(bpy.path.abspath(self.parent_directory)) / self.folder_name / asset.relative_path)
    except ValueError:
        return ''


def parent_destination_preview(self):
    if not self.parent_directory or not self.parent_mod_name:
        return ''
    try:
        folder = package.dummy_folder_name(self.parent_mod_name, self.parent_folder_name)
        return str(Path(bpy.path.abspath(self.parent_directory)) / folder / 'modinfo.ini')
    except ValueError:
        return ''


class ExportSF6ModFolder(bpy.types.Operator):
    bl_idname = 're_mesh.export_sf6_mod_folder'
    bl_label = 'Export SF6 Mod Folder'
    bl_description = 'Export an SF6 mesh mod or create a Fluffy bundle menu, with remembered mod information'

    targetCollection: StringProperty(name='Mesh Collection', update=update_collection)
    export_content: EnumProperty(name='Export Content', default='MESH', update=update_destination_notice, items=(
        ('MESH', 'Mesh Mod', 'Export the selected mesh and its mod information'),
        ('MENU', 'Menu Only', 'Create a DummyMod menu without exporting a mesh; use Parent Mod for nested menus')))
    parent_directory: StringProperty(name='Parent Directory', subtype='DIR_PATH', update=update_destination_notice,
        description='The mod folder will be created inside this directory, such as Fluffy Games/SF6/Mods')
    folder_name: StringProperty(name='Mod Folder Name', update=update_destination_notice)
    mod_name: StringProperty(name='Display Name', description='Name shown in Fluffy Mod Manager')
    mod_version: StringProperty(name='Version', default='v1')
    mod_author: StringProperty(name='Author')
    mod_category: StringProperty(name='Category')
    extra_categories: StringProperty(name='Additional Categories',
        description='Separate categories with semicolons, for example Colours; Hair. Each becomes its own category entry')
    mod_description: StringProperty(name='Description', description=r'Use \n for a line break in Fluffy')
    bundle_name: StringProperty(name='Bundle Name',
        description='Optional shared name (NameAsBundle). Options with the same name appear in one bundle')
    parent_mod_name: StringProperty(name='Parent Mod',
        description='Optional parent display name (AddonFor). Must exactly match the Name of its parent mod or menu')
    create_dummy_parent: BoolProperty(name='Create Parent Menu', default=False,
        description='Create a sibling dummy menu if needed; an existing matching menu is kept unchanged')
    parent_folder_name: StringProperty(name='Parent Menu Folder',
        description='Leave blank to use 00 followed by the parent mod name')
    parent_categories: StringProperty(name='Parent Categories',
        description='Separate with semicolons. Blank uses this option\'s categories; use !Characters > Multiple for a multi-character menu')
    parent_destination: StringProperty(name='Parent Menu Destination', get=parent_destination_preview)
    preview_path: StringProperty(name='Preview Image', subtype='FILE_PATH',
        description='Optional PNG or JPEG copied into the mod folder')
    last_character: StringProperty(options={'HIDDEN'})
    destination: StringProperty(name='Destination', get=destination_preview)
    destination_notice: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    exportBlendShapes: BoolProperty(name='SF6 Preserve Source Data', default=True,
        description='Mod folder export only; remembered independently of the batch exporter')
    sf6HybridPreserve: BoolProperty(name='SF6 Hybrid Shape Export (LOD0)', default=False,
        description='Rebuild LOD0 geometry/rig and keep unchanged source shape deltas on verified vertices; unmatched parts and vertices have zero deltas and lower LODs are lost')
    rotate90: BoolProperty(name='Convert Z Up to Y Up', default=True)

    def invoke(self, context, event):
        collection = bpy.data.collections.get(self.targetCollection) or choose_collection(context)
        values = load_operator_defaults(context, collection)
        for key, value in values.items():
            setattr(self, key, value)
        if collection:
            self.targetCollection = collection.name
            update_collection(self, context)
            try:
                asset = package.asset_from_collection(collection)
                suggested = f'{asset.character_name} C{int(asset.costume)} Mod'
                if not self.folder_name:
                    self.folder_name = suggested
                if not self.mod_name:
                    self.mod_name = suggested
            except ValueError:
                pass
        update_destination_notice(self, context)
        return context.window_manager.invoke_props_dialog(self, width=650, confirm_text='Export Mod Folder')

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, 'export_content')
        if self.export_content == 'MESH':
            layout.prop_search(self, 'targetCollection', bpy.data, 'collections')
        collection = bpy.data.collections.get(self.targetCollection)
        try:
            asset = package.asset_from_collection(collection) if collection and self.export_content == 'MESH' else None
            if asset and self.export_content == 'MESH':
                layout.label(text=f'{asset.character_name} (esf{asset.character})  |  Costume {asset.costume}  |  {asset.slot_name} ({asset.slot})')
        except ValueError as error:
            layout.label(text=str(error), icon='ERROR')
        layout.separator()
        layout.prop(self, 'parent_directory')
        layout.prop(self, 'folder_name')
        layout.prop(self, 'destination')
        if self.destination_notice:
            notice = layout.box()
            for index, line in enumerate(self.destination_notice.splitlines()):
                notice.label(text=line, icon='INFO' if index == 0 else 'NONE')
        layout.separator()
        for name in ('mod_name', 'mod_version', 'mod_author', 'mod_category', 'extra_categories', 'mod_description', 'preview_path'):
            layout.prop(self, name)
        grouping = layout.box()
        grouping.label(text='Bundles and Add-on Menus')
        grouping.prop(self, 'bundle_name')
        grouping.prop(self, 'parent_mod_name')
        if self.parent_mod_name:
            grouping.prop(self, 'create_dummy_parent')
            if self.create_dummy_parent:
                grouping.prop(self, 'parent_folder_name')
                grouping.prop(self, 'parent_categories')
                grouping.prop(self, 'parent_destination')
        if self.export_content == 'MESH':
            layout.separator()
            layout.prop(self, 'exportBlendShapes')
            row = layout.row()
            row.enabled = self.exportBlendShapes and collection is not None and bool(collection.get('SF6PreserveSource'))
            row.prop(self, 'sf6HybridPreserve')
            if self.sf6HybridPreserve:
                layout.label(text='Verified source deltas kept; unmatched parts get zero.', icon='INFO')
                layout.label(text='Rebuilt geometry/rig; lower LODs are lost.')
            layout.prop(self, 'rotate90')

    def execute(self, context):
        from .blender_re_mesh import exportREMeshFile
        collection = bpy.data.collections.get(self.targetCollection)
        mesh_export = self.export_content == 'MESH'
        if mesh_export and collection is None:
            self.report({'ERROR'}, 'Select the mesh collection to export.')
            return {'CANCELLED'}
        if mesh_export and self.sf6HybridPreserve and not self.exportBlendShapes:
            self.report({'ERROR'}, 'SF6 Hybrid Shape Export requires Preserve Source Data to be enabled.')
            return {'CANCELLED'}
        try:
            asset = package.asset_from_collection(collection) if mesh_export else None
            if not self.parent_directory.strip():
                raise ValueError('Choose the parent directory for the mod folder.')
            parent = bpy.path.abspath(self.parent_directory)
            preview = bpy.path.abspath(self.preview_path) if self.preview_path else ''
            preferences = context.preferences.addons[__package__.split('.')[0]].preferences
            options = dict(targetCollection=collection.name if collection else '', selectedOnly=False,
                           exportBlendShapes=self.exportBlendShapes, sf6HybridPreserve=self.sf6HybridPreserve,
                           rotate90=self.rotate90)
            for name in ('exportAllLODs', 'autoSolveRepeatedUVs', 'preserveSharpEdges',
                         'useBlenderMaterialName', 'preserveBoneMatrices', 'exportBoundingBoxes'):
                options[name] = getattr(preferences, 'default_' + name)
            category = self.mod_category or (asset.category if asset else '')
            categories = package.categories_from_text(category) + package.categories_from_text(self.extra_categories)
            metadata = dict(name=self.mod_name, version=self.mod_version, author=self.mod_author,
                            category=categories, description=self.mod_description,
                            nameasbundle=self.bundle_name, addonfor=self.parent_mod_name,
                            dummymod='' if mesh_export else 'True')
            output = package.export_mod_folder(parent, self.folder_name, asset, metadata, preview,
                                               lambda path: exportREMeshFile(path, options),
                                               create_dummy_parent=self.create_dummy_parent and bool(self.parent_mod_name),
                                               parent_folder_name=self.parent_folder_name,
                                               parent_categories=self.parent_categories)
        except (ValueError, OSError, KeyError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        if mesh_export:
            context.scene['REMeshLastExportedCollection'] = collection.name
            context.scene['REMeshLastExportedMeshVersion'] = 230110883
            collection['BatchExport_path'] = str(output)
            for key, value in options.items():
                if key not in ('targetCollection', 'selectedOnly', 'exportBlendShapes', 'sf6HybridPreserve') and not key.startswith('_'):
                    collection['BatchExport_' + key] = value
        mod_root = Path(parent).resolve() / self.folder_name
        if mesh_export:
            context.scene.re_mdf_toolpanel.modDirectory = str(mod_root / 'natives' / 'stm')
        values = {key: getattr(self, key) for key in package.DEFAULT_FIELDS}
        if not mesh_export:
            values['exportBlendShapes'] = load_operator_defaults(context, collection).get('exportBlendShapes', True)
        values.update(parent_directory=str(Path(parent).resolve()),
                      preview_path=str(Path(preview).resolve()) if preview else '',
                      mod_category=category, last_character=asset.character if asset else self.last_character)
        context.scene['SF6ModFolderDefaults'] = json.dumps(values)
        try:
            package.save_defaults(settings_path(), values)
        except OSError as error:
            self.report({'WARNING'}, 'Mod exported; defaults could not be saved: ' + str(error))
        self.report({'INFO'}, 'Exported mod folder: ' + str(mod_root))
        if mesh_export and self.sf6HybridPreserve:
            report_hybrid_export(self, options.get('_sf6HybridReport'))
        return {'FINISHED'}
