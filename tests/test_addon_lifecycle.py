"""Blender regression: disabling removes registered RNA properties; reload is clean."""
import importlib
from pathlib import Path
import sys
import bpy

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo.parent))
addon = importlib.import_module(repo.name)
properties = ((bpy.types.WindowManager, 'enableModFileTracking'),
              (bpy.types.Scene, 're_mdf_toolpanel'),
              (bpy.types.Scene, 're_modworkspace_toolpanel'),
              (bpy.types.Object, 're_mdf_material'),
              (bpy.types.Object, 're_sfur_data'))
for cycle in range(3):
    addon.register()
    assert all(hasattr(owner, key) for owner, key in properties)
    addon.unregister()
    leaked = [key for owner, key in properties if hasattr(owner, key)]
    assert not leaked, f'RNA properties survive addon disable: {leaked}'
    print(f'PASS clean register/unregister cycle {cycle + 1}')
print('ADDON_LIFECYCLE_PASSED')
