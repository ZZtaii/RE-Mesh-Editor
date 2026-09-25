import bpy

from bpy.types import (Panel,
					   Menu,
					   Operator,
					   PropertyGroup,
					   )


def tag_redraw(context, space_type="PROPERTIES", region_type="WINDOW"):
	for window in context.window_manager.windows:
		for area in window.screen.areas:
			if area.spaces[0].type == space_type:
				for region in area.regions:
					if region.type == region_type:
						region.tag_redraw()

class OBJECT_PT_MeshObjectModePanel(Panel):
	bl_label = "RE Mesh Tools"
	bl_idname = "OBJECT_PT_mesh_tools_panel"
	bl_space_type = "VIEW_3D"   
	bl_region_type = "UI"
	bl_category = "RE Mesh"   
	bl_context = "objectmode"
	bl_options = {'DEFAULT_CLOSED'}

	@classmethod
	def poll(self,context):
		return context is not None and "HIDE_RE_MDF_EDITOR_TAB" not in context.scene

	def draw(self, context):
		layout = self.layout
		scene = context.scene
		re_mdf_toolpanel = scene.re_mdf_toolpanel
		layout.operator("re_mesh.create_mesh_collection",icon = "COLLECTION_NEW")
		layout.operator("re_mesh.rename_meshes",icon = "MOD_LINEART")
		layout.operator("re_mesh.delete_loose",icon = "SNAP_VERTEX")
		layout.operator("re_mesh.solve_repeated_uvs",icon = "UV")
		layout.operator("re_mesh.remove_zero_weight_vertex_groups",icon = "MESH_DATA")
		layout.operator("re_mesh.limit_total_normalize",icon = "MOD_VERTEX_WEIGHT")
		layout.operator("re_mesh.batch_exporter",icon = "OUTLINER_OB_GROUP_INSTANCE")
		#layout.operator("re_mesh.quick_batch_export",icon = "OUTLINER_OB_GROUP_INSTANCE")#TODO FIX
		
class OBJECT_PT_SF6ShapeTransferPanel(Panel):
	"""Transfer original SF6 corrective shapes onto added body geometry."""

	bl_label = "SF6 Shape Transfer"
	bl_idname = "OBJECT_PT_sf6_shape_transfer_panel"
	bl_parent_id = "OBJECT_PT_mesh_tools_panel"
	bl_space_type = "VIEW_3D"
	bl_region_type = "UI"
	bl_category = "RE Mesh"
	bl_options = {'DEFAULT_CLOSED'}

	def draw(self, context):
		layout = self.layout
		settings = context.scene.sf6_shape_transfer_settings
		layout.prop(settings, "donor")
		layout.prop(settings, "target")
		layout.prop(settings, "leg_only")
		layout.prop(settings, "max_distance")
		layout.label(text="Use bodies imported with SF6 Preserve Source.", icon='INFO')
		layout.label(text="Existing retail shape vertices stay unchanged.")
		layout.label(text="Export with SF6 Hybrid Shape Export (LOD0).")
		row = layout.row(align=True)
		row.enabled = bool(settings.donor and settings.target and settings.donor != settings.target)
		row.operator("re_mesh.preview_sf6_shape_transfer", text="Preview", icon='INFO')
		row.operator("re_mesh.transfer_sf6_shape_keys", text="Transfer", icon='SHAPEKEY_DATA')

class OBJECT_PT_MeshArmatureToolsPanel(Panel):
	bl_label = "Armature Tools"
	bl_idname = "OBJECT_PT_mesh_armature_tools_panel"
	bl_parent_id = "OBJECT_PT_mesh_tools_panel"  # Specify the ID of the parent panel
	bl_space_type = "VIEW_3D"   
	bl_region_type = "UI"
	bl_category = "RE Mesh"   
	bl_options = {'DEFAULT_CLOSED'}
	
	def draw(self, context):
		layout = self.layout
		obj = context.active_object
		re_mdf_toolpanel = context.scene.re_mdf_toolpanel
		layout.operator("re_fbxskel.link_armature_bones")
		layout.operator("re_fbxskel.clear_bone_linkages")	
class OBJECT_PT_REAssetExtensionPanel(Panel):
	bl_label = "RE Asset Extensions"
	bl_idname = "OBJECT_PT_re_asset_extension_panel"
	bl_space_type = "VIEW_3D"  
	bl_region_type = "UI"
	bl_category = "RE Mesh"   
	bl_context = "objectmode"

	@classmethod
	def poll(self,context):
		return context is not None and "HIDE_RE_MDF_EDITOR_TAB" not in context.scene

	def draw(self, context):
		layout = self.layout
		scene = context.scene
		if hasattr(bpy.types, "OBJECT_PT_re_pak_panel"):
			try:
				layout.operator("re_asset.create_pak_patch")
			except:
				pass
		if hasattr(bpy.types, "RE_ASSET_OT_unpack_mod_pak"):
			try:
				layout.operator("re_asset.unpack_mod_pak")
			except:
				pass
			
		if hasattr(bpy.types, "RE_ASSET_OT_batch_mdf_updater"):
			
			try:
				layout.operator("re_asset.blender_mdf_updater")
				layout.operator("re_asset.batch_mdf_updater")
			except:
				pass
		
		if hasattr(bpy.types, "RE_ASSET_OT_batch_rsz_updater"):
			try:
				layout.operator("re_asset.batch_rsz_updater")
			except:
				pass
		else:
			layout.label(text="Update RE Asset Library for more options.")
