"""Synthetic Blender regression coverage for automatic hybrid modding routing.
Run: blender -b --factory-startup --python-exit-code 1 --python this_file.py
Tests routing/provenance and cleanup, not binary encoding or in-game behavior.
"""
import importlib
import json
from pathlib import Path
import sys
import types
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_sf6_automatic_audit'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / 'modules' / 'mesh')]
sys.modules[PACKAGE] = package
auto = importlib.import_module(PACKAGE + '.sf6_auto_hybrid')
sf6 = auto.sf6_source
HybridExportError = importlib.import_module(PACKAGE + '.sf6_hybrid').HybridExportError
HASH = '12345678' * 8
PARTS = [dict(lod=0, group=i, sub=0, material=i, start=i*3, count=3, faces=1) for i in range(2)]
class Source:
    parts = PARTS
    materials = ['body', 'cloth']
    # The corrective is authored over every vertex of both parts (start, offset, count).
    shapes = [[('corrective', [(p['start'], 0, p['count']) for p in PARTS])]]
    def part_shapes(self, part):
        return [('corrective', np.zeros((3,3)), [])]
SOURCE = Source()
RESULTS = []

def counts():
    return tuple(len(x) for x in (bpy.data.objects,bpy.data.meshes,bpy.data.collections,bpy.data.shape_keys))

def fixture():
    for o in list(bpy.data.objects): bpy.data.objects.remove(o, do_unlink=True)
    for c in list(bpy.data.collections): bpy.data.collections.remove(c)
    c = bpy.data.collections.new('fixture'); bpy.context.scene.collection.children.link(c)
    ad = bpy.data.armatures.new('rig'); arm = bpy.data.objects.new('rig', ad); c.objects.link(arm)
    bpy.context.view_layer.objects.active = arm; arm.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT'); bone = ad.edit_bones.new('C_Hip'); bone.tail=(0,0,1)
    bpy.ops.object.mode_set(mode='OBJECT'); arm.select_set(False)
    objs=[]
    for p in PARTS:
        mesh=bpy.data.meshes.new('mesh'); mesh.from_pydata([(0,0,p['group']), (1,0,p['group']), (0,1,p['group'])], [], [(0,1,2)]*p['faces'])
        obj=bpy.data.objects.new(f"Group_{p['group']}_Sub_0__{SOURCE.materials[p['material']]}",mesh);c.objects.link(obj)
        mesh.materials.append(bpy.data.materials.get(SOURCE.materials[p['material']]) or bpy.data.materials.new(SOURCE.materials[p['material']]))
        obj[sf6.META]=json.dumps(p);obj['SF6SourceSHA256']=HASH
        for name,domain,values in [(sf6.VERTEX_ID,'POINT',[0,1,2]),(sf6.FACE_ID,'FACE',list(range(p['faces'])))]+[(name,'POINT',[v]*3) for name,v in zip(auto.ORIGIN,auto.token(HASH,p))]:
            attr=mesh.attributes.new(name,'INT',domain);attr.data.foreach_set('value',values)
        obj.vertex_groups.new(name='C_Hip').add([0,1,2],1,'REPLACE')
        mod=obj.modifiers.new('Armature','ARMATURE');mod.object=arm
        obj.shape_key_add(name='Basis');obj.shape_key_add(name='corrective')
        objs.append(obj)
    return c,arm,objs

DUPLICATE='Duplicate source claim; ordinary fallback loses its corrective links'
AMBIGUOUS='Ambiguous or missing joined source row identities'
MISSING='Missing compatible target shape keys'
MASKED='Non-Basis-relative or masked target keys'
MATERIAL='Material changed on source section'
EXTRA='Additional shape keys have no verified source corrective identity'

