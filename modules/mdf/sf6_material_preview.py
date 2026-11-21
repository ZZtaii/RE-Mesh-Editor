#Street Fighter 6 material preview: opacity, glass, sheer cloth, hair, glow and colour slots.
#
#The formulas follow the game's own shader code, read from the DXIL inside the master
#material files (.mmtr) of the cloth, sheer cloth, glass (blended and dithered), glow,
#hair and eyelash shaders.
#The game cuts pixels with a dither pattern that changes every frame; here the share of
#pixels that survive becomes the alpha of a dithered Blender material.
#Not modelled: the fake reflection that makes glass more solid, the weave pattern of
#sheer cloth, damage, and every effect the game drives while playing.
#
#Each formula lives in a node group shared by all materials, so a material only gets
#its property nodes and one group node per effect.
import bpy
from mathutils import Vector

from .blender_nodes_re_mdf import addPropertyNode,EMISSION_MULTIPLIER

#Node that carries the material's alpha test flag, so the MDF panel can switch it live
ALPHA_TEST_FLAG_NODE = "MDF_BaseAlphaTestEnable"

#Shader type 14 (Forward): SF6's "ExTransparent" materials (glasses lenses, watch glass). Real blending.
SF6_BLENDED_SHADER_TYPES = set([14])

#Part of the node group names. Raise it whenever a group's sockets or maths change,
#so files saved with an older build keep their own groups.
GROUP_VERSION = "v1"

#Properties this file turns into nodes. The MDF panel updates these in the preview while editing.
SF6_LIVE_PROPS = set([
	"AlphaTestRef","AlphaTest_DissolveRate","AlphaValue",
	"SeeThrough_EdgeAlpha","SeeThrough_EdgePow",
	"Glass_Alpha","Glass_EdgeAlpha","Glass_EdgePow","Glass_EdgeOffset",
	"DissolveThered","DissolveRange","Dissolve_Pow","EdgeAlphaMask_Rate","EdgeAlphaMask_Range",
	"Emissive_Intensity","EmissiveMask_Pow","Emissive_Offset",
	] + ["CustomizeColor_%d_BlendRate" % index for index in range(8)])

FLOAT = "NodeSocketFloat"
COLOR = "NodeSocketColor"

def isFlagSet(flags,flagName):
	#One bit signed fields read as -1 when set
	return getattr(flags,flagName,0) != 0

def _prop(matInfo,nodeTree,propName):
	#Kept on two lines: the addon scans its source for addPropertyNode calls with a literal property name
	prop = matInfo["mPropDict"][propName]
	return addPropertyNode(prop, matInfo["currentPropPos"], nodeTree).outputs["Value"]

def _math(nodeTree,operation,a = None,b = None,c = None,clamp = False):
	node = nodeTree.nodes.new("ShaderNodeMath")
	node.operation = operation
	node.use_clamp = clamp
	node.location = (200*len(nodeTree.nodes),0)
	for index,value in enumerate((a,b,c)):
		if value is None:
			continue
		if isinstance(value,(int,float)):
			node.inputs[index].default_value = value
		else:
			nodeTree.links.new(value,node.inputs[index])
	return node.outputs["Value"]

def _newGroup(label,inputs,outputs):
	"""Returns (tree, group input sockets, group output sockets). The sockets are None when the group already exists."""
	name = "SF6 %s %s" % (label,GROUP_VERSION)
	tree = bpy.data.node_groups.get(name)
	if tree != None:
		return tree,None,None
	tree = bpy.data.node_groups.new(name,"ShaderNodeTree")
	for socketName,socketType,default in inputs:
		socket = tree.interface.new_socket(name = socketName,in_out = "INPUT",socket_type = socketType)
		socket.default_value = default
	for socketName,socketType in outputs:
		tree.interface.new_socket(name = socketName,in_out = "OUTPUT",socket_type = socketType)
	groupIn = tree.nodes.new("NodeGroupInput")
	groupOut = tree.nodes.new("NodeGroupOutput")
	groupOut.location = (6000,0)
	return tree,groupIn.outputs,groupOut.inputs

