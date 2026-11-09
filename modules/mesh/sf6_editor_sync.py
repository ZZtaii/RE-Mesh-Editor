"""Synchronize only hidden editor buffers needed by the requested export.

Blender's public flush operator is global. Never call it while any Mesh,
Curve or Surface with shape keys, or any Armature, is being edited. Flush/continuation
can corrupt relative key deltas in those editors. Text and non-keyed Surface buffers use object-scoped synchronization instead.
Ordinary mesh/rig/paint snapshots do not need the global operator and must remain independent of it.
"""
import hashlib
import json
import bpy
import numpy as np


def unsafe_global_editors():
    by_data = {}
    for obj in bpy.data.objects:
        data = obj.data
        if (data is not None and getattr(data, 'is_editmode', False) and
                (obj.type == 'ARMATURE' or
                 (obj.type in ('MESH', 'CURVE', 'SURFACE') and data.shape_keys is not None))):
            by_data.setdefault(data, []).append(obj)
    names = []
    for objects in by_data.values():
        editors = [obj for obj in objects if obj.mode == 'EDIT'] or objects
        names.extend(obj.name for obj in editors)
    return sorted(set(names))


def hidden_editor_sources(required_objects):
    data = {obj.data for obj in required_objects
            if isinstance(obj, bpy.types.Object) and obj.data is not None}
    result = []
    # A different object can own the active editor for shared source data.
    for obj in bpy.data.objects:
        if obj.data not in data:
            continue
        if obj.type == 'MESH' and obj.mode == 'SCULPT':
            if obj.use_dynamic_topology_sculpting:
                result.append((obj, 'dynamic_topology'))
            elif any(mod.type == 'MULTIRES' and mod.show_viewport and
                     mod.total_levels > 0 and mod.sculpt_levels > 0
                     for mod in obj.modifiers):
                result.append((obj, 'multires_sculpt'))
        elif obj.mode == 'EDIT' and getattr(obj.data, 'is_editmode', False):
            if obj.type == 'FONT':result.append((obj, 'text_edit'))
            elif obj.type == 'SURFACE' and obj.data.shape_keys is None:
                result.append((obj, 'surface_edit'))
    return result


def synchronize_hidden_editors(required_objects):
    required_objects = tuple(required_objects)
    needed = hidden_editor_sources(required_objects)
    sculpt = [(obj, reason) for obj, reason in needed if reason in ('dynamic_topology','multires_sculpt')]
    # Test the global interlock before even scoped synchronization when this
    # export also needs sculpt buffers, so a refused export changes nothing.
    unsafe = unsafe_global_editors() if sculpt else []
    if unsafe:
        raise ValueError('UNIFIED_EDITOR_SYNC_UNSAFE_EDITORS: To capture the current sculpt safely, first finish the shape-key or armature edits on these objects: ' + ', '.join(json.dumps(name) for name in unsafe) + '. No editor synchronization or export was performed.')
    # Reject uncopyable keyed curve mappings before touching any independent
    # hidden buffer. Equal visible points cannot prove the hidden key mapping
    # stayed unchanged after edit topology operations.
    keyed_curves = sorted({obj.name for obj in required_objects
                           if isinstance(obj, bpy.types.Object) and
                           obj.type in ('CURVE', 'SURFACE') and
                           getattr(obj.data, 'is_editmode', False) and
                           obj.data.shape_keys is not None})
    if keyed_curves:
        raise ValueError('UNIFIED_CURVE_SHAPE_KEY_EDIT_UNSUPPORTED: Finish editing the shape-keyed curve dependencies ' + ', '.join(json.dumps(name) for name in keyed_curves) + ' before review/export. Blender does not expose their pending point-to-shape-key mappings; no editor synchronization or export was performed.')
    if not needed:
        return {'performed': False, 'data_pointers': (), 'sources': ()}
    mode = bpy.context.mode
    layer = bpy.context.view_layer
    active = layer.objects.active
    selected = {obj.as_pointer() for obj in bpy.context.selected_objects}
    for obj, reason in needed:
        if reason in ('text_edit','surface_edit') and not obj.update_from_editmode():
            raise ValueError('UNIFIED_EDITOR_SYNC_FAILED: Blender did not synchronize the pending text or surface edits on "' + obj.name + '". No export was performed.')
    if sculpt:
        if not bpy.ops.ed.flush_edits.poll():
            raise ValueError('UNIFIED_EDITOR_SYNC_UNAVAILABLE: Blender cannot synchronize the current sculpt from this context. No export was performed.')
        if bpy.ops.ed.flush_edits() != {'FINISHED'}:
            raise ValueError('UNIFIED_EDITOR_SYNC_FAILED: Blender did not finish synchronizing the current editor data. No export was performed.')
    if (bpy.context.mode != mode or layer.objects.active != active or
            {obj.as_pointer() for obj in bpy.context.selected_objects} != selected):
        raise ValueError('UNIFIED_EDITOR_SYNC_CONTEXT_CHANGED: Blender changed the active editing context while synchronizing. Export was stopped.')
    layer.update()
    return {'performed': True,
            'data_pointers': tuple({obj.data.as_pointer() for obj, _ in needed}),
            'sources': tuple((obj.name, reason) for obj, reason in needed)}


def evaluated_multires_digest(obj):
    """Bind opaque displacement grids through their actual export geometry.

    This belongs in the session plan only. Extending the stored authoring-state
    fingerprint would invalidate existing verified import baselines.
    """
    if obj.type != 'MESH' or not any(mod.type == 'MULTIRES' and mod.show_viewport
                                    and mod.total_levels > 0 for mod in obj.modifiers):
        return None
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
    try:
        digest = hashlib.sha256()
        def put(value):
            digest.update(json.dumps(value, sort_keys=True).encode());digest.update(b'\0')
        def values(items, name, size=1, dtype=np.float32):
            data=np.empty(len(items)*size,dtype);items.foreach_get(name,data)
            digest.update(data.tobytes())
        put((len(mesh.vertices),len(mesh.edges),len(mesh.loops),len(mesh.polygons)))
        values(mesh.vertices,'co',3)
        values(mesh.edges,'vertices',2,np.int32)
        values(mesh.loops,'vertex_index',1,np.int32)
        values(mesh.polygons,'loop_start',1,np.int32)
        values(mesh.polygons,'loop_total',1,np.int32)
        values(mesh.polygons,'material_index',1,np.int32)
        values(mesh.polygons,'use_smooth',1,np.bool_)
        if len(mesh.corner_normals):values(mesh.corner_normals,'vector',3)
        for layer in mesh.uv_layers:
            put(layer.name);values(layer.data,'uv',2)
        for layer in mesh.color_attributes:
            put((layer.name,layer.domain,layer.data_type));values(layer.data,'color',4)
        return digest.hexdigest()
    finally:
        evaluated.to_mesh_clear()
