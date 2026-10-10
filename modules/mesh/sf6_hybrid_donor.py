"""Explicit foreign-geometry replacement slots for hybrid export.

Donor corrective shapes are not transferred. User geometry stays untouched;
only the evaluated export snapshot drops foreign source identity and keys.
"""
import base64,hashlib,json,zlib,math
import bpy
from bpy.props import StringProperty,EnumProperty,BoolProperty
from .sf6_source import SOURCE,SourceMesh
KEY='SF6HybridDonorSlotV1'
_enum_cache={}
def source_for(collection):
    text=bpy.data.texts.get(collection.get(SOURCE,''))
    if text is None:raise ValueError('Target needs an embedded SF6 source import.')
    data=zlib.decompress(base64.b64decode(text.as_string()))
    if hashlib.sha256(data).hexdigest()!=collection.get('SF6SourceSHA256'):raise ValueError('Target source hash mismatch.')
    return SourceMesh(data)
def slot_for(obj,collection,source):
    try:d=json.loads(obj[KEY])
    except (KeyError,ValueError,TypeError) as e:raise ValueError('Invalid donor-slot metadata: '+obj.name) from e
    if not isinstance(d,dict) or any(type(d.get(k)) is not int for k in ('group','sub')) or not isinstance(d.get('material'),str):
        raise ValueError('Invalid donor-slot metadata: '+obj.name)
    if d.get('source_hash')!=collection.get('SF6SourceSHA256'):raise ValueError('Donor slot belongs to a different target character: '+obj.name)
    matches=[p for p in source.parts if p['lod']==0 and p['group']==d.get('group') and p['sub']==d.get('sub') and source.materials[p['material']]==d.get('material')]
    if len(matches)!=1:raise ValueError('Donor target slot is missing or ambiguous: '+obj.name)
    arms=[o for o in collection.objects if o.type=='ARMATURE']
    if len(arms)!=1:raise ValueError('Donor hybrid export requires one target armature on the mesh collection root.')
    arm=arms[0];modifiers=[m for m in obj.modifiers if m.type=='ARMATURE' and m.show_viewport]
    if len(modifiers)!=1 or modifiers[0].object!=arm:raise ValueError('Bind the donor to the target character armature before hybrid export: '+obj.name)
    names={g.index:g.name for g in obj.vertex_groups}
    for v in obj.data.vertices:
        if any(g.group not in names or not math.isfinite(g.weight) or g.weight<0 for g in v.groups):
            raise ValueError('Donor contains invalid skin weights: '+obj.name)
    used={names[g.group] for v in obj.data.vertices for g in v.groups if g.weight>0}
    unknown=used-set(arm.data.bones.keys())
    if unknown:raise ValueError('Donor weights reference missing target bones: '+', '.join(sorted(unknown)))
    if not used:raise ValueError('Donor has no skin weights: '+obj.name)
    if any(sum(g.weight for g in v.groups)<=0 for v in obj.data.vertices):raise ValueError('Donor contains unweighted vertices: '+obj.name)
    return matches[0],d

def slot_items(self,context):
    c=bpy.data.collections.get(self.targetCollection)
    try:
        if c is None:return []
        source_hash=c.get('SF6SourceSHA256','')
        if source_hash not in _enum_cache:
            s=source_for(c)
            _enum_cache[source_hash]=[(str(p['start']),f"Group {p['group']} / Sub {p['sub']} / {s.materials[p['material']]}",'Replace this target part with ordinary donor geometry') for p in s.parts if p['lod']==0]
        return _enum_cache[source_hash]
    except Exception:return []
class PrepareHybridDonor(bpy.types.Operator):
    bl_idname='re_mesh.prepare_hybrid_donor'
    bl_label='RE Mesh: Prepare Hybrid Donor Replacement'
    bl_description='Assign the active donor mesh to an existing target slot. Donor shapes are excluded only from export; exclude or remove the replaced target object yourself'
    bl_options={'REGISTER','UNDO'}
    targetCollection:StringProperty(name='Target Mesh Collection')
    targetSlot:EnumProperty(name='Target Part',items=slot_items)
    @classmethod
    def poll(cls,context):return context.active_object is not None and context.active_object.type=='MESH'
    def invoke(self,context,event):
        return context.window_manager.invoke_props_dialog(self,width=650)
    def draw(self,context):
        self.layout.prop_search(self,'targetCollection',bpy.data,'collections')
        self.layout.prop(self,'targetSlot')
        self.layout.label(text='Use a donor already rigged to the target armature.')
        self.layout.label(text='Exclude/remove the original target part; donor shapes will not export.')
    def execute(self,context):
        obj=context.active_object;c=bpy.data.collections.get(self.targetCollection)
        if c is None:self.report({'ERROR'},'Choose the target mesh collection.');return {'CANCELLED'}
        old=obj.get(KEY)
        try:
            s=source_for(c);matches=[p for p in s.parts if p['lod']==0 and str(p['start'])==self.targetSlot]
            if len(matches)!=1:raise ValueError('Choose a unique target part.')
            p=matches[0];d={'source_hash':c['SF6SourceSHA256'],'group':p['group'],'sub':p['sub'],'material':s.materials[p['material']]}
            obj[KEY]=json.dumps(d);slot_for(obj,c,s)
            if obj.name not in c.all_objects:raise ValueError('Move/link the donor into the target collection first.')
        except Exception as e:
            if old is None:
                if KEY in obj:del obj[KEY]
            else:obj[KEY]=old
            self.report({'ERROR'},str(e));return {'CANCELLED'}
        self.report({'INFO'},'Donor assigned. Exclude the replaced target part, then use hybrid export. Donor corrective shapes will not be included.')
        return {'FINISHED'}

class ClearHybridDonor(bpy.types.Operator):
    bl_idname='re_mesh.clear_hybrid_donor'
    bl_label='RE Mesh: Clear Hybrid Donor Assignment'
    bl_description='Remove the active mesh donor-slot assignment without changing geometry, rig, materials, or original import metadata'
    bl_options={'REGISTER','UNDO'}

    @classmethod
    def poll(cls,context):
        obj=context.active_object
        return obj is not None and obj.type=='MESH' and KEY in obj

    def execute(self,context):
        obj=context.active_object
        if obj is None or obj.type!='MESH' or KEY not in obj:
            self.report({'ERROR'},'Select a mesh with a hybrid donor assignment.')
            return {'CANCELLED'}
        del obj[KEY]
        self.report({'INFO'},'Donor assignment cleared. Original import metadata and geometry are unchanged.')
        return {'FINISHED'}

