"""Blender UI for packaging one SF6 mesh as a Fluffy mod folder."""
import json
from pathlib import Path

import bpy
from bpy.props import BoolProperty, StringProperty

from . import sf6_mod_folder as package


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
                    mod_description=info.get('description', ''), preview_path=str(preview))
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
    return {key: value for key, value in values.items()
            if key in package.DEFAULT_FIELDS and isinstance(value, str)}


def update_collection(self, context):
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
        collection = bpy.data.collections.get(self.targetCollection)
        asset = package.asset_from_collection(collection) if collection else None
        if not asset or not self.parent_directory or not self.folder_name:
            return ''
        package.validate_folder_name(self.folder_name)
        return str(Path(bpy.path.abspath(self.parent_directory)) / self.folder_name / asset.relative_path)
    except ValueError:
        return ''


class ExportSF6ModFolder(bpy.types.Operator):
    bl_idname = 're_mesh.export_sf6_mod_folder'
    bl_label = 'Export SF6 Mod Folder'
    bl_description = 'Export the mesh and modinfo.ini to a Fluffy mod folder, deriving its game path from the imported mesh'

    targetCollection: StringProperty(name='Mesh Collection', update=update_collection)
    parent_directory: StringProperty(name='Parent Directory', subtype='DIR_PATH',
        description='The mod folder will be created inside this directory, such as Fluffy Games/SF6/Mods')
    folder_name: StringProperty(name='Mod Folder Name')
    mod_name: StringProperty(name='Display Name', description='Name shown in Fluffy Mod Manager')
    mod_version: StringProperty(name='Version', default='v1')
    mod_author: StringProperty(name='Author')
    mod_category: StringProperty(name='Category')
    mod_description: StringProperty(name='Description')
    preview_path: StringProperty(name='Preview Image', subtype='FILE_PATH',
        description='Optional PNG or JPEG copied into the mod folder')
    last_character: StringProperty(options={'HIDDEN'})
    destination: StringProperty(name='Mesh Destination', get=destination_preview)
    exportBlendShapes: BoolProperty(name='SF6 Preserve Source Data', default=True)
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
        return context.window_manager.invoke_props_dialog(self, width=650, confirm_text='Export Mod Folder')

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop_search(self, 'targetCollection', bpy.data, 'collections')
        collection = bpy.data.collections.get(self.targetCollection)
        try:
            asset = package.asset_from_collection(collection) if collection else None
            if asset:
                layout.label(text=f'{asset.character_name} (esf{asset.character})  |  Costume {asset.costume}  |  {asset.slot_name} ({asset.slot})')
        except ValueError as error:
            layout.label(text=str(error), icon='ERROR')
        layout.separator()
        layout.prop(self, 'parent_directory')
        layout.prop(self, 'folder_name')
        layout.prop(self, 'destination')
        layout.separator()
        for name in ('mod_name', 'mod_version', 'mod_author', 'mod_category', 'mod_description', 'preview_path'):
            layout.prop(self, name)
        layout.separator()
        layout.prop(self, 'exportBlendShapes')
        layout.prop(self, 'rotate90')

    def execute(self, context):
        from .blender_re_mesh import exportREMeshFile
        collection = bpy.data.collections.get(self.targetCollection)
        if collection is None:
            self.report({'ERROR'}, 'Select the mesh collection to export.')
            return {'CANCELLED'}
        try:
            asset = package.asset_from_collection(collection)
            if not self.parent_directory.strip():
                raise ValueError('Choose the parent directory for the mod folder.')
            parent = bpy.path.abspath(self.parent_directory)
            preview = bpy.path.abspath(self.preview_path) if self.preview_path else ''
            preferences = context.preferences.addons[__package__.split('.')[0]].preferences
            options = dict(targetCollection=collection.name, selectedOnly=False,
                           exportBlendShapes=self.exportBlendShapes, rotate90=self.rotate90)
            for name in ('exportAllLODs', 'autoSolveRepeatedUVs', 'preserveSharpEdges',
                         'useBlenderMaterialName', 'preserveBoneMatrices', 'exportBoundingBoxes'):
                options[name] = getattr(preferences, 'default_' + name)
            metadata = dict(name=self.mod_name, version=self.mod_version, author=self.mod_author,
                            category=self.mod_category or asset.category, description=self.mod_description)
            output = package.export_mod_folder(parent, self.folder_name, asset, metadata, preview,
                                               lambda path: exportREMeshFile(path, options))
        except (ValueError, OSError, KeyError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        context.scene['REMeshLastExportedCollection'] = collection.name
        context.scene['REMeshLastExportedMeshVersion'] = 230110883
        collection['BatchExport_path'] = str(output)
        for key, value in options.items():
            if key not in ('targetCollection', 'selectedOnly'):
                collection['BatchExport_' + key] = value
        mod_root = Path(parent).resolve() / self.folder_name
        context.scene.re_mdf_toolpanel.modDirectory = str(mod_root / 'natives' / 'stm')
        values = {key: getattr(self, key) for key in package.DEFAULT_FIELDS}
        values.update(parent_directory=str(Path(parent).resolve()),
                      preview_path=str(Path(preview).resolve()) if preview else '',
                      mod_category=metadata['category'], last_character=asset.character)
        context.scene['SF6ModFolderDefaults'] = json.dumps(values)
        try:
            package.save_defaults(settings_path(), values)
        except OSError as error:
            self.report({'WARNING'}, 'Mod exported; defaults could not be saved: ' + str(error))
        self.report({'INFO'}, 'Exported mod folder: ' + str(mod_root))
        return {'FINISHED'}
