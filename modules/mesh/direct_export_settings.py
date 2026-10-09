"""Direct-export preferences, isolated from batch and mod-folder exporters."""
import json,os,tempfile
from pathlib import Path
import bpy
KEY='REMeshDirectExportSettingsV1'
FIELDS=('selectedOnly','exportAllLODs','exportBlendShapes','sf6HybridPreserve','rotate90',
'autoSolveRepeatedUVs','preserveSharpEdges','splitLoopVertices','limitTotal','limitTotalCount',
'normalizeWeights','shapeKeyExportMode','useBlenderMaterialName','preserveBoneMatrices',
'exportBoundingBoxes','preserveMaterialTable')
def settings_path():
    return Path(bpy.utils.user_resource('CONFIG'))/'re_mesh_editor'/'direct_mesh_export.json'
def read_defaults():
    try:
        value=json.loads(settings_path().read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}
def load(operator,context,explicit=(),restore_enabled=True):
    disk=read_defaults()
    if restore_enabled and 'rememberFields' not in explicit:
        enabled=disk.get('enabled',True)
        operator.rememberFields=enabled if type(enabled) is bool else True
    if not operator.rememberFields:return
    versions=disk.get('versions',{})
    profile=versions.get(operator.filename_ext,{}) if isinstance(versions,dict) else {}
    values=dict(profile) if isinstance(profile,dict) else {}
    col=bpy.data.collections.get(operator.targetCollection)
    if col:
        try:local=json.loads(col.get(KEY,'{}'))
        except (ValueError,TypeError):local={}
        if isinstance(local,dict) and local.get('version')==operator.filename_ext:
            values.update(local.get('values',{}) if isinstance(local.get('values'),dict) else {})
            if 'filepath' not in explicit and isinstance(local.get('filepath'),str):operator.filepath=local['filepath']
    for key in FIELDS:
        if key in explicit or key not in values:continue
        prop=getattr(operator,'properties',operator).bl_rna.properties.get(key)
        value=values[key]
        if prop is None:continue
        valid=(prop.type=='BOOLEAN' and type(value)is bool) or (prop.type=='INT' and type(value)is int and prop.hard_min<=value<=prop.hard_max) or (prop.type=='ENUM' and isinstance(value,str) and value in prop.enum_items.keys())
        if valid:setattr(operator,key,value)
def save(operator,context):
    disk=read_defaults();disk['enabled']=bool(operator.rememberFields)
    if operator.rememberFields:
        values={k:getattr(operator,k) for k in FIELDS};versions=disk.get('versions',{})
        if not isinstance(versions,dict):versions={}
        versions[operator.filename_ext]=values;disk['versions']=versions
        col=bpy.data.collections.get(operator.targetCollection)
        if col:col[KEY]=json.dumps({'version':operator.filename_ext,'values':values,'filepath':operator.filepath})
    path=settings_path();tmp=None
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as f:
            tmp=f.name;json.dump(disk,f,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    except OSError as e:
        if tmp:
            try:os.unlink(tmp)
            except OSError:pass
        operator.report({'WARNING'},'Direct export settings could not be saved: '+str(e))
