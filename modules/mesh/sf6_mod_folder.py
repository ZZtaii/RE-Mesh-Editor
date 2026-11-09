"""SF6 filename routing and Fluffy mod-folder packaging, independent of Blender."""
from dataclasses import dataclass
import hashlib
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
BUNDLE_FIELDS = ('nameasbundle', 'addonfor', 'dummymod')
DEFAULT_FIELDS = ('parent_directory', 'folder_name', 'mod_name', 'mod_version',
                  'mod_author', 'mod_category', 'mod_description', 'preview_path',
                  'last_character', 'extra_categories', 'bundle_name', 'parent_mod_name',
                  'create_dummy_parent', 'parent_folder_name', 'parent_categories', 'export_content',
                  'exportBlendShapes', 'selectedOnly', 'sf6HybridPreserve',
                  'targetCollection', 'rotate90', 'sf6ExportWorkflow', 'sf6LodPolicy',
                  'autoSolveRepeatedUVs', 'preserveSharpEdges', 'splitLoopVertices', 'preserveBoneMatrices')
# Versioned choices are saved only after the complete folder transaction succeeds.
# Rig decisions and consent/plan tokens are execution-local, never defaults.
UNIFIED_DEFAULT_FIELDS = ('sf6ExportWorkflow', 'sf6LodPolicy')
# Some manually saved profiles contain the old selected-only LOD scope. Keep
# that evidence without introducing a new visible or defaulted operator field.
PROFILE_EVIDENCE_FIELDS = ('exportAllLODs',)
PROCESSING_DEFAULT_FIELDS = ('autoSolveRepeatedUVs', 'preserveSharpEdges',
                             'splitLoopVertices', 'preserveBoneMatrices')


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
        return {'00': 'Head', '01': 'Body', '02': 'Hair'}.get(self.slot, 'Mesh ' + self.slot)


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


def destination_conflicts(parent, folder_name, asset):
    """Find other character/costume payloads, without reading or changing files."""
    validate_folder_name(folder_name)
    mod_root = Path(parent) / folder_name
    model_root = _contained_path(mod_root, Path('natives/stm/product/model/esf'))
    if not model_root.exists():
        return []

    def scan_error(error):
        raise error

    conflicts = set()
    for character_dir in model_root.iterdir():
        match = re.fullmatch(r'esf(\d{3})', character_dir.name, re.IGNORECASE)
        if not match or not character_dir.is_dir():
            continue
        _contained_path(mod_root, character_dir.relative_to(mod_root))
        character = match[1]
        for costume_dir in character_dir.iterdir():
            costume = costume_dir.name
            if (not re.fullmatch(r'\d{3}', costume) or not costume_dir.is_dir() or
                    (character, costume) == (asset.character, asset.costume)):
                continue
            _contained_path(mod_root, costume_dir.relative_to(mod_root))
            # Empty directories aren't a mod payload. Include textures/MDFs too.
            visited = set()
            for directory, subdirs, files in os.walk(costume_dir, onerror=scan_error, followlinks=False):
                resolved = Path(directory).resolve()
                if resolved in visited:
                    subdirs.clear()
                    continue
                visited.add(resolved)
                for name in subdirs:
                    _contained_path(mod_root, Path(directory, name).relative_to(mod_root))
                if files:
                    conflicts.add((character, costume))
                    break
    return sorted(conflicts)


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
            key, value = key.strip().lower(), value.strip()
            if key == 'category':
                result.setdefault('categories', []).append(value)
                result.setdefault('category', value)
            else:
                result[key] = value
    return result


def categories_from_text(value):
    """The dialog accepts semicolon-separated categories, written as repeated keys."""
    entries = value.split(';') if isinstance(value, str) else value
    return list(dict.fromkeys(text for entry in entries if (text := _field_value(entry))))


def _description_value(value):
    value = str(value).replace('\r\n', '\n').replace('\r', '\n').replace('\n', r'\n')
    return _field_value(value)


def render_modinfo(values, existing=''):
    normalized = {}
    for key in FIELDS + tuple(key for key in BUNDLE_FIELDS if key in values):
        value = values.get(key, '')
        if key == 'category':
            normalized[key] = categories_from_text(value)
        else:
            value = _description_value(value) if key == 'description' else _field_value(value)
            normalized[key] = [value] if value else []
    if not normalized['name']:
        raise ValueError('Enter the name displayed in Fluffy Mod Manager.')
    result, seen = [], set()
    for line in existing.splitlines():
        key = line.split('=', 1)[0].strip().lower() if '=' in line else ''
        if key in normalized:
            if key not in seen:
                result.extend(key + '=' + value for value in normalized[key])
            seen.add(key)
        else:
            result.append(line)
    result.extend(key + '=' + value for key in normalized if key not in seen for value in normalized[key])
    return '\n'.join(result).rstrip() + '\n'


