"""Preserve declared driver dependencies in private export scenes.

Scene driver paths may reach objects by name. Private IDs have different names,
so resolve their ID boundary on the original and address the copied ID directly.
Scalar Scene settings remain read-only references to the original complete scene.
"""
import bpy

_DEPENDENCY_TYPES = (bpy.types.Object, bpy.types.Collection, bpy.types.NodeTree, bpy.types.Texture)


def _path_boundaries(path):
    quote = None
    escaped = False
    depth = 0
    for index, char in enumerate(path):
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char == '[':
            if depth == 0 and index:
                yield index
            depth += 1
        elif char == ']':
            depth -= 1
            if depth == 0:
                yield index + 1
        elif char == '.' and depth == 0:
            yield index
    yield len(path)


def path_ids(owner, path):
    """Yield ID boundaries without evaluating Python or rewriting name strings."""
    if owner is None:
        return
    seen = set()
    for end in _path_boundaries(path):
        if not end or end in seen:
            continue
        seen.add(end)
        try:
            value = owner.path_resolve(path[:end], True)
        except (ValueError, AttributeError, KeyError, TypeError):
            continue
        if isinstance(value, bpy.types.ID):
            yield value, path[end:].lstrip('.')


def _source_target(target):
    from .sf6_mode_snapshot import current_snapshot
    snapshot = current_snapshot()
    return snapshot.driver_origins.get(target.as_pointer(), target) if snapshot else target


def driver_dependencies(owner):
    animation = getattr(owner, 'animation_data', None)
    if animation is None:
        return
    from .sf6_mode_snapshot import current_snapshot
    snapshot = current_snapshot()
    for curve in animation.drivers:
        for variable in curve.driver.variables:
            for actual in variable.targets:
                target = _source_target(actual)
                values = [(target.id, target.data_path)]
                if variable.type == 'SINGLE_PROP':
                    values.extend(path_ids(target.id, target.data_path))
                for value, _ in values:
                    if isinstance(value, _DEPENDENCY_TYPES):
                        yield snapshot.mapping.get(value, value) if snapshot else value
                    elif isinstance(value, (bpy.types.Mesh, bpy.types.Armature, bpy.types.Curve, bpy.types.Lattice, bpy.types.Key)):
                        # Direct data-ID drivers still need the object owning
                        # its live edit buffer, not a stale bare ID.copy().
                        for obj in bpy.data.objects:
                            if obj.data == value or getattr(obj.data, 'shape_keys', None) == value:
                                yield snapshot.mapping.get(obj, obj) if snapshot else obj


def remap_drivers(original, copied, mapping, origins):
    source_animation = getattr(original, 'animation_data', None)
    animation = getattr(copied, 'animation_data', None)
    if source_animation is None or animation is None:
        return
    source_curves = {(curve.data_path, curve.array_index): curve for curve in source_animation.drivers}
    for curve in animation.drivers:
        source_curve = source_curves[(curve.data_path, curve.array_index)]
        for variable, source_variable in zip(curve.driver.variables, source_curve.driver.variables):
            for target, source_target in zip(variable.targets, source_variable.targets):
                origins[target.as_pointer()] = source_target
                owner = source_target.id
                path = source_target.data_path
                # Only declared property paths may cross into another ID type.
                if source_variable.type == 'SINGLE_PROP':
                    for candidate, remainder in path_ids(owner, path):
                        if candidate in mapping and not isinstance(candidate, bpy.types.Scene):
                            owner, path = candidate, remainder
                if isinstance(owner, bpy.types.Scene):
                    # Scene itself is a complete read-only dependency, not the
                    # private evaluation scene with a deliberately smaller graph.
                    target.id = owner
                    target.data_path = path
                elif owner in mapping:
                    if source_variable.type == 'SINGLE_PROP':
                        target.id_type = owner.id_type
                    target.id = mapping[owner]
                    target.data_path = path


def _plain(value):
    from .sf6_mode_snapshot import logical_pointer, logical_name
    if isinstance(value, bpy.types.ID):
        return (value.id_type, logical_name(value), logical_pointer(value))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, 'to_dict'):
        return {key: _plain(item) for key, item in value.to_dict().items()}
    if hasattr(value, 'to_list'):
        return [_plain(item) for item in value.to_list()]
    try:
        return [_plain(item) for item in value]
    except TypeError:
        if hasattr(value, 'path_from_id'):
            try:return (type(value).__name__, value.path_from_id())
            except (ValueError, TypeError):pass
        return type(value).__name__


def _settings(value):
    result = {}
    for prop in value.bl_rna.properties:
        key = prop.identifier
        if key in ('rna_type', 'name') or prop.is_readonly:
            continue
        if prop.type in ('BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'):
            result[key] = _plain(getattr(value, key))
    return result


def driver_state(owner):
    """Plan-only driver definitions and resolved inputs, never baseline schema."""
    animation = getattr(owner, 'animation_data', None)
    if animation is None:
        return []
    result = []
    for curve in animation.drivers:
        variables = []
        for variable in curve.driver.variables:
            targets = []
            for actual in variable.targets:
                source = _source_target(actual)
                state = dict(settings=_settings(source), id=_plain(source.id), path=source.data_path)
                if variable.type == 'SINGLE_PROP':
                    try:
                        state['resolved'] = _plain(actual.id.path_resolve(actual.data_path, True))
                    except (AttributeError, ValueError, KeyError, TypeError) as error:
                        state['resolution_error'] = type(error).__name__
                targets.append(state)
            variables.append((variable.name, variable.type, targets))
        result.append(dict(path=curve.data_path, index=curve.array_index,
                           settings=_settings(curve), driver=_settings(curve.driver),
                           variables=variables,
                           points=[_settings(point) for point in curve.keyframe_points],
                           modifiers=[_settings(modifier) for modifier in curve.modifiers]))
    return result