def _buildGroup(label,inputs,outputs,build):
	tree,groupIn,groupOut = _newGroup(label,inputs,outputs)
	if groupIn != None:
		try:
			build(tree,groupIn,groupOut)
		except Exception:
			bpy.data.node_groups.remove(tree)#Never leave a half built group behind under the shared name
			raise
	return tree

def _instance(nodeTree,tree,location,inputs):
	node = nodeTree.nodes.new("ShaderNodeGroup")
	node.node_tree = tree
	node.label = tree.name
	node.location = location
	for socketName,value in inputs.items():
		if value is None:
			continue
		if isinstance(value,(int,float)):
			node.inputs[socketName].default_value = value
		else:
			nodeTree.links.new(value,node.inputs[socketName])
	return node

def _facing(tree):
	#0 where the surface faces the camera, 1 at grazing angles: 1 - |N.V|
	node = tree.nodes.new("ShaderNodeLayerWeight")
	node.inputs["Blend"].default_value = 0.5
	return node.outputs["Facing"]

def _ditherShare(tree,alpha):
	#The game keeps a pixel when alpha - 1 + d > 0, d spread evenly over 0 to 5/6.
	#Share of pixels kept: 1.2 * alpha - 0.2, between 0 and 1.
	return _math(tree,"MULTIPLY_ADD",alpha,1.2,-0.2,clamp = True)

def _fadeAndGate(tree,opacity,alphaValue,flag):
	#AlphaValue below 1 fades the whole material with the same dither
	if alphaValue is not None:
		opacity = _math(tree,"MULTIPLY",opacity,_ditherShare(tree,alphaValue))
	#Without the alpha test flag the game uses the shader variants that never cut a pixel
	cutAway = _math(tree,"SUBTRACT",1.0,opacity)
	cutAway = _math(tree,"MULTIPLY",cutAway,flag)
	return _math(tree,"SUBTRACT",1.0,cutAway,clamp = True)

COMMON_INPUTS = [("AlphaValue",FLOAT,1.0),("Alpha Test Flag",FLOAT,0.0)]

def _clothOpacityGroup():
	#Cloth family. Kept when (alpha - ref) - rate * d > 0, d one of 0, 0.25, 0.5, 0.75, 1.
	def build(tree,groupIn,groupOut):
		above = _math(tree,"SUBTRACT",groupIn["Alpha"],groupIn["AlphaTestRef"])
		width = _math(tree,"MAXIMUM",groupIn["AlphaTest_DissolveRate"],0.0001)
		steps = _math(tree,"MULTIPLY",_math(tree,"DIVIDE",above,width),4.0)
		share = _math(tree,"DIVIDE",_math(tree,"CEIL",steps),5.0,clamp = True)
		#alpha - ref of 1 or more skips the dither
		solid = _math(tree,"GREATER_THAN",above,0.9999)
		opacity = _math(tree,"MAXIMUM",share,solid)
		tree.links.new(_fadeAndGate(tree,opacity,groupIn["AlphaValue"],groupIn["Alpha Test Flag"]),groupOut["Opacity"])
	return _buildGroup("Cloth Opacity",[("Alpha",FLOAT,1.0),("AlphaTestRef",FLOAT,0.5),("AlphaTest_DissolveRate",FLOAT,0.0)] + COMMON_INPUTS,[("Opacity",FLOAT)],build)

