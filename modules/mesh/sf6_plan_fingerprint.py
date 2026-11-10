"""Prototype session plan fingerprint; external cache contents are not covered."""
import hashlib,json
import bpy,numpy as np
from .sf6_editor_sync import evaluated_multires_digest
from .sf6_driver_snapshot import driver_state, driver_dependencies
from .sf6_mode_snapshot import logical_name, logical_pointer, needs_snapshot, export_snapshot

def _is_runtime_pbvh_attribute(attribute):
    # Dyntopo rebuilds these internal PBVH node indices after Undo/Redo and
    # strokes without changing authored geometry. Blender's Named Attribute
    # node cannot read them. Direct RNA driver reads remain separately bound
    # by driver_state(), including their resolved values.
    expected = {'.sculpt_dyntopo_node_id_vertex': 'POINT',
                '.sculpt_dyntopo_node_id_face': 'FACE'}
    return (getattr(attribute, 'is_internal', False) and
            attribute.data_type == 'INT' and
            expected.get(attribute.name) == attribute.domain)


def _is_editor_state_attribute(attribute):
    # Entering and leaving Edit Mode without editing rewrites the selection
    # layers and drops sharp flags that are false everywhere. Neither changes
    # the exported mesh; face smoothing is still bound through use_smooth.
    if attribute.name.startswith('.select_'):
        return True
    if attribute.name in ('sharp_edge', 'sharp_face') and attribute.data_type == 'BOOLEAN':
        values = np.empty(len(attribute.data), np.bool_)
        attribute.data.foreach_get('value', values)
        return not values.any()
    return False


