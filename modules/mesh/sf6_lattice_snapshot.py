"""Capture live fixed-grid Lattice edits without changing the source mode."""
import bpy


def copy_live_lattice(original, data, required=True):
    source = original.data
    editor = next((obj for obj in bpy.data.objects
                   if obj.type == 'LATTICE' and obj.data == source and obj.mode == 'EDIT'), original)
    dimensions = (source.points_u, source.points_v, source.points_w)
    coordinates = [point.co_deform.copy() for point in source.points]
    if (data.points_u, data.points_v, data.points_w) != dimensions:
        data.points_u, data.points_v, data.points_w = dimensions
    if len(data.points) != len(coordinates):
        if not required:
            return
        raise ValueError('UNIFIED_LATTICE_GRID_CHANGED: Could not capture the live lattice grid on "' + original.name + '". Its edits were not committed or discarded.')
    if data.shape_keys is not None:
        if data.shape_keys == source.shape_keys:
            raise ValueError('UNIFIED_LATTICE_KEYS_NOT_PRIVATE')
        data.shape_keys.use_fake_user = False
        keys = data.shape_keys.key_blocks
        index = editor.active_shape_key_index
        if not 0 <= index < len(keys) or len(keys[index].data) != len(coordinates):
            if not required:
                return
            raise ValueError('UNIFIED_LATTICE_KEY_GRID_CHANGED: Finish the lattice shape-key grid change on "' + original.name + '" before exporting. Its edits were not committed or discarded.')
        # Unlike Curve Edit, a Lattice has a fixed U/V/W point correspondence.
        # Blender commits only the edited key, including Basis. Do not offset
        # other relative keys: doing so would invent edits the user did not make.
        for point, coordinate in zip(keys[index].data, coordinates):
            point.co = coordinate
    for point, coordinate in zip(data.points, coordinates):
        point.co_deform = coordinate
    data.update_tag()
