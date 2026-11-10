"""A valid mesh file imports whatever it is called and whichever import options are on."""
import argparse
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sf6_hybrid_export import IMPORT_OPTIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', type=Path, required=True,
                        help='Any retail SF6 mesh, for example esf032_001_02.mesh.230110883')
    parser.add_argument('--report-json', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    io = importlib.import_module(repo.name + '.modules.mesh.blender_re_mesh')
    files = importlib.import_module(repo.name + '.modules.mesh.file_re_mesh')
    raw = args.source_file.read_bytes()
    stem = args.source_file.name.split('.mesh')[0]
    report = []

    def attempt(label, path, source_mode, **overrides):
        options = IMPORT_OPTIONS.copy()
        options.update(overrides)
        options.setdefault('clearScene', True)
        warnings, errors = io.importREMeshFile(str(path), options)
        collection = bpy.data.collections[bpy.context.scene['REMeshLastImportedCollection']]
        vertices = sum(len(o.data.vertices) for o in collection.all_objects if o.type == 'MESH')
        assert vertices == expected_vertices[0] or expected_vertices[0] is None, (label, vertices)
        expected_vertices[0] = vertices
        assert not errors, (label, errors)
        assert bool(collection.get('SF6PreserveSource')) == source_mode, label
        # A fallback must say so; a normal import must stay quiet about it.
        assert any(w.startswith('SF6 shape keys') for w in warnings) == (
            not source_mode and options.get('importBlendShapes', True)), (label, warnings)
        assert files.detectMeshFileVersion(str(path)) == 230110883, label
        report.append(dict(test=label, passed=True))
        print('PASS ' + label, flush=True)

    expected_vertices = [None]
    with tempfile.TemporaryDirectory(prefix='any-name-') as temporary:
        root = Path(temporary)

        def copy(name, data=raw):
            target = root / name
            target.write_bytes(data)
            return target

        attempt('retail name', copy(stem + '.mesh.230110883'), True)
        attempt('unrelated base name', copy('my body (final) v2.mesh.230110883'), True)
        attempt('no version suffix', copy(stem + '.mesh'), True)
        attempt('uppercase extension', copy(stem.upper() + '.MESH.230110883'), True)
        attempt('extra suffix after the version', copy(stem + '.mesh.230110883.bak'), True)
        attempt('another game version in the name', copy(stem + '.mesh.221108797'), True)
        attempt('no mesh marker in the name', copy(stem + '.bin'), True)
        # Material and shell fur lookups read the version from the name too. They
        # are on by default in the import dialog and must not stop an odd name.
        resources = dict(loadMaterials=True, loadMDFData=True, loadShellFur=True)
        attempt('retail name with materials on', copy(stem + '.mesh.230110883'), True, **resources)
        attempt('uppercase extension with materials on', copy(stem.upper() + '.MESH.230110883'), True, **resources)
        attempt('no mesh marker with materials on', copy(stem + '.bin'), True, **resources)
        attempt('no version suffix with materials on', copy(stem + '.mesh'), True, **resources)
        attempt('trailing bytes after the mesh', copy('padded.mesh.230110883', raw + b'\0' * 16), False)
        attempt('merge groups on', copy('merged.mesh.230110883'), False, mergeGroups=True)
        attempt('blend shapes off', copy('plain.mesh.230110883'), False, importBlendShapes=False)
        attempt('first of two', copy('first.mesh.230110883'), True)
        armature = next(iter(bpy.data.armatures.keys()))
        attempt('merge armature into the first import', copy('second.mesh.230110883'), False,
                clearScene=False, mergeArmature=armature)

        # A file that is not a mesh at all still fails, with the importer's own message.
        try:
            io.importREMeshFile(str(copy('not-a-mesh.mesh.230110883', b'\0' * 4096)), IMPORT_OPTIONS.copy())
        except Exception as error:
            assert 'mesh' in str(error).lower(), error
            report.append(dict(test='non-mesh data is still rejected', passed=True))
            print('PASS non-mesh data is still rejected', flush=True)
        else:
            raise AssertionError('Non-mesh data imported')

    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2) + '\n')
    print('MESH_IMPORT_ANY_NAME_PASSED', flush=True)


if __name__ == '__main__':
    main()