def filter_defaults(values):
    result = {key: values[key] for key in DEFAULT_FIELDS
              if isinstance(values.get(key), bool if key in (
                  'create_dummy_parent', 'exportBlendShapes', 'selectedOnly',
                  'sf6HybridPreserve', 'rotate90') else str)}
    # Preserve malformed and future-version values for explicit review. Dropping
    # one here could silently reactivate legacy routing on the next attempt.
    for key in UNIFIED_DEFAULT_FIELDS + PROFILE_EVIDENCE_FIELDS + PROCESSING_DEFAULT_FIELDS:
        if key in values:
            result[key] = values[key]
    if result.get('export_content') not in ('MESH', 'MENU'):
        result.pop('export_content', None)
    return result


def load_defaults(path):
    try:
        values = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(values, dict):
        return {}
    return filter_defaults(values)


def save_defaults(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    values = filter_defaults(values)
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


def _file_state(path):
    path = Path(path)
    if path.is_symlink():
        return {'kind': 'symlink', 'target': os.readlink(path)}
    if not path.exists():
        return {'kind': 'missing'}
    if not path.is_file():
        return {'kind': 'directory' if path.is_dir() else 'other'}
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'kind': 'file', 'sha256': digest.hexdigest(), 'size': path.stat().st_size}


def _publish_existing(staged, destination, relative_paths, backup, transaction=None):
    # Back up and verify every affected file. An attempted replace is included
    # in rollback even if the OS raises after changing the destination.
    transaction = transaction if transaction is not None else {}
    targets = [_contained_path(destination, relative) for relative in relative_paths]
    original = {}
    transaction['affected_paths'] = [str(target) for target in targets]
    for target, relative in zip(targets, relative_paths):
        original[relative] = _file_state(target)
        if target.is_symlink():
            raise ValueError('A destination file is a symbolic link. Choose another Mod Folder Name.')
        if target.exists():
            if not target.is_file():
                raise ValueError('An export destination is not a file: ' + str(target))
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
            if _file_state(saved) != original[relative]:
                raise OSError('The original destination could not be backed up safely: ' + str(target))
    attempted, created_dirs = [], []
    try:
        for target, relative in zip(targets, relative_paths):
            missing = []
            directory = target.parent
            while not directory.exists():
                missing.append(directory)
                directory = directory.parent
            for directory in reversed(missing):
                transaction['publication_started'] = True
                created_dirs.append(directory)
                directory.mkdir()
            transaction['publication_started'] = True
            transaction['folder_unchanged'] = False
            attempted.append((target, relative))
            os.replace(staged / relative, target)
    except Exception as error:
        restore_errors = []
        for target, relative in reversed(attempted):
            saved = backup / relative
            try:
                if saved.exists():
                    shutil.copy2(saved, target)
                else:
                    target.unlink(missing_ok=True)
                if _file_state(target) != original[relative]:
                    raise OSError('Restored file verification failed: ' + str(target))
            except OSError as restore_error:
                restore_errors.append(restore_error)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError as restore_error:
                if directory.exists():
                    restore_errors.append(restore_error)
        transaction.update(rollback_verified=not restore_errors,
                           folder_unchanged=not restore_errors)
        if restore_errors:
            original_files = {relative: state for relative, state in original.items()
                              if state['kind'] == 'file'}
            verified_backups = 0
            for relative, state in original_files.items():
                try:
                    verified_backups += _file_state(backup / relative) == state
                except OSError:
                    pass
            all_backups_verified = bool(original_files) and verified_backups == len(original_files)
            recovery = backup if backup.is_dir() else backup.parent if backup.parent.is_dir() else None
            transaction.update(publication_outcome='recovery_required',
                recovery_kind='verified_backups' if all_backups_verified else
                    'unverified_backups' if original_files else 'cleanup_only',
                verified_backup_files=verified_backups)
            transaction.pop('recovery_directory', None)
            if recovery is not None:
                transaction['recovery_directory'] = str(recovery)
            if all_backups_verified:
                detail = f'Original files are saved in {backup}.'
            elif original_files:
                detail = 'The original-file backups could not all be verified.'
            else:
                detail = ('Newly created files or directories could not be removed. '
                          'No original destination files were replaced.')
            if not all_backups_verified:
                detail += (f' Recovery workspace: {recovery}.' if recovery is not None else
                           ' No recovery workspace is available; review the affected output paths.')
            raise RecoveryRequiredError(
                f'Export failed and some files or directories could not be restored. '
                f'{detail} Error: {error}') from error
        transaction['publication_outcome'] = 'rolled_back'
        raise


