"""User-fixture regression for persistence of source metadata and exact export."""
import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
import addon_utils
import bpy

args_parser = argparse.ArgumentParser()
args_parser.add_argument('--source-file', type=Path, required=True)
args_parser.add_argument('--report-json', type=Path)
args = args_parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo.parent))
addon_utils.enable(repo.name, default_set=True)
mesh_io = importlib.import_module(repo.name + '.modules.mesh.blender_re_mesh')
options_module = importlib.import_module(repo.name + '.tests.test_sf6_source_preservation')
source = args.source_file.resolve()
with tempfile.TemporaryDirectory(prefix='sf6-save-reopen-') as td:
    root = Path(td)
    mesh_io.importREMeshFile(str(source), options_module.IMPORT_OPTIONS.copy())
    name = bpy.context.scene['REMeshLastImportedCollection']
    bpy.ops.wm.save_as_mainfile(filepath=str(root / 'source.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(root / 'source.blend'), load_ui=False)
    out = root / source.name
    assert mesh_io.exportREMeshFile(str(out), dict(targetCollection=name, rotate90=True, exportBlendShapes=True))
    assert out.read_bytes() == source.read_bytes()
    print('PASS saved and reopened Blender source exports byte-identically')
    if args.report_json:
        args.report_json.write_text(json.dumps({'source': source.name, 'byte_identical': True, 'bytes': out.stat().st_size}, indent=2))