def _sheerOpacityGroup():
	#Sheer cloth: alpha rises to alpha + SeeThrough_EdgeAlpha towards the edges, then is dithered.
	#The blue channel of the same texture marks where the cloth is sheer at all.
	def build(tree,groupIn,groupOut):
		alpha = groupIn["Alpha"]
		edge = _math(tree,"POWER",_facing(tree),groupIn["SeeThrough_EdgePow"])
		edgeTarget = _math(tree,"ADD",alpha,groupIn["SeeThrough_EdgeAlpha"],clamp = True)
		sheerAlpha = _math(tree,"MULTIPLY_ADD",_math(tree,"SUBTRACT",edgeTarget,alpha),edge,alpha)
		opacity = _math(tree,"MULTIPLY",_ditherShare(tree,sheerAlpha),_math(tree,"GREATER_THAN",alpha,0.0005))
		hidden = _math(tree,"MULTIPLY",_math(tree,"SUBTRACT",1.0,opacity),groupIn["Sheer Mask"])
		opacity = _math(tree,"SUBTRACT",1.0,hidden,clamp = True)
		tree.links.new(_fadeAndGate(tree,opacity,groupIn["AlphaValue"],groupIn["Alpha Test Flag"]),groupOut["Opacity"])
	return _buildGroup("Sheer Opacity",[("Alpha",FLOAT,1.0),("Sheer Mask",FLOAT,1.0),("SeeThrough_EdgeAlpha",FLOAT,0.0),("SeeThrough_EdgePow",FLOAT,1.0)] + COMMON_INPUTS,[("Opacity",FLOAT)],build)

def _hairOpacityGroup():
	#Hair and eyelashes: alpha (less AlphaTestRef where the shader has one) is spread between
	#DissolveThered and DissolveRange, faded at the card edges, times AlphaValue, then dithered
	def build(tree,groupIn,groupOut):
		low = groupIn["DissolveThered"]
		span = _math(tree,"SUBTRACT",groupIn["DissolveRange"],low)
		sign = _math(tree,"MULTIPLY_ADD",_math(tree,"LESS_THAN",span,0.0),-2.0,1.0)
		span = _math(tree,"MULTIPLY",_math(tree,"MAXIMUM",_math(tree,"ABSOLUTE",span),0.0001),sign)
		above = _math(tree,"SUBTRACT",_math(tree,"SUBTRACT",groupIn["Alpha"],groupIn["AlphaTestRef"]),low)
		spread = _math(tree,"DIVIDE",above,span,clamp = True)
		soft = _math(tree,"MULTIPLY",spread,2.0,clamp = True)
		hard = _math(tree,"MULTIPLY_ADD",spread,2.0,-1.0,clamp = True)
		shaped = _math(tree,"MULTIPLY_ADD",_math(tree,"SUBTRACT",soft,hard),groupIn["Dissolve_Pow"],hard)
		towardCamera = _math(tree,"SUBTRACT",1.0,_facing(tree))
		inner = _math(tree,"SUBTRACT",_math(tree,"MULTIPLY",towardCamera,groupIn["EdgeAlphaMask_Range"],clamp = True),1.0)
		edgeFade = _math(tree,"MULTIPLY_ADD",inner,groupIn["EdgeAlphaMask_Rate"],1.0)
		opacity = _math(tree,"MULTIPLY",_math(tree,"MULTIPLY",shaped,edgeFade),groupIn["AlphaValue"])
		tree.links.new(_fadeAndGate(tree,_ditherShare(tree,opacity),None,groupIn["Alpha Test Flag"]),groupOut["Opacity"])
	return _buildGroup("Hair Opacity",[("Alpha",FLOAT,1.0),("AlphaTestRef",FLOAT,0.0),("DissolveThered",FLOAT,0.0),("DissolveRange",FLOAT,1.0),("Dissolve_Pow",FLOAT,0.5),("EdgeAlphaMask_Rate",FLOAT,0.0),("EdgeAlphaMask_Range",FLOAT,1.0)] + COMMON_INPUTS,[("Opacity",FLOAT)],build)

def _otherOpacityGroup():
	#Shaders that were not read are assumed to dither by the texture alpha like hair and sheer cloth do
	def build(tree,groupIn,groupOut):
		opacity = _ditherShare(tree,groupIn["Alpha"])
		tree.links.new(_fadeAndGate(tree,opacity,groupIn["AlphaValue"],groupIn["Alpha Test Flag"]),groupOut["Opacity"])
	return _buildGroup("Dithered Opacity",[("Alpha",FLOAT,1.0)] + COMMON_INPUTS,[("Opacity",FLOAT)],build)