def fingerprint(collection,selected_objects,options=None,stable_ids=False,canonical_attributes=False):
    if needs_snapshot(collection):
        with export_snapshot(collection) as (target, snapshot):
            selected = None if selected_objects is None else tuple(snapshot.mapping[o] for o in selected_objects if o in snapshot.mapping)
            return fingerprint(target, selected, options, stable_ids, canonical_attributes)
    # Update evaluated transforms without committing live Edit Mode data.
    bpy.context.view_layer.update()
    digest=hashlib.sha256();pending=[];seen=set()
    def put(value):digest.update(json.dumps(value,sort_keys=True,default=str).encode());digest.update(b'\0')
    def identity(value):return (type(value).__name__,logical_name(value)) if stable_ids else logical_pointer(value)
    def pointer(value):
        if value is None:return None
        if isinstance(value,(bpy.types.Object,bpy.types.Collection,bpy.types.NodeTree,bpy.types.Texture)):pending.append(value)
        return (type(value).__name__,logical_name(value),identity(value))
    def rna(value):
        result={}
        for p in value.bl_rna.properties:
            key=p.identifier
            if key in ('rna_type','name') or p.is_readonly:continue
            try:v=getattr(value,key)
            except Exception:continue
            if p.type=='POINTER':result[key]=pointer(v)
            elif p.type=='COLLECTION':continue
            elif getattr(p,"is_array",False):result[key]=list(v)
            elif p.type in ('BOOLEAN','INT','FLOAT','STRING','ENUM'):result[key]=sorted(v) if isinstance(v,set) else v
        return result
    def custom(value):
        def convert(v):
            if isinstance(v,bpy.types.ID):return pointer(v)
            if hasattr(v,'to_dict'):return {k:convert(x) for k,x in v.to_dict().items()}
            if hasattr(v,'to_list'):return [convert(x) for x in v.to_list()]
            if isinstance(v,(list,tuple)):return [convert(x) for x in v]
            return v
        try:keys=value.keys()
        except TypeError:return {}  # Some modifier RNA types have no custom properties.
        return {k:convert(value[k]) for k in keys}
    put({'collection':((logical_name(collection),'stable_v1') if stable_ids else collection.get('SF6PlanCollectionIdentity',(logical_name(collection),logical_pointer(collection)))),'selected':None if selected_objects is None else sorted(identity(o) for o in selected_objects),'settings':{k:v for k,v in (options or {}).items() if not k.startswith('_sf6') and k!='targetCollection'},'source_identity':collection.get('SF6SourceSHA256'),'owners':(options or {}).get('_sf6PreserveSourceOwners',()),'logical_target':(options or {}).get('_sf6LogicalTargetPath'),'frame':(bpy.context.scene.frame_current,bpy.context.scene.frame_subframe)})
    put(('export_collection_metadata',collection.get('LODGroupNameHash'),collection.get('LOD Distance')))
    pending.extend(collection.children)
    pending.extend(collection.all_objects)
    while pending:
        o=pending.pop()
        if o.as_pointer() in seen:continue
        seen.add(o.as_pointer())
        if not stable_ids:
            owners=(o, getattr(o,'data',None),getattr(getattr(o,'data',None),'shape_keys',None))
            for owner in owners:
                state=driver_state(owner)
                if state:
                    put(('driver_dependencies',pointer(owner),state))
                    for dependency in driver_dependencies(owner):pointer(dependency)
        if isinstance(o,bpy.types.Collection):
            put(('collection_dependency',logical_name(o),identity(o),o.get('LODGroupNameHash'),o.get('LOD Distance'),[pointer(x) for x in o.objects],[pointer(x) for x in o.children]));continue
        if isinstance(o,bpy.types.Texture):
            put(('texture_dependency',logical_name(o),identity(o),rna(o),custom(o)));continue
        if isinstance(o,bpy.types.NodeTree):
            put(('node_tree_dependency',logical_name(o),identity(o),rna(o),custom(o)))
            for node in o.nodes:
                put((node.name,node.bl_idname,rna(node),custom(node)))
                for socket in list(node.inputs)+list(node.outputs):put((socket.identifier,rna(socket)))
            put([(link.from_node.name,link.from_socket.identifier,link.to_node.name,link.to_socket.identifier) for link in o.links])
            if hasattr(o,'interface'):put([(item.name,item.item_type,rna(item)) for item in o.interface.items_tree])
            continue
        put((logical_name(o),identity(o),pointer(o.data),[list(row) for row in o.matrix_world],pointer(o.parent),o.hide_viewport,o.hide_render))
        put([(m.name,m.type,rna(m),custom(m)) for m in o.modifiers]);put([(c.name,c.type,rna(c)) for c in o.constraints])
        if o.type=='ARMATURE' and canonical_attributes:
            # Leaving armature Edit Mode rounds rest matrices. The rest rig is
            # compared separately with a tolerance, so bind structure and the
            # authored pose channels here instead of exact derived matrices.
            put([(b.name,b.parent.name if b.parent else None,b.use_deform,custom(b)) for b in o.data.bones])
            put([(b.name,[list(row) for row in b.matrix_basis],[(c.name,c.type,rna(c)) for c in b.constraints]) for b in o.pose.bones])
        elif o.type=='ARMATURE':
            put([(b.name,b.parent.name if b.parent else None,[list(row) for row in b.matrix_local],b.use_deform,custom(b)) for b in o.data.bones])
            put([(b.name,[list(row) for row in b.matrix],[(c.name,c.type,rna(c)) for c in b.constraints]) for b in o.pose.bones])
        if o.type in ('CURVE','SURFACE','FONT') and not stable_ids:
            # Plan-only dependency state: extending the stored import-state
            # schema would invalidate v0.70 baseline hashes on saved files.
            put(('curve_dependency',rna(o.data),custom(o.data)))
            for spline in o.data.splines:
                put((rna(spline),[rna(point) for point in spline.points],
                     [rna(point) for point in spline.bezier_points]))
            if o.type == 'FONT':
                # Collection properties are excluded by generic writable RNA.
                put(('font_layout', [rna(char) for char in o.data.body_format],
                     [rna(box) for box in o.data.text_boxes]))
                for name in ('font', 'font_bold', 'font_italic', 'font_bold_italic'):
                    font = getattr(o.data, name, None)
                    put(('font_resource', name, pointer(font),
                         rna(font) if font is not None else None))
            keys=o.data.shape_keys
            if keys:
                put((keys.use_relative,keys.eval_time))
                for key in keys.key_blocks:
                    put((key.name,key.value,key.mute,key.relative_key.name,
                         [rna(point) for point in key.data]))
        if o.type=='LATTICE' and not stable_ids:
            put(('lattice_dependency',rna(o.data),custom(o.data),
                 [(tuple(point.co_deform),[(group.group,group.weight) for group in point.groups])
                  for point in o.data.points]))
            keys=o.data.shape_keys
            if keys:
                put((keys.use_relative,keys.eval_time,o.active_shape_key_index,o.show_only_shape_key))
                for key in keys.key_blocks:
                    put((key.name,key.value,key.mute,key.relative_key.name,
                         [tuple(point.co) for point in key.data]))
        if o.type!='MESH':continue
        if not stable_ids:
            put(('mesh_key_display', o.active_shape_key_index, o.show_only_shape_key))
            multires=evaluated_multires_digest(o)
            if multires is not None:put(('evaluated_multires',multires))
        mesh=o.data
        def floats(items,property,size):
            values=np.empty(len(items)*size,np.float32);items.foreach_get(property,values);digest.update(values.tobytes())
        floats(mesh.vertices,'co',3)
        put([(tuple(p.vertices),p.material_index,p.use_smooth) for p in mesh.polygons]);put([(g.index,g.name) for g in o.vertex_groups]);put([[(g.group,g.weight) for g in v.groups] for v in mesh.vertices])
        put([slot.material.name if slot.material else None for slot in o.material_slots])
        put(('active_channels',mesh.uv_layers.active_index,[u.active_render for u in mesh.uv_layers],getattr(mesh.color_attributes,'active_color_name','')))
        for layer in mesh.uv_layers:put(layer.name);floats(layer.data,'uv',2)
        for layer in mesh.color_attributes:put((layer.name,layer.domain,layer.data_type));floats(layer.data,'color',4)
        # Canonical form: attribute order also changes across an Edit Mode visit.
        attributes = sorted(mesh.attributes, key=lambda a: a.name) if canonical_attributes else mesh.attributes
        for attr in attributes:
            if _is_runtime_pbvh_attribute(attr):continue
            if canonical_attributes and _is_editor_state_attribute(attr):continue
            put((attr.name,attr.domain,attr.data_type,len(attr.data)))
            if not len(attr.data):continue
            sample=attr.data[0]
            for prop in sample.bl_rna.properties:
                key=prop.identifier
                if key=='rna_type':continue
                if prop.type in ('FLOAT','INT','BOOLEAN'):
                    size=max(1,getattr(prop,'array_length',0))
                    dtype=np.float32 if prop.type=='FLOAT' else np.bool_ if prop.type=='BOOLEAN' else np.int32
                    values=np.empty(len(attr.data)*size,dtype)
                    attr.data.foreach_get(key,values);put(key);digest.update(values.tobytes())
                elif prop.type in ('STRING','ENUM'):
                    put((key,[getattr(item,key) for item in attr.data]))
                else:
                    # Unknown data layouts must never silently evade plan safety.
                    raise ValueError('Unsupported mesh attribute fingerprint layout: '+attr.data_type+'/'+key)
        if len(mesh.corner_normals):floats(mesh.corner_normals,'vector',3)
        if mesh.shape_keys:
            keys=mesh.shape_keys;put((keys.use_relative,keys.eval_time))
            for k in keys.key_blocks:
                put((k.name,k.value,k.mute,k.vertex_group,k.relative_key.name,k.slider_min,k.slider_max));floats(k.data,'co',3)
    return digest.hexdigest()
