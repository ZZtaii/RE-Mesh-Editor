from ..gen_functions import textColors
import bpy
import textwrap
ERROR_WINDOW_SIZE = 750
SPLIT_FACTOR = .35
class REMeshErrorEntry(bpy.types.PropertyGroup):
	errorType: bpy.props.StringProperty(
		name="",
		)

	errorName: bpy.props.StringProperty(
		name="",
		)
	errorDescription: bpy.props.StringProperty(
		name="",
		)
	objectSetString: bpy.props.StringProperty(
		name="",
		)
	errorCount: bpy.props.IntProperty(
		name="",
		)   


class MESH_UL_REMeshErrorList(bpy.types.UIList):
	
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
		layout.label(text=f"{item.errorType} ({str(item.errorCount)})")
	# Disable double-click to rename
	def invoke(self, context, event):
		return {'PASS_THROUGH'}

class WM_OT_ShowREMeshErrorWindow(bpy.types.Operator):
	'Show Export Errors'
	bl_idname = 're_mesh.show_export_error_window'
	bl_label = 'RE Mesh Export Error'
	bl_options = {'REGISTER'}
	targetCollection : bpy.props.StringProperty()
	armatureName : bpy.props.StringProperty()
	errorList_items: bpy.props.CollectionProperty(type = REMeshErrorEntry)
	errorList_index: bpy.props.IntProperty(name="")
	def execute(self, context):
		print("Displayed error window2.")
		return {'FINISHED'}
		
	def invoke(self, context, event):
		region = bpy.context.region
		centerX = region.width // 2
		centerY = region.height
		
		#currentX = event.mouse_region_X
		#currentY = event.mouse_region_Y
		
		#Move cursor to center so error window is at the center of the window
		context.window.cursor_warp(centerX,centerY)
		for entry in bpy.context.scene.re_mesh_error_list:
			item = self.errorList_items.add()
			for key,value in entry.items():
				item[key] = value
		return context.window_manager.invoke_props_dialog(self,width = ERROR_WINDOW_SIZE)
	
	def draw(self, context):
		layout = self.layout
		rowCount = 2
		uifontscale = 9 * context.preferences.view.ui_scale
		max_label_width = int((ERROR_WINDOW_SIZE*(1-SPLIT_FACTOR)*(2-SPLIT_FACTOR)) // uifontscale)
		layout.label(text=f"The mesh has {len(self.errorList_items)} {'issues' if len(self.errorList_items) > 1 else 'issue'} that must be fixed before it can be exported.",icon = "ERROR")
		row = layout.row()
		row.label(text=f"Target Collection: {self.targetCollection}")
		row.label(text=f"Target Armature: {self.armatureName}")
		
		row = layout.row().separator()
		split = layout.split(factor = SPLIT_FACTOR)#Indent list slightly to make it more clear it's a part of a sub panel
		col1 = split.column()
		col2 = split.column()
		
		"""
		layout.prop(self,"currentError")
		row.label(text =f"Error {str(self.currentError)} / {str(len(bpy.context.scene.re_mesh_error_list))}")
		if self.currentError <= len(bpy.context.scene.re_mesh_error_list):
		"""
		
		if len(self.errorList_items) != 0:
			item = self.errorList_items[self.errorList_index]
			col2.label(text = f"Error Info: {item.errorType}")
			box = col2.box()
			row = box.row()
			for line in item.errorDescription.splitlines():
				line = line.strip()
				for chunk in textwrap.wrap(line,width=max_label_width):
					box.label(text=chunk)
					rowCount+=1
			row = layout.row()
			if item.objectSetString != "":
				for line in item.objectSetString.splitlines():
					line = line.strip()
					for chunk in textwrap.wrap(line,width=max_label_width):
						box.label(text=chunk)
						rowCount+=1
		col1.label(text = f"Error Count: {str(len(self.errorList_items))}")
		col1.template_list(
			listtype_name = "MESH_UL_REMeshErrorList", 
			list_id = "",
			dataptr = self,
			propname = "errorList_items",
			active_dataptr = self, 
			active_propname = "errorList_index",
			rows = rowCount,
			type='DEFAULT'
			)
def addErrorToDict(errorDict,errorType,objectName):
	if errorType in errorDict:
		errorDict[errorType]["count"] += 1
		if objectName != None:
			errorDict[errorType]["objectSet"].add(objectName)
		
	else:
		if objectName != None:
			errorDict[errorType] = {"count":1,"objectSet":set([objectName])}
		else:
			errorDict[errorType] = {"count":1,"objectSet":set()}
		
errorInfoDict = {
"NoMeshesInCollection":'No meshes to export\nChoose a target collection containing meshes. If Selected Objects Only is enabled, select meshes in that collection or turn it off.',

"MoreThanOneArmature":'More than one target armature\nThis collection needs exactly one target armature. Move extra armatures out of the export collection.',
"NoMaterialOnSubMesh":'Missing material\nAssign a material to every used section. If exporting material names from object names, include the MDF material name after two underscores.',

"MoreThanOneMaterialOnSubMesh":'Multiple materials on one submesh\nThis export path requires one material per submesh. Split this mesh by material before retrying.',

"NoUVMapOnSubMesh":'Missing UV map\nAdd a UV map and unwrap the mesh.',

"NoVerticesOnSubMesh":'No vertices\nAdd valid geometry, exclude this object from export, or remove it if unneeded.',

"NoFacesOnSubMesh":'No faces\nAdd valid geometry, exclude this object from export, or remove it if unneeded.',

"LooseVerticesOnSubMesh":"""Loose Vertices On Sub Mesh
A mesh has loose vertices with no faces assigned.
	
HOW TO FIX:
_______________
	
Select the listed mesh in edit mode, press A to select all vertices. In the menu bar at the top, select > Mesh > Clean Up > Delete Loose.

OR

Press the "Delete Loose Geometry" button in the RE Mesh tab to delete loose vertices on all meshes.
""",

"NonTriangulatedFace":"""Non Triangulated Faces
A mesh has non triangulated faces. All faces must be triangulated.
	
HOW TO FIX:
_______________
	
Select the listed mesh in edit mode, press A to select all vertices. Press Ctrl + T to triangulate faces.


""",
"MultipleUVsAssignedToVertex":"""Multiple UVs Assigned To Vertex
A mesh has multiple uvs assigned to a single vertex.
	
HOW TO FIX:
_______________
	
Select the listed mesh in edit mode, switch to edge select mode.
Press A to select all edges,then press F3 to search for and select "Seams From Islands".
Select an edge that is marked red on the mesh, then press Shift > G > Seam. 
Press V to rip the vertices and then press Esc so that the vertices stay in the same location.

OR

Check the "Auto Solve Repeated UVs" box when exporting the mesh.
""",
"MaxVerticesExceeded":"""Max Vertices Exceeded On Sub Mesh
A mesh exceeded the limit of 4294967295 vertices.
	
HOW TO FIX:
_______________
	
Separate parts of the mesh into more sub meshes.

OR

Use the decimate modifier to reduce mesh quality.
""",
"MaxFacesExceeded":'Too many faces\nToo many faces for this format. Split the object or reduce its face count. The format limit is 4294967295 faces.',

"MaxWeightsPerVertexExceeded":'Too many bone weights per vertex\nReduce the number of influences to the target format’s primary weight limit, then normalize valid weights. See the export details for the actual limit.',


"MaxPrimaryWeightsPerVertexExceeded":"""DD2 Primary Weights Per Vertex Exceeded
A vertex has more than 8 ordinary bone weights.

HOW TO FIX:
_______________
Limit ordinary bone weights to 8 on this mesh. Secondary weight arrays do not count towards the primary weight array's limit.
""",

"MaxSecondaryWeightsPerVertexExceeded":"""DD2 SHAPEKEY_ Weights Per Vertex Exceeded
A vertex has more than 8 SHAPEKEY_ bone weights in the secondary weight array.

HOW TO FIX:
_______________
Limit SHAPEKEY_ weights to 8 on this mesh. Primary weight arrays do not count towards the secondary weight array's limit.
""",

"ExtendedMaxWeightsPerVertexExceeded":'Too many bone weights per vertex\nReduce the number of influences to the target format’s extended weight limit, then normalize valid weights. See the export details for the actual limit.',

"NoBonesOnArmature":"""No Bones on Armature
The armature in the target collection has no bones.
	
HOW TO FIX:
_______________
	
Import a valid armature from an existing mesh file.
""",

"NoArmatureInCollection":"""No Armature In Collection
A mesh has weights but no armature is inside the mesh collection.
	
HOW TO FIX:
_______________
	
Move the armature that the mesh is parented to inside the mesh collection.

You can do this by selecting the armature in the outliner and dragging it onto the mesh collection.

""",

"NoWeightsOnMesh":'Missing bone weights\nAssign valid positive bone weights to every vertex. Weighted groups must match bones on the target armature. Normalize All alone does not repair unweighted vertices.',
"MaxWeightedBonesExceeded":"""Max Weighted Bones Exceeded
The mesh exceeded the limit of 256 bones with weights assigned to them.
	
HOW TO FIX:
_______________
	
Reduce the amount of chain bones on the armature.
""",
"InvalidWeights":'Invalid bone weights\nAssign valid positive bone weights to every vertex. Weighted groups must match bones on the target armature, and the armature must be in the export collection. Normalize valid weights after correcting missing or invalid assignments.',
	}
	
def printErrorDict(errorDict):
	print(f"\n{textColors.FAIL}Unable to export mesh. {len(errorDict)} error(s) were found that need to be fixed.{textColors.ENDC}\n")
	for index, errorType in enumerate(sorted(errorDict.keys())):
		count = errorDict[errorType]["count"]
		objectSet = errorDict[errorType]["objectSet"]
		errorInfo = errorInfoDict[errorType]
		nameListString = ""
		if objectSet:
			nameListString = f"\nObjects with this error ({str(len(objectSet))}):\n"
			for name in sorted(list(objectSet)):
				nameListString += "["+name +"]\n"
		print(f"{textColors.FAIL}ERROR ({str(index+1)} / {len(errorDict)}): {str(count)} instance(s) of {errorInfo}{nameListString}\n__________________________________{textColors.ENDC}")
		
def showREMeshErrorWindow(targetCollectionName,armatureObj,errorDict):
	bpy.types.Scene.re_mesh_error_list = bpy.props.CollectionProperty(type=REMeshErrorEntry)
	bpy.context.scene.re_mesh_error_list.clear()
	for index, errorType in enumerate(sorted(errorDict.keys())):
		item = bpy.context.scene.re_mesh_error_list.add()
		item.errorCount = errorDict[errorType]["count"]
		errorInfoSplit = errorInfoDict[errorType].split("\n",1)
		
		item.errorType = errorInfoSplit[0]
		item.errorDescription = errorInfoSplit[1]
		objectSet = errorDict[errorType]["objectSet"]
		errorInfo = errorInfoDict[errorType]
		nameListString = ""
		if objectSet:
			nameListString = f"\nObjects with this error ({str(len(objectSet))}):\n"
			for name in sorted(list(objectSet)):
				nameListString += "["+name +"]\n"
		item.objectSetString = nameListString
	if armatureObj != None:
		armatureName = armatureObj.name
	else:
		armatureName = "None"
	
	bpy.ops.re_mesh.show_export_error_window('INVOKE_DEFAULT',targetCollection = targetCollectionName,armatureName = armatureName)