def _blendedGlassGroup():
	#Blended glass: Glass_Alpha in the middle, Glass_EdgeAlpha at the edges, edge = 1.25 * (1 - N.V) ^ Glass_EdgePow - 0.25
	def build(tree,groupIn,groupOut):
		edge = _math(tree,"POWER",_facing(tree),groupIn["Glass_EdgePow"])
		edge = _math(tree,"MULTIPLY_ADD",edge,1.25,-0.25,clamp = True)
		gain = _math(tree,"SUBTRACT",groupIn["Glass_EdgeAlpha"],groupIn["Glass_Alpha"])
		glass = _math(tree,"MULTIPLY_ADD",gain,edge,groupIn["Glass_Alpha"],clamp = True)
		tree.links.new(_math(tree,"MULTIPLY",groupIn["Opacity"],glass),groupOut["Opacity"])
	return _buildGroup("Blended Glass",[("Opacity",FLOAT,1.0),("Glass_Alpha",FLOAT,0.0),("Glass_EdgeAlpha",FLOAT,1.0),("Glass_EdgePow",FLOAT,1.0)],[("Opacity",FLOAT)],build)

def _ditheredGlassGroup():
	#Glass that is not blended: the same two values, edge = smoothstep between 0.25 * (1 - Glass_EdgeOffset)
	#and 1 - Glass_EdgeOffset. The result is the share of pixels kept. The alpha texture plays no part.
	def build(tree,groupIn,groupOut):
		edge = _math(tree,"POWER",_facing(tree),groupIn["Glass_EdgePow"])
		high = _math(tree,"SUBTRACT",1.0,groupIn["Glass_EdgeOffset"])
		low = _math(tree,"MULTIPLY",high,0.25)
		width = _math(tree,"MAXIMUM",_math(tree,"SUBTRACT",high,low),0.0001)
		step = _math(tree,"DIVIDE",_math(tree,"SUBTRACT",edge,low),width,clamp = True)
		smooth = _math(tree,"MULTIPLY",_math(tree,"MULTIPLY",step,step),_math(tree,"MULTIPLY_ADD",step,-2.0,3.0))
		gain = _math(tree,"SUBTRACT",groupIn["Glass_EdgeAlpha"],groupIn["Glass_Alpha"])
		opacity = _math(tree,"MULTIPLY_ADD",gain,smooth,groupIn["Glass_Alpha"],clamp = True)
		tree.links.new(_fadeAndGate(tree,opacity,groupIn["AlphaValue"],groupIn["Alpha Test Flag"]),groupOut["Opacity"])
	return _buildGroup("Dithered Glass",[("Glass_Alpha",FLOAT,0.0),("Glass_EdgeAlpha",FLOAT,1.0),("Glass_EdgePow",FLOAT,1.0),("Glass_EdgeOffset",FLOAT,0.0)] + COMMON_INPUTS,[("Opacity",FLOAT)],build)