def run(name, mutate, expected_retained=None, expected_ordinary=None, error=None, selection=None, losses=()):
    """Prepare one case. An edit that would drop source correctives never proceeds
    on its own: each such section first asks for consent with an exact reason,
    and only becomes Ordinary geometry once that loss token is granted.
    'losses' lists the reasons asked for, in order; 'error' is a refusal that no
    consent can lift."""
    c,arm,objs=fixture(); mutate(c,arm,objs)
    before=counts(); selected=None if selection is None else [objs[i] for i in selection]
    granted=[];asked=[]
    while True:
        try:
            with auto.automatic_collection(c,SOURCE,HASH,selected,approved_corrective_losses=tuple(granted)) as (prepared,mapped,report):
                if error: raise AssertionError('Expected refusal: '+error)
                assert report['retained_source_parts']==expected_retained,report
                assert len(report['ordinary_sections'])==expected_ordinary,report
                assert len(mapped)==report['output_sections']
            break
        except auto.CorrectiveLossConsentRequired as exc:
            assert counts()==before, ('Consent request leaked data',before,counts())
            assert exc.loss_token not in granted, 'Granted loss was asked for again'
            asked.append(exc.reason);granted.append(exc.loss_token)
            assert len(asked)<=len(losses), (name,asked)
        except HybridExportError as exc:
            assert error and error in str(exc),(error,str(exc))
            break
    assert asked==list(losses), (name,asked)
    assert counts()==before, ('Temporary data leak',before,counts())
    RESULTS.append(dict(test=name,passed=True));print('PASS '+name,flush=True)

run('unchanged_two_source_parts',lambda c,a,o:None,2,0)
run('deleted_part_is_reported_not_resurrected',lambda c,a,o:bpy.data.objects.remove(o[1],do_unlink=True),1,0)
run('selected_subset_excludes_other_parts',lambda c,a,o:None,1,0,selection=[0])
# An empty selection prepares nothing here; the export operators reject it before any file is written.
run('empty_selection_prepares_nothing',lambda c,a,o:None,0,0,selection=[])
def duplicate(c,a,o):
    d=o[0].copy();d.data=o[0].data.copy();c.objects.link(d)
run('duplicate_shaped_claims_need_consent_for_each_claimant',duplicate,1,2,losses=[DUPLICATE,DUPLICATE])
def join(c,a,o):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in o:obj.select_set(True)
    bpy.context.view_layer.objects.active=o[0];bpy.ops.object.join()
run('joined_native_parts_recover_separate_origins',join,2,0)
def joined_duplicate(c,a,o):
    d=o[0].copy();d.data=o[0].data.copy();c.objects.link(d)
    d.location.x=2
    join(c,a,[o[0],d])
run('joined_moved_duplicate_needs_consent',joined_duplicate,1,1,losses=[AMBIGUOUS])

run('missing_corrective_is_ordinary_after_consent',lambda c,a,o:o[0].shape_key_clear(),1,1,losses=[MISSING])
run('masked_corrective_is_ordinary_after_consent',lambda c,a,o:setattr(o[0].data.shape_keys.key_blocks[1],'vertex_group','C_Hip'),1,1,losses=[MASKED])
run('absolute_correctives_are_ordinary_after_consent',lambda c,a,o:setattr(o[0].data.shape_keys,'use_relative',False),1,1,losses=[MASKED])
run('duplicated_vertex_ids_need_consent',lambda c,a,o:setattr(o[0].data.attributes[sf6.VERTEX_ID].data[1],'value',0),1,1,losses=[AMBIGUOUS])
run('removed_provenance_is_ordinary_after_consent',lambda c,a,o:o[0].data.attributes.remove(o[0].data.attributes[sf6.VERTEX_ID]),1,1,losses=[AMBIGUOUS])
run('shaped_material_reassignment_needs_consent',lambda c,a,o:o[0].data.materials.__setitem__(0,bpy.data.materials.new('replacement')),1,1,losses=[MATERIAL])
def added(c,a,o):
    d=o[0].copy();d.data=o[0].data.copy();c.objects.link(d);d.shape_key_clear()
    for key in list(d.keys()):del d[key]
    for name in auto.ORIGIN: d.data.attributes.remove(d.data.attributes[name])
run('new_rigged_geometry_is_ordinary',added,2,1)
def unbound(c,a,o):
    o[0].shape_key_clear();o[0].modifiers.clear()
run('unbound_replacement_is_ordinary_after_consent',unbound,1,1,losses=[MISSING])
def unweighted(c,a,o):
    o[0].shape_key_clear();o[0].vertex_groups.clear()
