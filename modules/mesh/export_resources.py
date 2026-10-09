"""Own only temporary IDs explicitly allocated by the ordinary exporter.

Cleanup runs on success and exceptions. Never select disposable data by name:
a user may already own a collection named clonedMeshes or an orphan mesh.
"""
from functools import wraps
import bpy

_scopes = []

class ExportResources:
    def __init__(self):
        self.view_layer = bpy.context.view_layer
        self.active = self.view_layer.objects.active
        self.selected = tuple(bpy.context.selected_objects)
        self.mode = self.active.mode if self.active is not None else None
        self.objects = []
        self.meshes = []
        self.armatures = []
        self.collection = None

    def cleanup(self):
        for obj in reversed(self.objects):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except ReferenceError:
                pass
        if self.collection is not None:
            bpy.data.collections.remove(self.collection)
        for values, registry in ((self.meshes, bpy.data.meshes),
                                 (self.armatures, bpy.data.armatures)):
            for data in reversed(values):
                try:
                    data.use_fake_user = False
                    if data.users == 0:
                        registry.remove(data)
                except ReferenceError:
                    pass

    def restore_context(self):
        # Export operators may temporarily activate disposable clones. Restore
        # the user's selection/active object even if serialization failed.
        for obj in tuple(bpy.context.selected_objects):
            obj.select_set(False)
        for obj in self.selected:
            try:
                if self.view_layer.objects.get(obj.name) == obj:
                    obj.select_set(True)
            except ReferenceError:
                pass
        try:
            active = self.active
            if active is not None and self.view_layer.objects.get(active.name) == active:
                self.view_layer.objects.active = active
                if self.mode and active.mode != self.mode:
                    bpy.ops.object.mode_set(mode=self.mode)
            elif active is None:
                self.view_layer.objects.active = None
        except ReferenceError:
            pass


def track_object(obj):
    _scopes[-1].objects.append(obj)
    return obj


def track_mesh(mesh):
    _scopes[-1].meshes.append(mesh)
    return mesh


def track_armature(armature):
    _scopes[-1].armatures.append(armature)
    return armature


def temporary_collection():
    scope = _scopes[-1]
    if scope.collection is None:
        scope.collection = bpy.data.collections.new('clonedMeshes')
        bpy.context.scene.collection.children.link(scope.collection)
    return scope.collection


def owned_export_resources(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        scope = ExportResources()
        _scopes.append(scope)
        try:
            return function(*args, **kwargs)
        finally:
            try:
                scope.cleanup()
            finally:
                try:
                    scope.restore_context()
                finally:
                    _scopes.pop()
    return wrapped