def _colorSlotGroup():
	#Tint = twice the slot colour. Exactly white in the panel stands for "no change".
	def build(tree,groupIn,groupOut):
		doubleNode = tree.nodes.new("ShaderNodeMixRGB")
		doubleNode.blend_type = "ADD"
		doubleNode.inputs["Fac"].default_value = 1.0
		tree.links.new(groupIn["Color"],doubleNode.inputs["Color1"])
		tree.links.new(groupIn["Color"],doubleNode.inputs["Color2"])
		partsNode = tree.nodes.new("ShaderNodeSeparateColor")
		tree.links.new(groupIn["Color"],partsNode.inputs["Color"])
		red,green,blue = partsNode.outputs["Red"],partsNode.outputs["Green"],partsNode.outputs["Blue"]
		lowest = _math(tree,"MINIMUM",_math(tree,"MINIMUM",red,green),blue)
		highest = _math(tree,"MAXIMUM",_math(tree,"MAXIMUM",red,green),blue)
		isWhite = _math(tree,"MULTIPLY",_math(tree,"GREATER_THAN",lowest,0.9999),_math(tree,"LESS_THAN",highest,1.0001))
		tintNode = tree.nodes.new("ShaderNodeMixRGB")
		tintNode.blend_type = "MIX"
		tree.links.new(isWhite,tintNode.inputs["Fac"])
		tree.links.new(doubleNode.outputs["Color"],tintNode.inputs["Color1"])
		tintNode.inputs["Color2"].default_value = (1.0,1.0,1.0,1.0)
		tree.links.new(tintNode.outputs["Color"],groupOut["Tint"])
		tree.links.new(_math(tree,"MULTIPLY",groupIn["Mask"],groupIn["Blend Rate"],clamp = True),groupOut["Factor"])
	return _buildGroup("Colour Slot",[("Color",COLOR,(0.5,0.5,0.5,1.0)),("Mask",FLOAT,0.0),("Blend Rate",FLOAT,1.0)],[("Tint",COLOR),("Factor",FLOAT)],build)

def _valueSlotGroup():
	#Base + mask x value. Exactly 1 in the panel stands for "nothing added".
	def build(tree,groupIn,groupOut):
		compareNode = tree.nodes.new("ShaderNodeMath")
		compareNode.operation = "COMPARE"
		tree.links.new(groupIn["Value"],compareNode.inputs[0])
		compareNode.inputs[1].default_value = 1.0
		compareNode.inputs[2].default_value = 0.0001
		value = _math(tree,"MULTIPLY",groupIn["Value"],_math(tree,"SUBTRACT",1.0,compareNode.outputs["Value"]))
		tree.links.new(_math(tree,"MULTIPLY_ADD",groupIn["Mask"],value,groupIn["Base"]),groupOut["Result"])
	return _buildGroup("Value Slot",[("Base",FLOAT,0.0),("Mask",FLOAT,0.0),("Value",FLOAT,0.0)],[("Result",FLOAT)],build)

def _glowGroup():
	#(mask red ^ EmissiveMask_Pow + Emissive_Offset) x Emissive_Intensity
	def build(tree,groupIn,groupOut):
		partsNode = tree.nodes.new("ShaderNodeSeparateColor")
		tree.links.new(groupIn["Mask"],partsNode.inputs["Color"])
		strength = _math(tree,"POWER",partsNode.outputs["Red"],groupIn["EmissiveMask_Pow"])
		strength = _math(tree,"ADD",strength,groupIn["Emissive_Offset"])
		strength = _math(tree,"MULTIPLY",strength,groupIn["Emissive_Intensity"])
		tree.links.new(_math(tree,"MULTIPLY",strength,EMISSION_MULTIPLIER),groupOut["Strength"])
	return _buildGroup("Glow",[("Mask",COLOR,(0.0,0.0,0.0,1.0)),("EmissiveMask_Pow",FLOAT,1.0),("Emissive_Offset",FLOAT,0.0),("Emissive_Intensity",FLOAT,0.0)],[("Strength",FLOAT)],build)

def _props(matInfo,nodeTree,propNames):
	"""Property name -> node socket for the names the material has. Missing ones keep the group's default."""
	return {propName:_prop(matInfo,nodeTree,propName) for propName in propNames if propName in matInfo["mPropDict"]}