run('unweighted_replacement_refuses_even_with_consent',unweighted,error='invalid or unweighted',losses=[MISSING])
def joined_modifier(c,a,o):
    join(c,a,o);o[0].modifiers.new('Mirror','MIRROR')
run('joined_live_modifier_refuses_before_geometry_changes',joined_modifier,error='Apply non-armature modifiers')

run('extra_unmapped_key_needs_consent',lambda c,a,o:o[0].shape_key_add(name='CustomUnmappedKey'),1,1,losses=[EXTRA])
run('entire_mesh_replaced_is_ordinary_after_consent',lambda c,a,o:[obj.shape_key_clear() for obj in o],0,2,losses=[MISSING,MISSING])
def empty_mesh(c,a,o):
    o[0].shape_key_clear();o[0].data.clear_geometry()
run('empty_mesh_refuses_actionably',empty_mesh,error='Mixed mesh has no faces')

run('missing_assigned_material_refuses',lambda c,a,o:o[0].data.materials.clear(),error='Assign a material')
def joined_material(c,a,o):
    o[1].data.materials[0]=bpy.data.materials.new('joined_replacement')
    join(c,a,o)
run('joined_material_reassignment_needs_consent',joined_material,1,1,losses=[MATERIAL])
def object_material(c,a,o):
    o[0].material_slots[0].link='OBJECT'
    o[0].material_slots[0].material=bpy.data.materials.new('object_replacement')
run('object_linked_material_reassignment_needs_consent',object_material,1,1,losses=[MATERIAL])
run('unused_material_slot_does_not_change_identity',lambda c,a,o:o[0].data.materials.append(bpy.data.materials.new('unused')),2,0)

def equivalent_slot(c,a,o):
    o[0].data.materials.append(o[0].data.materials[0])
    o[0].data.polygons[0].material_index=1
run('equivalent_material_in_another_slot_preserves_shapes',equivalent_slot,2,0)

# A changed, unselected section must not block exporting the selected one.
c,arm,objs=fixture()
objs[1].data.materials[0]=bpy.data.materials.new('unselected_replacement')
assert not auto.needs_automatic(c,SOURCE,HASH,{objs[0]})
before=counts()
with auto.automatic_collection(c,SOURCE,HASH,[objs[0]]) as (_,_,report):
    assert report['retained_source_parts']==1 and not report['ordinary_sections']
assert counts()==before
RESULTS.append(dict(test='unselected_material_change_does_not_block_subset',passed=True))

# Exact source identities containing dots must not be mistaken for edits.
original_materials=SOURCE.materials
try:
    SOURCE.materials=['body.layer','cloth']
    c,arm,objs=fixture()
    assert not auto.needs_automatic(c,SOURCE,HASH)
    run('literal_dotted_source_material_retains_shapes',lambda c,a,o:None,2,0)
    def dotted_duplicate(c,a,o):
        o[0].data.materials[0]=bpy.data.materials.new('body.layer.001')
        assert not auto.needs_automatic(c,SOURCE,HASH)
    run('dotted_source_numeric_duplicate_retains_shapes',dotted_duplicate,2,0)
finally:
    SOURCE.materials=original_materials

# Multiple used but equivalent material slots are still a single source part.
try:
    PARTS[0]['faces']=2
    def duplicate_used_slots(c,a,o):
        o[0].data.materials.append(o[0].data.materials[0])
        o[0].data.polygons[1].material_index=1
        assert auto.needs_automatic(c,SOURCE,HASH)
    run('multiple_equivalent_used_slots_retain_source_correctives',duplicate_used_slots,2,0)
finally:
    PARTS[0]['faces']=1

# Route detection itself must reach normalization when only the material changes.
c,arm,objs=fixture()
assert not auto.needs_automatic(c,SOURCE,HASH)
objs[0].data.materials[0]=bpy.data.materials.new('single_changed_material')
detected=auto.needs_automatic(c,SOURCE,HASH)
RESULTS.append(dict(test='material_reassignment_route_detection',passed=detected))
print('ROUTE_MATERIAL_CHANGE',detected,flush=True)
print(json.dumps({'blender':bpy.app.version_string,'results':RESULTS}),flush=True)
assert detected,'Single-slot material reassignment does not enter automatic normalization'