def dummy_folder_name(parent_mod_name, folder_name=''):
    if not folder_name:
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', parent_mod_name).strip(' .')
        folder_name = '00 ' + safe_name
    return validate_folder_name(folder_name)


def _plan_dummy_parent(parent, child_folder, metadata, folder_name, categories):
    name = _field_value(metadata.get('addonfor', ''))
    if not name:
        raise ValueError('Enter the parent mod name before creating its menu folder.')
    folder = dummy_folder_name(name, folder_name)
    if folder.casefold() == child_folder.casefold():
        raise ValueError('The dummy parent and mesh option must use different folders.')
    root = parent / folder
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError('The dummy parent destination must be a regular folder.')
    path = _contained_path(root, 'modinfo.ini')
    if path.is_file():
        info = read_modinfo(path)
        if info.get('name') != name or info.get('dummymod', '').lower() not in ('true', '1'):
            raise ValueError('The parent folder already belongs to a different mod. Choose another menu folder.')
        return None  # Reuse its exact name without overwriting its metadata.
    if root.exists() and any(root.iterdir()):
        raise ValueError('The parent folder is not empty and has no matching dummy modinfo.ini.')
    values = dict(name=name, dummymod='True', version=metadata.get('version', ''),
                  author=metadata.get('author', ''),
                  category=categories or metadata.get('category', ''))
    return Path(folder, 'modinfo.ini'), render_modinfo(values)


def plan_mod_folder(parent_directory, folder_name, asset, values, preview_path,
                    *, create_dummy_parent=False, parent_folder_name='', parent_categories=''):
    """Validate packaging and inspect existing content without creating files."""
    parent = Path(parent_directory).expanduser().resolve()
    if not parent.is_dir():
        raise ValueError('Choose an existing Parent Directory; the mod folder will be created inside it.')
    destination = parent / validate_folder_name(folder_name)
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ValueError('The mod destination must be a regular folder.')
    menu_only = str(values.get('dummymod', '')).lower() in ('true', '1')
    if asset is None and not menu_only:
        raise ValueError('Choose a mesh collection or export a menu-only dummy mod.')
    if menu_only and asset is not None:
        raise ValueError('A dummy menu cannot contain an exported mesh.')
    relative_paths = ([asset.relative_path] if asset else []) + [Path('modinfo.ini')]
    if menu_only and destination.exists():
        if (destination / 'natives').exists():
            raise ValueError('This folder contains game assets. Choose a separate folder for the dummy menu.')
        previous = destination / 'modinfo.ini'
        if previous.is_file() and read_modinfo(previous).get('dummymod', '').lower() not in ('true', '1'):
            raise ValueError('This folder belongs to a mesh mod. Choose a separate folder for the dummy menu.')
    for relative in relative_paths:
        _contained_path(destination, relative)
    metadata = dict(values)
    parent_name = _field_value(metadata.get('addonfor', ''))
    if parent_name and parent_name == _field_value(metadata.get('name', '')):
        raise ValueError('An add-on option must have a different display name from its parent mod.')
    dummy = (_plan_dummy_parent(parent, folder_name, metadata, parent_folder_name, parent_categories)
             if create_dummy_parent else None)
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
    for relative in relative_paths:
        target = _contained_path(destination, relative)
        if target.is_symlink():
            raise ValueError('A destination file is a symbolic link. Choose another Mod Folder Name.')
        if target.exists() and not target.is_file():
            raise ValueError('An export destination is not a file: ' + str(target))
    states = {str(destination / relative): _file_state(destination / relative)
              for relative in relative_paths}
    states[str(destination)] = _file_state(destination)
    if preview:
        states[str(preview)] = _file_state(preview)
    if create_dummy_parent:
        parent_ini = parent / dummy_folder_name(parent_name, parent_folder_name) / 'modinfo.ini'
        states[str(parent_ini)] = _file_state(parent_ini)
        states[str(parent_ini.parent)] = _file_state(parent_ini.parent)
    conflicts = destination_conflicts(parent, folder_name, asset) if asset else []
    snapshot = hashlib.sha256(json.dumps({'files': states, 'conflicts': conflicts},
                            sort_keys=True).encode('utf-8')).hexdigest()
    return dict(parent=parent, destination=destination, asset=asset, metadata=metadata,
                preview=preview, relative_paths=relative_paths, dummy=dummy, ini=ini,
                snapshot=snapshot, states=states, conflicts=conflicts)


