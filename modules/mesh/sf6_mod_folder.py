"""SF6 filename routing and Fluffy mod-folder packaging, independent of Blender."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


CHARACTERS = {
    '001': 'Ryu', '002': 'Luke', '003': 'Kimberly', '004': 'Chun-Li',
    '005': 'Manon', '006': 'Zangief', '007': 'JP', '008': 'Dhalsim',
    '009': 'Cammy', '010': 'Ken', '011': 'Dee Jay', '012': 'Lily',
    '013': 'A.K.I', '014': 'Rashid', '015': 'Blanka', '016': 'Juri',
    '017': 'Marisa', '018': 'Guile', '019': 'Ed', '020': 'E. Honda',
    '021': 'Jamie', '022': 'Akuma', '025': 'Sagat', '026': 'M. Bison',
    '027': 'Terry', '028': 'Mai', '029': 'Elena', '030': 'C.Viper',
    '031': 'Alex', '032': 'Ingrid', '033': 'Yasmine',
}
MESH_NAME = re.compile(
    r'esf(?P<character>\d{3})_(?P<costume>\d{3})_(?P<slot>\d{2})'
    r'\.mesh(?:\.230110883)?(?:\.\d{3})?', re.IGNORECASE)
FIELDS = ('name', 'version', 'author', 'category', 'description', 'screenshot')
DEFAULT_FIELDS = ('parent_directory', 'folder_name', 'mod_name', 'mod_version',
                  'mod_author', 'mod_category', 'mod_description', 'preview_path',
                  'last_character')


@dataclass(frozen=True)
class SF6Asset:
    character: str
    costume: str
    slot: str

    @property
    def character_name(self):
        return CHARACTERS.get(self.character, 'esf' + self.character)

    @property
    def filename(self):
        return f'esf{self.character}_{self.costume}_{self.slot}.mesh.230110883'

    @property
    def relative_path(self):
        return Path('natives', 'stm', 'product', 'model', 'esf',
                    'esf' + self.character, self.costume, self.slot, self.filename)

    @property
    def category(self):
        return '!Characters > ' + self.character_name

    @property
    def slot_name(self):
        return {'01': 'Head', '02': 'Hair'}.get(self.slot, 'Mesh ' + self.slot)


def parse_asset_name(value):
    name = str(value).replace('\\', '/').rsplit('/', 1)[-1]
    match = MESH_NAME.fullmatch(name)
    if match is None:
        raise ValueError('Cannot determine the SF6 destination. Import a mesh named '
                         'esfNNN_CCC_SS.mesh.230110883, or select its mesh collection.')
    return SF6Asset(**match.groupdict())


def asset_from_collection(collection):
    imported_name = collection.get('SF6SourceFilename')
    if imported_name:
        return parse_asset_name(imported_name)
    # Compatibility with projects saved before the filename metadata existed.
    for value in (collection.get('~ASSETPATH'), collection.name,
                  collection.get('BatchExport_path')):
        if value:
            try:
                return parse_asset_name(value)
            except ValueError:
                pass
    raise ValueError('The selected collection has no recognizable SF6 mesh filename.')


def validate_folder_name(name):
    if not name or name in ('.', '..') or name != name.strip() or name.endswith('.'):
        raise ValueError('Enter a folder name without leading/trailing spaces or a trailing dot.')
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError('The mod folder name cannot contain path separators or reserved characters.')
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                     *(f'COM{i}' for i in range(1, 10)),
                                     *(f'LPT{i}' for i in range(1, 10))}:
        raise ValueError('The mod folder name is reserved by Windows.')
    return name


def _field_value(value):
    value = str(value)
    if any(ord(character) < 32 for character in value):
        raise ValueError('Mod information must use single-line values.')
    return value.strip()


def read_modinfo(path):
    result = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith((';', '#')):
            key, value = line.split('=', 1)
            result[key.strip().lower()] = value.strip()
    return result


def render_modinfo(values, existing=''):
    values = {key: _field_value(values.get(key, '')) for key in FIELDS}
    if not values['name']:
        raise ValueError('Enter the name displayed in Fluffy Mod Manager.')
    result, seen = [], set()
    for line in existing.splitlines():
        key = line.split('=', 1)[0].strip().lower() if '=' in line else ''
        if key in values:
            if key not in seen and values[key]:
                result.append(key + '=' + values[key])
            seen.add(key)
        else:
            result.append(line)
    result.extend(key + '=' + values[key] for key in FIELDS if key not in seen and values[key])
    return '\n'.join(result).rstrip() + '\n'


def load_defaults(path):
    try:
        values = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(values, dict):
        return {}
    return {key: values[key] for key in DEFAULT_FIELDS if isinstance(values.get(key), str)}


def save_defaults(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {key: values[key] for key in DEFAULT_FIELDS if isinstance(values.get(key), str)}
    fd, temporary = tempfile.mkstemp(prefix='.sf6-defaults-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _contained_path(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('A destination path points outside the selected mod folder.')
    return path


class RecoveryRequiredError(OSError):
    """Keep the staging directory when a failed update cannot be rolled back."""


def _publish_existing(staged, destination, relative_paths, backup):
    # Roll back this export's files on failure; unrelated textures/MDFs remain.
    targets = [_contained_path(destination, relative) for relative in relative_paths]
    for target, relative in zip(targets, relative_paths):
        if target.exists():
            if not target.is_file():
                raise ValueError('An export destination is not a file: ' + str(target))
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
    changed, created_dirs = [], []
    try:
        for target, relative in zip(targets, relative_paths):
            missing = []
            directory = target.parent
            while not directory.exists():
                missing.append(directory)
                directory = directory.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_dirs.append(directory)
            os.replace(staged / relative, target)
            changed.append((target, relative))
    except Exception as error:
        restore_errors = []
        for target, relative in reversed(changed):
            saved = backup / relative
            try:
                if saved.exists():
                    shutil.copy2(saved, target)
                else:
                    target.unlink()
            except OSError as restore_error:
                restore_errors.append(restore_error)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        if restore_errors:
            raise RecoveryRequiredError(
                f'Export failed and some files could not be restored. '
                f'Original files are saved in {backup}. Error: {error}') from error
        raise


def export_mod_folder(parent_directory, folder_name, asset, values, preview_path, export_mesh):
    """Stage a mesh, INI and optional image before updating the chosen mod folder."""
    parent = Path(parent_directory).expanduser().resolve()
    if not parent.is_dir():
        raise ValueError('Choose an existing parent directory for the mod folder.')
    destination = parent / validate_folder_name(folder_name)
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ValueError('The mod destination must be a regular folder.')
    relative_paths = [asset.relative_path, Path('modinfo.ini')]
    for relative in relative_paths:
        _contained_path(destination, relative)
    metadata = dict(values)
    preview = None
    if preview_path:
        preview = Path(preview_path).expanduser().resolve()
        if not preview.is_file() or preview.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
            raise ValueError('Choose an existing PNG or JPEG preview image, or leave it blank.')
        image_name = 'screenshot' + preview.suffix.lower()
        _contained_path(destination, image_name)
        relative_paths.append(Path(image_name))
        metadata['screenshot'] = image_name
    else:
        metadata['screenshot'] = ''
    ini_path = destination / 'modinfo.ini'
    existing = ini_path.read_text(encoding='utf-8-sig') if ini_path.is_file() else ''
    ini = render_modinfo(metadata, existing)
    temporary = Path(tempfile.mkdtemp(prefix='.re-mesh-export-', dir=parent))
    keep_backup = False
    try:
        staged = temporary / 'mod'
        staged_mesh = staged / asset.relative_path
        staged_mesh.parent.mkdir(parents=True)
        if not export_mesh(str(staged_mesh)) or not staged_mesh.is_file():
            raise ValueError('Mesh export failed; the mod folder was not updated.')
        (staged / 'modinfo.ini').write_text(ini, encoding='utf-8')
        if preview:
            shutil.copy2(preview, staged / metadata['screenshot'])
        if destination.exists():
            _publish_existing(staged, destination, relative_paths, temporary / 'backup')
        else:
            os.replace(staged, destination)
    except RecoveryRequiredError:
        keep_backup = True
        raise
    finally:
        if not keep_backup:
            shutil.rmtree(temporary)
    return destination / asset.relative_path