def setupSF6ColorMask(nodeTree,matInfo,imageNode,separateNode):
	"""Colour slots: each mask channel (red, green, blue, alpha) tints the surface with
	twice its CustomizeColor, so 0.5 grey leaves the colour unchanged.
	CustomizeRoughness and CustomizeMetal are added to roughness and metal, not multiplied.

	The MDF panel shows the game's "no change" values (0.5 grey, 0) as white and 1, and the
	export writes white and 1 back as 0.5 grey and 0. The preview follows the same rule,
	so it shows what the exported file will do."""
	props = matInfo["mPropDict"]
	channels = [separateNode.outputs["Red"],separateNode.outputs["Green"],separateNode.outputs["Blue"],imageNode.outputs["Alpha"]]
	firstSlot = 0 if not matInfo.get("sf6ColorMaskDone",False) else 4
	matInfo["sf6ColorMaskDone"] = True
	pos = Vector(imageNode.location) + Vector((600,0))
	for offset,maskSocket in enumerate(channels):
		slot = firstSlot + offset
		colorName = "CustomizeColor_%d" % slot
		if colorName in props:
			colorNode = addPropertyNode(props[colorName], matInfo["currentPropPos"], nodeTree)
			inputs = {"Color":colorNode.outputs["Color"],"Mask":maskSocket}
			if colorName + "_BlendRate" in props:
				inputs["Blend Rate"] = _prop(matInfo,nodeTree,colorName + "_BlendRate")
			slotNode = _instance(nodeTree,_colorSlotGroup(),pos + Vector((0,-200*offset)),inputs)
			matInfo["albedoNodeLayerGroup"].addMixLayer(slotNode.outputs["Tint"],slotNode.outputs["Factor"],mixType = "MULTIPLY")
		for kind in ("Roughness","Metal"):
			propName = "Customize%s_%d" % (kind,slot)
			if propName in props:
				valueNode = addPropertyNode(props[propName], matInfo["currentPropPos"], nodeTree)
				matInfo.setdefault("sf6%sAdd" % kind,[]).append((maskSocket,valueNode.outputs["Value"]))
	return imageNode

def applySF6CustomizeAdd(nodeTree,targetSocket,additions):
	"""Adds mask x value pairs to whatever feeds targetSocket (the roughness or metal clamp)."""
	if not additions or not targetSocket.links:
		return
	current = targetSocket.links[0].from_socket
	pos = Vector(targetSocket.node.location) + Vector((-250*len(additions),-250))
	for index,(maskSocket,valueSocket) in enumerate(additions):
		slotNode = _instance(nodeTree,_valueSlotGroup(),pos + Vector((250*index,0)),{"Base":current,"Mask":maskSocket,"Value":valueSocket})
		current = slotNode.outputs["Result"]
	nodeTree.links.new(current,targetSocket)

def setupSF6Emission(matInfo,nodeTree):
	"""Glow of the ClothEmi / DefaultEmi shader families:
	surface colour x Emissive_Intensity x (mask red ^ EmissiveMask_Pow + Emissive_Offset).
	The brightness scale of Blender against the game is not known."""
	props = matInfo["mPropDict"]
	if "EmissiveMaskMap" not in matInfo["textureNodeDict"] or "Emissive_Intensity" not in props:
		return False
	if "eye" in matInfo["mmtrName"]:
		return False#The eye shaders were not read
	maskNode = matInfo["textureNodeDict"]["EmissiveMaskMap"]
	if "Color" not in maskNode.outputs:
		return False
	inputs = _props(matInfo,nodeTree,("EmissiveMask_Pow","Emissive_Offset","Emissive_Intensity"))
	inputs["Mask"] = maskNode.outputs["Color"]
	glowNode = _instance(nodeTree,_glowGroup(),Vector(maskNode.location) + Vector((300,0)),inputs)
	if matInfo["albedoNodeLayerGroup"].currentOutSocket != None:
		matInfo["emissionColorNodeLayerGroup"].addMixLayer(matInfo["albedoNodeLayerGroup"].currentOutSocket)
	matInfo["emissionStrengthNodeLayerGroup"].addMixLayer(glowNode.outputs["Strength"])
	return True

