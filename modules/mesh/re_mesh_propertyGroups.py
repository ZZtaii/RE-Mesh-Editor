#Author: NSA Cloud
import bpy
import os
import json
from bpy.props import (StringProperty,
					   BoolProperty,
					   IntProperty,
					   FloatProperty,
					   FloatVectorProperty,
					   EnumProperty,
					   PointerProperty,
					   CollectionProperty,
					   )


def getBatchMeshWeightLimitDefault(path):
	"""Use the destination game's total weight slots, or eight for an unknown path."""
	from .blender_re_mesh import getMeshWeightLimits
	from .file_re_mesh import meshFileVersionToGameNameDict
	try:
		version = int(os.path.splitext(path)[1].lstrip("."))
	except (TypeError, ValueError):
		return 8
	game = meshFileVersionToGameNameDict.get(version)
	return getMeshWeightLimits(game)[1] if game is not None else 8


def setBatchMeshWeightLimit(item, value, explicit=False):
	"""Distinguish an automatically chosen default from a saved/user choice."""
	item.limitTotalCount = value
	if isinstance(item, bpy.types.PropertyGroup):
		item.batchWeightLimitExplicit = bool(explicit)


def getBatchMeshWeightLimit(item):
	# Fresh operator items may be supplied with only a destination path.
	if hasattr(item, "is_property_set") and not item.is_property_set("limitTotalCount"):
		return getBatchMeshWeightLimitDefault(item.path)
	return getattr(item, "limitTotalCount", getBatchMeshWeightLimitDefault(item.path))


def _remember_batch_edit(self, name):
	if getattr(self, 'sf6BatchInitializing', False):
		return
	try:
		explicit = json.loads(self.sf6BatchExplicitFields)
	except (ValueError, TypeError):
		explicit = []
	if not isinstance(explicit, list):
		explicit = []
	self.sf6BatchExplicitFields = json.dumps(sorted(set(explicit) | {name}))


def update_batchPreserveSource(self, _context):
	# Population marks malformed stored values after its initial assignment.
	self.sf6BatchPreserveReviewRequired = False
	_remember_batch_edit(self, 'exportBlendShapes')


def update_batchIntent(name):
	def update(self, _context):
		_remember_batch_edit(self, name)
	return update


def update_batchUnifiedRequest(self, _context):
	if not getattr(self, 'sf6BatchInitializing', False):
		from .sf6_export_settings import apply_batch_new_profile_defaults
		apply_batch_new_profile_defaults(self, bpy.data.collections.get(self.name))


def update_batchLodChoice(self, _context):
	if not getattr(self, 'sf6BatchInitializing', False):
		self.sf6LodPolicy = self.sf6LodChoice


def update_batchWeightLimit(self, _context):
	self.batchWeightLimitExplicit = True


def update_relPathToAbs(self,context):
	try:
		if "//" in self.path:
			#print("updated path")
			self.path = os.path.realpath(bpy.path.abspath(self.path))
	except:
		pass
	if self.path == "" or self.path.count(".") < 2:#Check if path is empty or if number extension is missing
		self.invalid = True
	else:
		self.invalid = False
	if self.exportType == "MESH" and not self.batchWeightLimitExplicit:
		setBatchMeshWeightLimit(self, getBatchMeshWeightLimitDefault(self.path))
