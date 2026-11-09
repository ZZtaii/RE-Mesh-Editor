"""Read live authoring modes through private Object-mode datablocks.

Never switch an author's mode. Hidden sculpt/text/surface buffers use guarded
public synchronization; ordinary mesh/rig edits remain private and flush-free.
Mesh.copy() alone omits pending BMesh edits and Armature.copy() omits edit bones.
The isolated
scene also protects an unrelated active object from legacy writer operators.
Logical aliases keep review consent bound to source IDs, not disposable copies.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import bpy
import bmesh
import hashlib
import numpy as np

_CURRENT = ContextVar('sf6_live_mode_snapshot', default=None)


def logical_id(value):
    current = _CURRENT.get()
    return current.aliases.get(value.as_pointer(), value) if current and value is not None else value


def logical_name(value):
    return getattr(logical_id(value), 'name', None)


def logical_pointer(value):
    return logical_id(value).as_pointer() if value is not None else None


def current_snapshot():
    return _CURRENT.get()


def alias_copy(copy, original):
    snapshot=_CURRENT.get()
    if snapshot is not None:
        if copy is original:snapshot.aliases.pop(copy.as_pointer(),None)
        else:snapshot.aliases[copy.as_pointer()]=logical_id(original)


def current_selected_objects():
    if _CURRENT.get() is None:return tuple(bpy.context.selected_objects)
    layer=bpy.context.view_layer
    return tuple(obj for obj in layer.objects if obj.select_get(view_layer=layer))


@contextmanager
def private_object_context(obj):
    if _CURRENT.get() is None:
        yield
    else:
        with bpy.context.temp_override(object=obj,active_object=obj,
             selected_objects=[obj],selected_editable_objects=[obj],edit_object=obj,
             objects_in_mode=[obj],objects_in_mode_unique_data=[obj]):
            yield


def needs_snapshot(collection):
    if _CURRENT.get() is not None:
        return False
    if bpy.context.mode != 'OBJECT':
        return True
    # Dependencies can be edited in another Blender window even when the
    # invoking window is in Object Mode.
    return any(item.mode != 'OBJECT' or getattr(item.data, 'is_editmode', False)
               for item in _input_graph([collection]) if isinstance(item, bpy.types.Object))


def _properties(source, target):
    for key in source.keys():
        target[key] = source[key]


def _references(value):
    """ID dependencies in modifier/constraint/socket RNA, without mesh traversal."""
    for prop in value.bl_rna.properties:
        if prop.identifier == 'rna_type':
            continue
        try:
            item = getattr(value, prop.identifier)
        except (AttributeError, TypeError, RuntimeError):
            continue
        if prop.type == 'POINTER' and isinstance(item, (bpy.types.Object, bpy.types.Collection, bpy.types.NodeTree, bpy.types.Texture)):
            yield item
        elif prop.type == 'COLLECTION':
            for child in item:
                if not isinstance(child, bpy.types.ID):
                    yield from _references(child)
    try:
        for key in value.keys():
            item = value[key]
            if isinstance(item, (bpy.types.Object, bpy.types.Collection, bpy.types.NodeTree, bpy.types.Texture)):
                yield item
    except TypeError:
        pass


def _remap(value, mapping):
    for prop in value.bl_rna.properties:
        key = prop.identifier
        if key == 'rna_type':
            continue
        try:
            item = getattr(value, key)
        except (AttributeError, TypeError, RuntimeError):
            continue
        if prop.type == 'POINTER' and not prop.is_readonly and isinstance(item, bpy.types.ID) and item in mapping:
            setattr(value, key, mapping[item])
        elif prop.type == 'COLLECTION':
            for child in item:
                if not isinstance(child, bpy.types.ID):
                    _remap(child, mapping)
    try:
        for key in tuple(value.keys()):
            item = value[key]
            if isinstance(item, bpy.types.ID) and item in mapping:
                value[key] = mapping[item]
    except TypeError:
        pass


def _object_inputs(obj):
    if obj.parent is not None:
        yield obj.parent
    if obj.instance_collection is not None:
        yield obj.instance_collection
    if obj.type in ('CURVE', 'SURFACE', 'FONT'):
        yield from _references(obj.data)
    for item in list(obj.modifiers) + list(obj.constraints):
        yield from _references(item)
    if obj.pose:
        for bone in obj.pose.bones:
            for constraint in bone.constraints:
                yield from _references(constraint)
    from .sf6_driver_snapshot import driver_dependencies
    for owner in (obj, obj.data, getattr(obj.data, 'shape_keys', None)):
        yield from driver_dependencies(owner)


def _armature_edit_records(data):
    records = []
    for bone in data.edit_bones:
        values = {}
        for prop in bone.bl_rna.properties:
            key = prop.identifier
            # These are derived from head/tail/roll. Reassigning them as RNA
            # arrays can transpose the matrix or move already authored ends.
            if key in ('rna_type', 'name', 'parent', 'matrix', 'length') or prop.is_readonly:
                continue
            if prop.type in ('BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'):
                value = getattr(bone, key)
                values[key] = list(value) if getattr(prop, 'is_array', False) else value
            elif key in ('bbone_custom_handle_start', 'bbone_custom_handle_end'):
                value = getattr(bone, key)
                values[key] = value.name if value is not None else None
        records.append((bone.name, bone.parent.name if bone.parent else None,
                        values, {key: bone[key] for key in bone.keys()},
                        [group.name for group in bone.collections]))
    return records


def curve_records(data):
    """Read spline RNA, which exposes the live Edit Curve buffer in Blender."""
    def properties(value, excluded=()):
        result = {}
        for prop in value.bl_rna.properties:
            key = prop.identifier
            if key in ('rna_type', 'type', *excluded) or prop.is_readonly:
                continue
            if prop.type in ('BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'):
                item = getattr(value, key)
                result[key] = tuple(item) if getattr(prop, 'is_array', False) else item
        return result
    result = []
    for spline in data.splines:
        points = spline.bezier_points if spline.type == 'BEZIER' else spline.points
        result.append(dict(type=spline.type, count_v=spline.point_count_v,
                           properties=properties(spline, ('hide',)),
                           points=[properties(point, ('select', 'select_control_point',
                                                      'select_left_handle', 'select_right_handle', 'hide'))
                                   for point in points]))
    return result


def _live_curve_data(original, data, required):
    """Reconstruct a private curve without committing the original edit buffer."""
    records = curve_records(original.data)
    if not required:
        # An unrelated active curve is only copied to isolate operator context.
        # It does not contribute geometry to this export.
        return
    if original.data.shape_keys is not None:
        # Equal live coordinates/counts do not prove unchanged keyed topology:
        # delete+subdivide can recreate identical coordinates but a different
        # hidden point-to-KeyBlock mapping. Blender exposes no copy of that map.
        raise ValueError('UNIFIED_CURVE_SHAPE_KEY_EDIT_UNSUPPORTED: Finish editing the shape-keyed curve dependency "' + original.name + '" before review/export. Blender does not expose its pending point-to-shape-key mapping; the live edits were not committed or discarded.')
    if records == curve_records(data):
        return
    shape = lambda rows: [(row['type'], row['count_v'], len(row['points'])) for row in rows]
    rebuild = shape(records) != shape(curve_records(data))
    if rebuild and (original.type == 'SURFACE' or any(record['count_v'] > 1 for record in records)):
        raise ValueError('UNIFIED_SURFACE_TOPOLOGY_EDIT_UNSUPPORTED: Finish editing the surface dependency "' + original.name + '" before review/export. Its live surface topology was not committed or discarded.')
    _apply_curve_records(data, records, rebuild=rebuild)


def _apply_curve_records(data, records, *, rebuild):
    def assign(value, key, item):
        current = getattr(value, key)
        if isinstance(item, tuple):
            current = tuple(current)
        # Blender stores some legacy defaults outside their setter's range.
        # Do not clamp a retained zero soft-body weight or unused NURBS order.
        if current != item:
            setattr(value, key, item)
    if rebuild:
        data.splines.clear()
    for index, record in enumerate(records):
        spline = data.splines.new(record['type']) if rebuild else data.splines[index]
        points = spline.bezier_points if spline.type == 'BEZIER' else spline.points
        if rebuild and record['points']:
            points.add(len(record['points']) - 1)
        for point, values in zip(points, record['points']):
            if spline.type == 'BEZIER':
                point.handle_left_type = 'FREE'
                point.handle_right_type = 'FREE'
            for key, value in values.items():
                if key not in ('co', 'handle_left', 'handle_right', 'handle_left_type', 'handle_right_type'):
                    assign(point, key, value)
            point.co = values['co']
        for point, values in zip(points, record['points']):
            if spline.type == 'BEZIER':
                point.handle_left = values['handle_left']
                point.handle_right = values['handle_right']
                point.handle_left_type = values['handle_left_type']
                point.handle_right_type = values['handle_right_type']
        for key, value in record['properties'].items():
            assign(spline, key, value)
    data.update_tag()


def _input_graph(initial):
    pending = list(initial)
    result = []
    seen = set()
    while pending:
        item = pending.pop()
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
        if isinstance(item, bpy.types.Collection):
            pending.extend(item.children)
            pending.extend(item.objects)
        elif isinstance(item, bpy.types.Object):
            pending.extend(_object_inputs(item))
        else:
            pending.extend(_references(item))
            from .sf6_driver_snapshot import driver_dependencies
            pending.extend(driver_dependencies(item))
    return result


def _mesh_digest(mesh):
    """Exact authored mesh comparison, excluding selection-only edit layers."""
    digest=hashlib.sha256()
    def put(value):digest.update(repr(value).encode());digest.update(b'\0')
    def values(items,key,size=1,dtype=np.float32):
        data=np.empty(len(items)*size,dtype);items.foreach_get(key,data);digest.update(data.tobytes())
    values(mesh.vertices,'co',3)
    values(mesh.edges,'vertices',2,np.int32)
    put([(tuple(p.vertices),p.material_index,p.use_smooth) for p in mesh.polygons])
    put([[(g.group,g.weight) for g in v.groups] for v in mesh.vertices])
    for attribute in sorted(mesh.attributes,key=lambda a:a.name):
        if attribute.name.startswith(('.select_', '.hide_', '.vs.', '.es.', '.pn.')):continue
        put((attribute.name,attribute.domain,attribute.data_type,len(attribute.data)))
        if not len(attribute.data):continue
        for prop in attribute.data[0].bl_rna.properties:
            if prop.identifier=='rna_type':continue
            if prop.type in ('FLOAT','INT','BOOLEAN'):
                values(attribute.data,prop.identifier,max(1,getattr(prop,'array_length',0)),
                       np.float32 if prop.type=='FLOAT' else np.bool_ if prop.type=='BOOLEAN' else np.int32)
            else:put([getattr(item,prop.identifier) for item in attribute.data])
    if mesh.shape_keys:
        for key in mesh.shape_keys.key_blocks:
            put(key.name);values(key.data,'co',3)
    return digest.digest()


def _live_mesh_data(original, data):
    live=bmesh.from_edit_mesh(original.data).copy()
    try:live.to_mesh(data);data.update()
    finally:live.free()
    # A no-edit BMesh round trip can change normal packing / internal layers.
    # Compare like with like so entering Edit Mode alone does not drop LODs.
    # Only exact equality of every authored channel permits the stale RNA copy.
    reference=original.data.copy()
    if reference.shape_keys is not None:
        reference.shape_keys.use_fake_user=False
    control=bmesh.new()
    try:
        control.from_mesh(original.data,use_shape_key=bool(original.data.shape_keys),
                          shape_key_index=original.active_shape_key_index)
        control.to_mesh(reference);reference.update()
        unchanged=_mesh_digest(data)==_mesh_digest(reference)
    finally:
        control.free();bpy.data.meshes.remove(reference)
    if unchanged:
        bpy.data.meshes.remove(data)
        return original.data.copy()
    # Preserve existing selection-only attributes exactly and discard newly
    # generated ones. They are UI state, not mesh export inputs.
    for attr in tuple(data.attributes):
        if attr.name.startswith(('.select_', '.hide_', '.vs.', '.es.', '.pn.')):
            old=original.data.attributes.get(attr.name)
            if old is None:data.attributes.remove(attr)
            elif len(old.data)==len(attr.data):
                for prop in old.data[0].bl_rna.properties if len(old.data) else ():
                    if prop.type=='BOOLEAN':
                        values=np.empty(len(old.data),np.bool_);old.data.foreach_get(prop.identifier,values);attr.data.foreach_set(prop.identifier,values)
    return data


class ModeSnapshot:
    def __init__(self, collection):
        self.collection = collection
        self.source_context = bpy.context.copy()
        self.source_scene = bpy.context.scene
        self.source_layer = bpy.context.view_layer
        self.source_active = self.source_layer.objects.active
        self.source_selected = tuple(bpy.context.selected_objects)
        self.aliases = {}
        self.driver_origins = {}
        self.mapping = {}
        self.owned = []
        self.scene = None

    def _own(self, original, copy):
        self.owned.append(copy)
        copy.use_fake_user = False
        copied_keys = getattr(copy, 'shape_keys', None)
        if copied_keys is not None and copied_keys != getattr(original, 'shape_keys', None):
            copied_keys.use_fake_user = False
            source_keys=getattr(original, 'shape_keys', None)
            if source_keys is not None:
                self.mapping[source_keys]=copied_keys
                self.aliases[copied_keys.as_pointer()]=source_keys
        self.mapping[original] = copy
        self.aliases[copy.as_pointer()] = original
        return copy

    def build(self):
        # Update transforms before dependency discovery. Hidden editor buffers
        # are synchronized separately after the required-data safety preflight.
        self.source_layer.update()
        pending = [self.collection]
        if self.source_scene.camera is not None:
            pending.append(self.source_scene.camera)
        # Keep selected/active context valid even when editing outside the export.
        pending.extend(self.source_selected)
        if self.source_active is not None:
            pending.append(self.source_active)
        inputs = _input_graph(pending)
        # A curve/text sitting in the collection is not itself exported by the
        # mesh writer. Only actual mesh/rig/helper dependency graphs require it.
        required_inputs = set(_input_graph([item for item in self.collection.all_objects
                                           if item.type in ('MESH', 'ARMATURE') or item.get('~TYPE')]))
        required_data = {item.data for item in required_inputs
                         if isinstance(item, bpy.types.Object) and item.data is not None}
        # Preflight and public synchronization happen before temporary IDs.
        # Most modes use private data copies and never reach the global flush.
        from .sf6_editor_sync import synchronize_hidden_editors
        self.editor_sync = synchronize_hidden_editors(required_inputs)
        self.scene = bpy.data.scenes.new('__SF6_MODE_SNAPSHOT')
        self.owned.append(self.scene)
        self.scene.frame_current = self.source_scene.frame_current
        self.scene.frame_subframe = self.source_scene.frame_subframe
        self.scene.render.fps = self.source_scene.render.fps
        self.scene.render.fps_base = self.source_scene.render.fps_base
        self.mapping[self.source_scene]=self.scene
        self.aliases[self.scene.as_pointer()]=self.source_scene
        _properties(self.source_scene,self.scene)
        for source,target in ((self.source_scene.render,self.scene.render),(self.source_scene.unit_settings,self.scene.unit_settings)):
            for prop in source.bl_rna.properties:
                key=prop.identifier
                if prop.is_readonly or key=='rna_type':continue
                if prop.type in ('BOOLEAN','INT','FLOAT','STRING','ENUM'):
                    value=getattr(source,key)
                    try:setattr(target,key,list(value) if getattr(prop,'is_array',False) else value)
                    except (AttributeError,TypeError):pass
        self.layer = self.scene.view_layers[0]
        armatures = []
        for original in inputs:
            if isinstance(original, bpy.types.Collection):
                copied = self._own(original, bpy.data.collections.new(original.name))
                _properties(original, copied)
                copied.hide_viewport = original.hide_viewport
                copied.hide_render = original.hide_render
            else:
                copied = self._own(original, original.copy())
            if not isinstance(original, bpy.types.Object) or original.data is None:
                continue
            if original.data not in self.mapping:
                data = self._own(original.data, original.data.copy())
                if original.type == 'MESH':
                    if original.data.is_editmode:
                        old_data=data
                        old_pointers=[data.as_pointer()]
                        if data.shape_keys:old_pointers.append(data.shape_keys.as_pointer())
                        editor = next((item for item in inputs
                                       if isinstance(item, bpy.types.Object) and item.type == 'MESH'
                                       and item.data == original.data and item.mode == 'EDIT'), original)
                        data=_live_mesh_data(editor,data)
                        if data is not old_data:
                            for pointer in old_pointers:self.aliases.pop(pointer,None)
                            self.owned = [item for item in self.owned if item is not old_data]
                            self._own(original.data,data)
                    if original.data.shape_keys:
                        self.aliases[data.shape_keys.as_pointer()] = original.data.shape_keys
                elif original.type == 'ARMATURE' and original.data.is_editmode:
                    armatures.append((copied, _armature_edit_records(original.data)))
                elif original.type in ('CURVE', 'SURFACE') and original.data.is_editmode:
                    _live_curve_data(original, data, original.data in required_data)
                elif original.type == 'LATTICE' and original.data.is_editmode:
                    from .sf6_lattice_snapshot import copy_live_lattice
                    copy_live_lattice(original, data, required=original.data in required_data)
                elif (original.type == 'FONT' and original.mode == 'EDIT' and original.data in required_data
                      and original.data.as_pointer() not in self.editor_sync['data_pointers']):
                    raise ValueError('UNIFIED_FONT_EDIT_UNSUPPORTED: Finish editing the text dependency "' + original.name + '" before review/export. Blender does not expose its pending text through a private data copy; the text was not committed or discarded.')
                if original.type in ('CURVE', 'SURFACE', 'LATTICE') and original.data.shape_keys:
                    self.aliases[data.shape_keys.as_pointer()] = original.data.shape_keys
            copied.data = self.mapping[original.data]
        child_collections = {child for original in inputs if isinstance(original, bpy.types.Collection) for child in original.children}
        for original in inputs:
            copied = self.mapping[original]
            if isinstance(original, bpy.types.Collection):
                for child in original.children:
                    copied.children.link(self.mapping[child])
                for obj in original.objects:
                    copied.objects.link(self.mapping[obj])
                # Link every dependency collection into the evaluation scene.
                if original not in child_collections:
                    self.scene.collection.children.link(copied)
            elif isinstance(original, bpy.types.Object):
                if not copied.users_collection:
                    self.scene.collection.objects.link(copied)
                if original.parent in self.mapping:
                    copied.parent = self.mapping[original.parent]
                if original.instance_collection in self.mapping:
                    copied.instance_collection = self.mapping[original.instance_collection]
                if original.type in ('CURVE', 'SURFACE', 'FONT'):
                    _remap(copied.data, self.mapping)
                for item in list(copied.modifiers) + list(copied.constraints):
                    _remap(item, self.mapping)
                if copied.pose:
                    for bone in copied.pose.bones:
                        for constraint in bone.constraints:
                            _remap(constraint, self.mapping)
                from .sf6_driver_snapshot import remap_drivers
                owners = ((original, copied), (original.data, copied.data),
                          (getattr(original.data, 'shape_keys', None), getattr(copied.data, 'shape_keys', None)))
                for source_owner, copied_owner in owners:
                    remap_drivers(source_owner, copied_owner, self.mapping, self.driver_origins)

            else:
                _remap(copied, self.mapping)
                from .sf6_driver_snapshot import remap_drivers
                remap_drivers(original, copied, self.mapping, self.driver_origins)
        self.scene.camera = self.mapping.get(self.source_scene.camera)
        self.scene.world = self.source_scene.world
        self.layer.update()
        for obj, records in armatures:
            self._fill_armature(obj, records)
        for obj in self.scene.objects:
            obj.select_set(False, view_layer=self.layer)
        for original in self.source_selected:
            self.mapping[original].select_set(True, view_layer=self.layer)
        self.layer.objects.active = self.mapping.get(self.source_active)
        self.layer.update()
        return self.mapping[self.collection]

    def override(self, active=None, selected=None):
        active = self.layer.objects.active if active is None else active
        values = dict(scene=self.scene, view_layer=self.layer, object=active,
                      active_object=active, edit_object=None, sculpt_object=None,
                      vertex_paint_object=None, weight_paint_object=None,
                      image_paint_object=None, pose_object=None,
                      objects_in_mode=[], objects_in_mode_unique_data=[])
        if selected is None:
            selected = [obj for obj in self.layer.objects if obj.select_get(view_layer=self.layer)]
        values.update(selected_objects=selected, selected_editable_objects=selected)
        return bpy.context.temp_override(**values)

    def _fill_armature(self, obj, records):
        source = self.aliases[obj.as_pointer()]
        # Assigning copied rest data can rebuild pose channels using stale bone
        # names while the source is still editing. Restore complete channels
        # from the source after the private rest-bone reconstruction.
        pose_basis = {bone.name: bone.matrix_basis.copy() for bone in source.pose.bones}
        hidden = obj.hide_viewport
        no_pending_edits = False

        def canonical(value):
            if hasattr(value, 'to_dict'):
                return canonical(value.to_dict())
            if hasattr(value, 'to_list'):
                return canonical(value.to_list())
            if isinstance(value, dict):
                return {key: canonical(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [canonical(item) for item in value]
            if isinstance(value, set):
                return sorted(value)
            return value

        def authored(rows):
            ui_only = {'select', 'select_head', 'select_tail', 'hide', 'hide_select', 'lock', 'show_wire'}
            return canonical([(name, parent, {key: value for key, value in values.items() if key not in ui_only},
                               custom, sorted(collections))
                              for name, parent, values, custom, collections in rows])

        def copy_rna(source_value, target_value, excluded=()):
            for prop in source_value.bl_rna.properties:
                key = prop.identifier
                if key in ('rna_type', 'name', 'type', *excluded) or prop.is_readonly or prop.type == 'COLLECTION':
                    continue
                value = getattr(source_value, key)
                if prop.type == 'POINTER':
                    if isinstance(value, bpy.types.ID):
                        value = self.mapping.get(value, value)
                    elif isinstance(value, bpy.types.PoseBone):
                        value = obj.pose.bones.get(value.name)
                elif getattr(prop, 'is_array', False):
                    value = value.copy() if hasattr(value, 'copy') else list(value)
                setattr(target_value, key, value)

        self.layer.objects.active = obj
        obj.hide_viewport = False
        obj.hide_set(False, view_layer=self.layer)
        obj.select_set(True, view_layer=self.layer)
        with self.override(active=obj, selected=[obj]):
            bpy.ops.object.mode_set(mode='EDIT')
            try:
                bones = obj.data.edit_bones
                no_pending_edits = authored(_armature_edit_records(obj.data)) == authored(records)
                desired = {name for name, *_ in records}
                for bone in tuple(bones):
                    if bone.name not in desired:
                        bones.remove(bone)
                for name, _, _, _, _ in records:
                    if bones.get(name) is None:
                        bones.new(name)
                for name, parent, values, custom, collections in records:
                    bone = bones[name]
                    # Connection and derived geometry setters can constrain
                    # heads. Apply metadata first, exact authored ends last.
                    bone.use_connect = False
                    bone.parent = bones.get(parent) if parent else None
                    for key, value in values.items():
                        if key in ('head', 'tail', 'roll', 'use_connect'):
                            continue
                        if key in ('bbone_custom_handle_start', 'bbone_custom_handle_end'):
                            value = bones.get(value) if value is not None else None
                        setattr(bone, key, value)
                    bone.head = values['head']
                    bone.tail = values['tail']
                    bone.roll = values['roll']
                    bone.use_connect = values['use_connect']
                    for key in tuple(bone.keys()):
                        del bone[key]
                    for key, value in custom.items():
                        bone[key] = value
                    for group in tuple(bone.collections):
                        group.unassign(bone)
                    for group_name in collections:
                        group = obj.data.collections.get(group_name)
                        if group is not None:
                            group.assign(bone)
            finally:
                bpy.ops.object.mode_set(mode='OBJECT')
        if no_pending_edits:
            # Entering/leaving Edit Mode itself rounds rest matrices. If the
            # copied edit representation exactly equals the source edit buffer,
            # retain the pristine stored rest data instead of inventing edits.
            previous = obj.data
            pristine = self._own(source.data, source.data.copy())
            for copied in self.mapping.values():
                if isinstance(copied, bpy.types.Object) and copied.data == previous:
                    copied.data = pristine
        for source_bone in source.pose.bones:
            bone = obj.pose.bones.get(source_bone.name)
            if bone is None:
                continue
            copy_rna(source_bone, bone, excluded=('matrix', 'matrix_basis'))
            for key in tuple(bone.keys()):
                del bone[key]
            _properties(source_bone, bone)
            for constraint in tuple(bone.constraints):
                bone.constraints.remove(constraint)
            for source_constraint in source_bone.constraints:
                constraint = bone.constraints.new(source_constraint.type)
                constraint.name = source_constraint.name
                copy_rna(source_constraint, constraint)
                if source_constraint.type == 'ARMATURE':
                    for source_target in source_constraint.targets:
                        target = constraint.targets.new()
                        copy_rna(source_target, target)
                _remap(constraint, self.mapping)
            bone.matrix_basis = pose_basis[source_bone.name]
        obj.hide_viewport = hidden
        self.layer.update()

    @contextmanager
    def original_context(self):
        token = _CURRENT.set(None)
        try:
            with bpy.context.temp_override(**self.source_context):
                yield
        finally:
            _CURRENT.reset(token)

    def close(self):
        # Copies may still reference each other; batch removal severs them all.
        live = []
        for item in self.owned:
            try:
                item.as_pointer()
                live.append(item)
            except ReferenceError:
                pass
        if live:
            bpy.data.batch_remove(ids=live)


@contextmanager
def export_snapshot(collection):
    snapshot = ModeSnapshot(collection)
    token = None
    try:
        target = snapshot.build()
        token = _CURRENT.set(snapshot)
        with snapshot.override():
            yield target, snapshot
    finally:
        if token is not None:
            _CURRENT.reset(token)
        snapshot.close()