def setupSF6Opacity(matInfo,nodeBSDF,nodeTree):
	"""Builds the opacity of an SF6 material and links it to the shader.
	Returns False when the material is left to the general code (eye shadow and other blended shader types)."""
	if matInfo["isAlphaBlend"]:
		return False
	props = matInfo["mPropDict"]
	flags = matInfo["flags"]
	pos = Vector(nodeBSDF.location) + Vector((-600,-500))
	isBlended = matInfo["shaderType"] in SF6_BLENDED_SHADER_TYPES
	isGlass = "Glass_Alpha" in props and "Glass_EdgeAlpha" in props
	isSheer = "SeeThrough_EdgeAlpha" in props
	isHair = "DissolveThered" in props and "DissolveRange" in props

	if isFlagSet(flags,"ShadowCastDisable"):
		matInfo["disableShadowCast"] = True
	#Alpha of the texture: ATOS red channel, or none (null texture) = fully solid
	alpha = matInfo["alphaSocket"]
	if alpha == None and not isGlass and not isSheer and not isHair and "AlphaTestRef" not in props and "AlphaValue" not in props:
		#Nothing here can make the material see-through
		return True

	flagNode = nodeTree.nodes.new("ShaderNodeValue")
	flagNode.name = ALPHA_TEST_FLAG_NODE
	flagNode.label = "Alpha Test flag"
	flagNode.location = pos + Vector((-300,200))
	flagNode.outputs["Value"].default_value = 1.0 if isFlagSet(flags,"BaseAlphaTestEnable") else 0.0
	common = {"Alpha Test Flag":flagNode.outputs["Value"]}
	common.update(_props(matInfo,nodeTree,("AlphaValue",)))

	if isHair:
		inputs = _props(matInfo,nodeTree,("AlphaTestRef","DissolveThered","DissolveRange","Dissolve_Pow","EdgeAlphaMask_Rate","EdgeAlphaMask_Range"))
		group = _hairOpacityGroup()
	elif isSheer:
		inputs = _props(matInfo,nodeTree,("SeeThrough_EdgeAlpha","SeeThrough_EdgePow"))
		if alpha != None and alpha.node.type == "SEPARATE_COLOR":
			inputs["Sheer Mask"] = alpha.node.outputs["Blue"]
		group = _sheerOpacityGroup()
	elif isGlass and not isBlended and "Glass_EdgeOffset" in props:
		inputs = _props(matInfo,nodeTree,("Glass_Alpha","Glass_EdgeAlpha","Glass_EdgePow","Glass_EdgeOffset"))
		alpha = None
		group = _ditheredGlassGroup()
	elif "AlphaTestRef" in props:
		inputs = _props(matInfo,nodeTree,("AlphaTestRef","AlphaTest_DissolveRate"))
		group = _clothOpacityGroup()
	else:
		inputs = {}
		group = _otherOpacityGroup()
	inputs.update(common)
	if alpha != None:
		inputs["Alpha"] = alpha
	opacity = _instance(nodeTree,group,pos,inputs).outputs["Opacity"]

	if isGlass and (isBlended or "Glass_EdgeOffset" not in props):
		inputs = _props(matInfo,nodeTree,("Glass_Alpha","Glass_EdgeAlpha","Glass_EdgePow"))
		inputs["Opacity"] = opacity
		opacity = _instance(nodeTree,_blendedGlassGroup(),pos + Vector((300,0)),inputs).outputs["Opacity"]

	nodeTree.links.new(opacity,nodeBSDF.inputs["Alpha"])

	if isBlended:
		matInfo["blenderMaterial"].surface_render_method = "BLENDED"
		matInfo["blenderMaterial"].use_transparent_shadow = True
		matInfo["disableShadowCast"] = True
	return True

def syncSF6FlagPreview(materialObj):
	"""Called when a flag changes in the MDF panel: updates the linked preview material."""
	material = materialObj.re_mdf_material.linkedMaterial
	if material == None or material.node_tree == None:
		return
	flagNode = material.node_tree.nodes.get(ALPHA_TEST_FLAG_NODE,None)
	if flagNode != None:
		flagNode.outputs["Value"].default_value = 1.0 if materialObj.re_mdf_material.flags.BaseAlphaTestEnable else 0.0