class ExporterNodePropertyGroup(bpy.types.PropertyGroup):
	name: StringProperty(
        name="",
		description = "",
	)
	icon: StringProperty(
        name="",
		description = "",
	)
	enabled: BoolProperty(
		name="",
		description = "",
		default = True,

	)
	show: BoolProperty(
		name="",
		description = "",
		default = True
	)
	hasChild: BoolProperty(
		name="",
		description = "",
		default = False
	)
	expand: BoolProperty(
		name="",
		description = "",
		default = True
	)
	
	parentName: StringProperty(
		name="",
		description = "",
		default = ""
	)
	hierarchyLevel: IntProperty(
		name="",
		description = "",
		default = 0
	)
	exportType: StringProperty(
        name="",
		description = "",
		default = ""
	)
	batchWeightLimitExplicit: BoolProperty(
		name="Weight Limit Explicit",
		description="Internal marker for a saved or manually edited batch weight limit",
		default=False,
		options={'HIDDEN', 'SKIP_SAVE'},
	)
	path: StringProperty(
        name="",
		subtype="FILE_PATH",
		description = "Path to where to export the file to",
		update = update_relPathToAbs
	)
	invalid: BoolProperty(
		name="",
		description = "",
		default = False
	)

	# Prototype-only inputs. Persisted workflow choices live in an independent
	# collection namespace; review tokens and results never become presets.
	sf6BatchInitializing: BoolProperty(name="sf6BatchInitializing", default=False,
		options={'HIDDEN', 'SKIP_SAVE'})
	sf6BatchExplicitFields: StringProperty(name="sf6BatchExplicitFields", default="[]",
		options={'HIDDEN', 'SKIP_SAVE'})
	sf6BatchPreserveReviewRequired: BoolProperty(name="sf6BatchPreserveReviewRequired", default=False,
		options={'HIDDEN', 'SKIP_SAVE'})
	sf6ReviewOnly: BoolProperty(name="sf6ReviewOnly", default=False,
		options={'HIDDEN', 'SKIP_SAVE'})
	sf6UnifiedOptIn: BoolProperty(name="sf6UnifiedOptIn", default=False,
		options={'HIDDEN', 'SKIP_SAVE'}, update=update_batchUnifiedRequest)
	sf6ExportWorkflow: StringProperty(name="sf6ExportWorkflow", default="",
		options={'HIDDEN'}, update=update_batchUnifiedRequest)
	sf6LodChoice: EnumProperty(name="LOD Export", items=(
		('retain_if_compatible', 'Keep compatible source LODs', 'Keep source LODs when the unchanged source output is compatible; rebuilt output may contain only LOD0'),
		('lod0', 'LOD0 only', 'Export only the highest-detail mesh level')),
		default='retain_if_compatible', options={'SKIP_SAVE'}, update=update_batchLodChoice)
	sf6LodPolicy: StringProperty(name="sf6LodPolicy", default="",
		options={'HIDDEN'})
	sf6RigPolicy: StringProperty(name="sf6RigPolicy", default="auto",
		options={'HIDDEN', 'SKIP_SAVE'})
	sf6ApprovedCorrectiveLosses: StringProperty(name="sf6ApprovedCorrectiveLosses", default="[]",
		options={'HIDDEN', 'SKIP_SAVE'})
	sf6ExpectedPlan: StringProperty(name="sf6ExpectedPlan", default="",
		options={'HIDDEN', 'SKIP_SAVE'})
	sf6UnifiedResult: StringProperty(name="sf6UnifiedResult", default="",
		options={'HIDDEN', 'SKIP_SAVE'})

	#mesh operator arguments
	
	exportAllLODs : BoolProperty(
	   name = "Export All LODs",
	   description = "Export all LODs. If disabled, only LOD0 will be exported. Note that LODs meshes must be grouped inside a collection for each level and that collection must be contained in another collection. See a mesh with LODs imported for reference on how it should look. A target collection must also be set",
	   default = True)
	exportBlendShapes : BoolProperty(
	   name = "Preserve Compatible Source Data",
	   description = "Keep verified source corrective shapes where compatible. Required shape-key losses are reviewed before export",
	   default = False, update=update_batchPreserveSource)
	rotate90 : BoolProperty(
	   name = "Convert Z Up To Y Up",
	   description = "Rotates objects 90 degrees for export. Leaving this option enabled is recommended",
	   default = True)
	autoSolveRepeatedUVs : BoolProperty(
	   name = "Auto Solve Repeated UVs",
	   description = "Splits connected UV islands if present. The mesh format does not allow for multiple uvs assigned to a vertex.\nNOTE: This will modify the object and may slightly increase time taken to export",
	   default = True, update=update_batchIntent("autoSolveRepeatedUVs"))
	preserveSharpEdges : BoolProperty(
	   name = "Split Sharp Edges",
	   description = "Edge splits all edges marked as sharp to preserve them on the exported mesh.\nNOTE: This will modify the exported mesh",
	   default = True, update=update_batchIntent("preserveSharpEdges"))
	splitLoopVertices : BoolProperty(
	   name = "Split Vertices For Corner Attributes",
	   description = "Create exported vertices when corner normals, tangents, UVs, or colors differ. Disable to keep one exported vertex per Blender vertex",
	   default = True, update=update_batchIntent("splitLoopVertices"))
	limitTotal : BoolProperty(
	   name = "Limit Total",
	   description = "Limit bone influences on export. Enable Normalize Weights to renormalize the remaining weights",
	   default = False)
	limitTotalCount : IntProperty(
	   name = "Max Weights",
	   description = "Maximum bone influences per vertex when Limit Total is enabled",
	   default = 8,
	   min = 1,
	   max = 32,
	   update = update_batchWeightLimit)
	normalizeWeights : BoolProperty(
	   name = "Normalize Weights",
	   description = "Normalize each set of vertex weights to 1.0 on export",
	   default = True)
	shapeKeyExportMode : EnumProperty(
		name = "Export Shapekeys?",
		description = "Choose whether and how Monster Hunter Wilds shape keys are exported",
		items = [
			("NO", "No", "Do not export shape keys or Wilds blendshape data"),
			("MODE0", "Mode 0", "Keep shading consistent with meshes without blendshapes; recommended for custom or heavily edited meshes"),
			("MODE1", "Mode 1", "Use vanilla-style blendshape shading; recommended for retaining vanilla data"),
		],
		default = "MODE0")
	useBlenderMaterialName : BoolProperty(
	   name = "Use Blender Material Names",
	   description = "If left unchecked, the exporter will get the material names to be used from the end of each object name. For example, if a mesh is named LOD_0_Group_0_Sub_0__Shirts_Mat, the material name is Shirts_Mat. If this option is enabled, the material name will instead be taken from the first material assigned to the object",
	   default = False)
	preserveBoneMatrices : BoolProperty(
	   name = "Preserve Bone Matrices",
	   description = "Export using the original matrices of the imported bones. Note that this option only applies armatures imported with this addon. Any newly added bones will have new matrices calculated",
	   default = False, update=update_batchIntent("preserveBoneMatrices"))
	exportBoundingBoxes : BoolProperty(
	   name = "Export Bounding Boxes",
	   description = "Exports the original bounding boxes from the \"Import Bounding Boxes\" import option. New bounding boxes will be generated for any bones that do not have them",
	   default = False)
	
	
class MESH_UL_REExporterList(bpy.types.UIList):
	
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
				
			row = layout.row()
			if not item.hasChild and item.invalid:
				row.alert = True
			col1 = row.column()
			#col1.prop(item,"expand")
			col1.alignment = "RIGHT"
			col1.label(text="      |    "*item.hierarchyLevel if item.hierarchyLevel != 0 else " ")
			if not item.hasChild:
				col2 = row.column()
				col2.prop(item,"enabled")
			col3 = row.column()
			col3.label(icon = item.icon,text=item.name)
			col4 = row.column()
			
	# Disable double-click to rename
	def invoke(self, context, event):
		return {'PASS_THROUGH'}