def export_mod_folder(parent_directory, folder_name, asset, values, preview_path, export_mesh,
                      *, create_dummy_parent=False, parent_folder_name='', parent_categories='',
                      validate_staged=None, expected_snapshot=None, transaction_report=None):
    """Stage every payload; report untouched/rollback only when verified."""
    transaction = transaction_report if transaction_report is not None else {}
    transaction.update(publication_started=False, folder_unchanged=True,
                       rollback_verified=False, publication_outcome='not_started')
    planning = dict(create_dummy_parent=create_dummy_parent,
                    parent_folder_name=parent_folder_name, parent_categories=parent_categories)
    plan = plan_mod_folder(parent_directory, folder_name, asset, values, preview_path, **planning)
    if expected_snapshot is not None and expected_snapshot != plan['snapshot']:
        raise ValueError('UNIFIED_REQUEST_CHANGED')
    parent, destination, metadata = plan['parent'], plan['destination'], plan['metadata']
    preview, relative_paths, dummy, ini = plan['preview'], plan['relative_paths'], plan['dummy'], plan['ini']
    temporary = Path(tempfile.mkdtemp(prefix='.re-mesh-export-', dir=parent))
    keep_backup = False
    try:
        staged_root = temporary / 'mods'
        staged = staged_root / folder_name
        staged.mkdir(parents=True)
        if asset:
            staged_mesh = staged / asset.relative_path
            staged_mesh.parent.mkdir(parents=True)
            if not export_mesh(str(staged_mesh)) or not staged_mesh.is_file():
                raise ValueError('Mesh export failed; the mod folder was not updated.')
        (staged / 'modinfo.ini').write_text(ini, encoding='utf-8')
        if preview:
            shutil.copy2(preview, staged / metadata['screenshot'])
            if _file_state(preview) != plan['states'][str(preview)]:
                raise ValueError('UNIFIED_REQUEST_CHANGED')
            if _file_state(staged / metadata['screenshot']) != plan['states'][str(preview)]:
                raise OSError('The Preview Image could not be copied safely. Choose the image again and retry.')
        if dummy:
            dummy_path = staged_root / dummy[0]
            dummy_path.parent.mkdir(parents=True)
            dummy_path.write_text(dummy[1], encoding='utf-8')
        # Unified callers bind review to the logical destination rather than
        # this transaction's fresh temporary path. Recheck after every staged
        # file is ready, immediately before publishing any of them.
        if validate_staged is not None:
            validate_staged()
        if expected_snapshot is not None:
            current = plan_mod_folder(parent_directory, folder_name, asset, values, preview_path, **planning)
            if current['snapshot'] != expected_snapshot:
                raise ValueError('UNIFIED_REQUEST_CHANGED')
        if destination.exists() or dummy:
            publication = [Path(folder_name) / relative for relative in relative_paths]
            if dummy:
                publication.append(dummy[0])
            _publish_existing(staged_root, parent, publication, temporary / 'backup', transaction)
        else:
            transaction.update(publication_started=True, folder_unchanged=False)
            try:
                os.replace(staged, destination)
            except Exception:
                transaction.update(folder_unchanged=not destination.exists(),
                    publication_outcome='not_written' if not destination.exists() else 'unverified')
                raise
        transaction.update(publication_outcome='committed', folder_unchanged=False,
                           published_paths=[str(destination / path) for path in relative_paths] +
                               ([str(parent / dummy[0])] if dummy else []))
    except RecoveryRequiredError:
        keep_backup = True
        raise
    finally:
        if not keep_backup:
            try:
                shutil.rmtree(temporary)
            except OSError as error:
                # Cleanup failure does not undo a proven package commit.
                transaction['cleanup_warning'] = str(error)
                transaction['staging_directory'] = str(temporary)
    return destination / (asset.relative_path if asset else 'modinfo.ini')
