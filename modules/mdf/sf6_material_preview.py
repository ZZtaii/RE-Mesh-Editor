#Street Fighter 6 material preview: opacity, glass, sheer cloth, hair, glow and colour slots.
#
#The formulas follow the game's own shader code, read from the DXIL inside the master
#material files (.mmtr) of the cloth, sheer cloth, glass, glow and hair shaders.
#The game cuts pixels with a dither pattern that changes every frame; here the share of
#pixels that survive becomes the alpha of a dithered Blender material.
#Not modelled: the fake reflection that makes glass more solid, the weave pattern of
#sheer cloth, damage, and every effect the game drives while playing.
import bpy
from mathutils import Vector

from .blender_nodes_re_mdf import addPropertyNode,EMISSION_MULTIPLIER

#Node that carries the material's alpha test flag, so the MDF panel can switch it live
ALPHA_TEST_FLAG_NODE = "MDF_BaseAlphaTestEnable"

#Shader type 14 (Forward): SF6's "ExTransparent" materials (glasses lenses, watch glass). Real blending.
SF6_BLENDED_SHADER_TYPES = set([14])

#Properties this file turns into nodes. The MDF panel updates these in the preview while editing.
SF6_LIVE_PROPS = set([
	"AlphaTestRef","AlphaTest_DissolveRate","AlphaValue",
	"SeeThrough_EdgeAlpha","SeeThrough_EdgePow",
	"Glass_Alpha","Glass_EdgeAlpha","Glass_EdgePow",
	"DissolveThered","DissolveRange","Dissolve_Pow","EdgeAlphaMask_Rate","EdgeAlphaMask_Range",
	"Emissive_Intensity","EmissiveMask_Pow","Emissive_Offset",
	] + ["CustomizeColor_%d_BlendRate" % index for index in range(8)])

def isFlagSet(flags,flagName):
	#One bit signed fields read as -1 when set
	return getattr(flags,flagName,0) != 0

def _prop(matInfo,nodeTree,propName):
	#Kept on two lines: the addon scans its source for addPropertyNode calls with a literal property name
	prop = matInfo["mPropDict"][propName]
	return addPropertyNode(prop, matInfo["currentPropPos"], nodeTree).outputs["Value"]

def _math(nodeTree,operation,location,a = None,b = None,c = None,clamp = False):
	node = nodeTree.nodes.new("ShaderNodeMath")
	node.operation = operation
	node.use_clamp = clamp
	node.location = location
	for index,value in enumerate((a,b,c)):
		if value is None:
			continue
		if isinstance(value,(int,float)):
			node.inputs[index].default_value = value
		else:
			nodeTree.links.new(value,node.inputs[index])
	return node.outputs["Value"]

def _facing(nodeTree,location):
	#0 where the surface faces the camera, 1 at grazing angles: 1 - |N.V|
	node = nodeTree.nodes.get("SF6FacingNode")
	if node == None:
		node = nodeTree.nodes.new("ShaderNodeLayerWeight")
		node.name = "SF6FacingNode"
		node.label = "Edge (view angle)"
		node.inputs["Blend"].default_value = 0.5
		node.location = location
	return node.outputs["Facing"]

def _ditherShare(nodeTree,location,alpha):
	#The game keeps a pixel when alpha - 1 + d > 0, d spread evenly over 0 to 5/6.
	#Share of pixels kept: 1.2 * alpha - 0.2, between 0 and 1.
	return _math(nodeTree,"MULTIPLY_ADD",location,alpha,1.2,-0.2,clamp = True)

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
		rowPos = pos + Vector((0,-300*offset))
		colorName = "CustomizeColor_%d" % slot
		if colorName in props:
			colorNode = addPropertyNode(props[colorName], matInfo["currentPropPos"], nodeTree)
			doubleNode = nodeTree.nodes.new("ShaderNodeMixRGB")
			doubleNode.blend_type = "ADD"
			doubleNode.inputs["Fac"].default_value = 1.0
			doubleNode.location = rowPos
			nodeTree.links.new(colorNode.outputs["Color"],doubleNode.inputs["Color1"])
			nodeTree.links.new(colorNode.outputs["Color"],doubleNode.inputs["Color2"])
			#White in the panel stands for "no change"
			partsNode = nodeTree.nodes.new("ShaderNodeSeparateColor")
			partsNode.location = rowPos + Vector((0,-150))
			nodeTree.links.new(colorNode.outputs["Color"],partsNode.inputs["Color"])
			lowest = _math(nodeTree,"MINIMUM",rowPos + Vector((200,-150)),partsNode.outputs["Red"],partsNode.outputs["Green"])
			lowest = _math(nodeTree,"MINIMUM",rowPos + Vector((400,-150)),lowest,partsNode.outputs["Blue"])
			isWhite = _math(nodeTree,"GREATER_THAN",rowPos + Vector((600,-150)),lowest,0.9999)
			tintNode = nodeTree.nodes.new("ShaderNodeMixRGB")
			tintNode.blend_type = "MIX"
			tintNode.location = rowPos + Vector((800,0))
			nodeTree.links.new(isWhite,tintNode.inputs["Fac"])
			nodeTree.links.new(doubleNode.outputs["Color"],tintNode.inputs["Color1"])
			tintNode.inputs["Color2"].default_value = (1.0,1.0,1.0,1.0)
			factor = maskSocket
			if colorName + "_BlendRate" in props:
				factor = _math(nodeTree,"MULTIPLY",rowPos + Vector((1000,0)),maskSocket,_prop(matInfo,nodeTree,colorName + "_BlendRate"),clamp = True)
			matInfo["albedoNodeLayerGroup"].addMixLayer(tintNode.outputs["Color"],factor,mixType = "MULTIPLY")
		for kindIndex,kind in enumerate(("Roughness","Metal")):
			propName = "Customize%s_%d" % (kind,slot)
			if propName in props:
				valueNode = addPropertyNode(props[propName], matInfo["currentPropPos"], nodeTree)
				#1 in the panel stands for "nothing added"
				isSet = _math(nodeTree,"LESS_THAN",rowPos + Vector((1200 + 400*kindIndex,-150)),valueNode.outputs["Value"],0.9999)
				value = _math(nodeTree,"MULTIPLY",rowPos + Vector((1400 + 400*kindIndex,-150)),valueNode.outputs["Value"],isSet)
				matInfo.setdefault("sf6%sAdd" % kind,[]).append((maskSocket,value))
	return imageNode

def applySF6CustomizeAdd(nodeTree,targetSocket,additions):
	"""Adds mask x value pairs to whatever feeds targetSocket (the roughness or metal clamp)."""
	if not additions or not targetSocket.links:
		return
	current = targetSocket.links[0].from_socket
	pos = Vector(targetSocket.node.location) + Vector((0,-250))
	for index,(maskSocket,valueSocket) in enumerate(additions):
		part = _math(nodeTree,"MULTIPLY",pos + Vector((-400,-150*index)),maskSocket,valueSocket)
		current = _math(nodeTree,"ADD",pos + Vector((-200,-150*index)),current,part)
	nodeTree.links.new(current,targetSocket)

def setupSF6Emission(matInfo,nodeTree):
	"""Glow of the ClothEmi / DefaultEmi shader families:
	surface colour x Emissive_Intensity x (mask red ^ EmissiveMask_Pow + Emissive_Offset).
	The brightness scale of Blender against the game is not known."""
	props = matInfo["mPropDict"]
	if "EmissiveMaskMap" not in matInfo["textureNodeDict"] or "Emissive_Intensity" not in props:
		return False
	maskNode = matInfo["textureNodeDict"]["EmissiveMaskMap"]
	if "Color" not in maskNode.outputs:
		return False
	pos = Vector(maskNode.location) + Vector((300,0))
	separateNode = nodeTree.nodes.new("ShaderNodeSeparateColor")
	separateNode.location = pos
	nodeTree.links.new(maskNode.outputs["Color"],separateNode.inputs["Color"])
	strength = separateNode.outputs["Red"]
	if "EmissiveMask_Pow" in props:
		strength = _math(nodeTree,"POWER",pos + Vector((300,0)),strength,_prop(matInfo,nodeTree,"EmissiveMask_Pow"))
	if "Emissive_Offset" in props:
		strength = _math(nodeTree,"ADD",pos + Vector((500,0)),strength,_prop(matInfo,nodeTree,"Emissive_Offset"))
	strength = _math(nodeTree,"MULTIPLY",pos + Vector((700,0)),strength,_prop(matInfo,nodeTree,"Emissive_Intensity"))
	strength = _math(nodeTree,"MULTIPLY",pos + Vector((900,0)),strength,EMISSION_MULTIPLIER)
	if matInfo["albedoNodeLayerGroup"].currentOutSocket != None:
		matInfo["emissionColorNodeLayerGroup"].addMixLayer(matInfo["albedoNodeLayerGroup"].currentOutSocket)
	matInfo["emissionStrengthNodeLayerGroup"].addMixLayer(strength)
	return True

def setupSF6Opacity(matInfo,nodeBSDF,nodeTree):
	"""Builds the opacity of an SF6 material and links it to the shader.
	Returns False when the material is left to the general code (eye shadow and other blended shader types)."""
	if matInfo["isAlphaBlend"]:
		return False
	props = matInfo["mPropDict"]
	flags = matInfo["flags"]
	pos = Vector(nodeBSDF.location) + Vector((-2400,-500))
	isBlended = matInfo["shaderType"] in SF6_BLENDED_SHADER_TYPES
	isGlass = "Glass_Alpha" in props and "Glass_EdgeAlpha" in props
	isSheer = "SeeThrough_EdgeAlpha" in props
	isHair = "DissolveThered" in props and "DissolveRange" in props
	flagOn = isFlagSet(flags,"BaseAlphaTestEnable")

	#Alpha of the texture: ATOS red channel, or none (null texture) = fully solid
	alpha = matInfo["alphaSocket"] if matInfo["alphaSocket"] != None else 1.0
	if isinstance(alpha,float) and not isGlass and not isSheer and not flagOn:
		#Nothing here can make the material see-through
		if isFlagSet(flags,"ShadowCastDisable"):
			matInfo["disableShadowCast"] = True
		return True

	alphaValueDone = False
	if "AlphaTestRef" in props:
		#Cloth family. Kept when (alpha - ref) - rate * d > 0, d one of 0, 0.25, 0.5, 0.75, 1.
		above = _math(nodeTree,"SUBTRACT",pos,alpha,_prop(matInfo,nodeTree,"AlphaTestRef"))
		if "AlphaTest_DissolveRate" in props:
			width = _math(nodeTree,"MAXIMUM",pos + Vector((0,-200)),_prop(matInfo,nodeTree,"AlphaTest_DissolveRate"),0.0001)
			ratio = _math(nodeTree,"DIVIDE",pos + Vector((200,0)),above,width)
			steps = _math(nodeTree,"MULTIPLY",pos + Vector((400,0)),ratio,4.0)
			steps = _math(nodeTree,"CEIL",pos + Vector((600,0)),steps)
			share = _math(nodeTree,"DIVIDE",pos + Vector((800,0)),steps,5.0,clamp = True)
			#alpha - ref of 1 or more skips the dither
			solid = _math(nodeTree,"GREATER_THAN",pos + Vector((800,-200)),above,0.9999)
			opacity = _math(nodeTree,"MAXIMUM",pos + Vector((1000,0)),share,solid)
		else:
			opacity = _math(nodeTree,"GREATER_THAN",pos + Vector((1000,0)),above,0.0)
	elif isSheer:
		#Sheer cloth: alpha rises to alpha + SeeThrough_EdgeAlpha towards the edges, then is dithered.
		#The blue channel of the same texture marks where the cloth is sheer at all.
		edge = _facing(nodeTree,pos + Vector((0,-400)))
		if "SeeThrough_EdgePow" in props:
			edge = _math(nodeTree,"POWER",pos + Vector((200,-400)),edge,_prop(matInfo,nodeTree,"SeeThrough_EdgePow"))
		edgeTarget = _math(nodeTree,"ADD",pos + Vector((200,-200)),alpha,_prop(matInfo,nodeTree,"SeeThrough_EdgeAlpha"),clamp = True)
		edgeGain = _math(nodeTree,"SUBTRACT",pos + Vector((400,-200)),edgeTarget,alpha)
		sheerAlpha = _math(nodeTree,"MULTIPLY_ADD",pos + Vector((600,-200)),edgeGain,edge,alpha)
		opacity = _ditherShare(nodeTree,pos + Vector((800,-200)),sheerAlpha)
		present = _math(nodeTree,"GREATER_THAN",pos + Vector((800,0)),alpha,0.0005)
		opacity = _math(nodeTree,"MULTIPLY",pos + Vector((1000,-200)),opacity,present)
		sheerMaskNode = matInfo["alphaSocket"].node if matInfo["alphaSocket"] != None else None
		if sheerMaskNode != None and sheerMaskNode.type == "SEPARATE_COLOR":
			hidden = _math(nodeTree,"SUBTRACT",pos + Vector((1200,-200)),1.0,opacity)
			hidden = _math(nodeTree,"MULTIPLY",pos + Vector((1400,-200)),hidden,sheerMaskNode.outputs["Blue"])
			opacity = _math(nodeTree,"SUBTRACT",pos + Vector((1600,-200)),1.0,hidden,clamp = True)
	elif isHair:
		#Hair: alpha is spread between DissolveThered and DissolveRange, faded at the card edges, then dithered
		low = _prop(matInfo,nodeTree,"DissolveThered")
		span = _math(nodeTree,"SUBTRACT",pos,_prop(matInfo,nodeTree,"DissolveRange"),low)
		span = _math(nodeTree,"MAXIMUM",pos + Vector((200,0)),span,0.0001)
		spread = _math(nodeTree,"SUBTRACT",pos + Vector((200,-200)),alpha,low)
		spread = _math(nodeTree,"DIVIDE",pos + Vector((400,0)),spread,span,clamp = True)
		opacity = spread
		if "Dissolve_Pow" in props:
			soft = _math(nodeTree,"MULTIPLY",pos + Vector((600,0)),spread,2.0,clamp = True)
			hard = _math(nodeTree,"MULTIPLY_ADD",pos + Vector((600,-200)),spread,2.0,-1.0,clamp = True)
			gain = _math(nodeTree,"SUBTRACT",pos + Vector((800,0)),soft,hard)
			opacity = _math(nodeTree,"MULTIPLY_ADD",pos + Vector((1000,0)),gain,_prop(matInfo,nodeTree,"Dissolve_Pow"),hard)
		if "EdgeAlphaMask_Rate" in props and "EdgeAlphaMask_Range" in props:
			towardCamera = _math(nodeTree,"SUBTRACT",pos + Vector((0,-600)),1.0,_facing(nodeTree,pos + Vector((-200,-600))))
			inner = _math(nodeTree,"MULTIPLY",pos + Vector((200,-600)),towardCamera,_prop(matInfo,nodeTree,"EdgeAlphaMask_Range"),clamp = True)
			inner = _math(nodeTree,"SUBTRACT",pos + Vector((400,-600)),inner,1.0)
			edgeFade = _math(nodeTree,"MULTIPLY_ADD",pos + Vector((600,-600)),inner,_prop(matInfo,nodeTree,"EdgeAlphaMask_Rate"),1.0)
			opacity = _math(nodeTree,"MULTIPLY",pos + Vector((1200,0)),opacity,edgeFade)
		if "AlphaValue" in props:
			opacity = _math(nodeTree,"MULTIPLY",pos + Vector((1400,0)),opacity,_prop(matInfo,nodeTree,"AlphaValue"))
			alphaValueDone = True
		opacity = _ditherShare(nodeTree,pos + Vector((1600,0)),opacity)
	else:
		#Other shaders were not read; they are assumed to dither by the texture alpha like hair and sheer cloth do
		opacity = _ditherShare(nodeTree,pos + Vector((1600,0)),alpha)

	if "AlphaValue" in props and not alphaValueDone:
		#AlphaValue below 1 fades the whole material with the same dither
		fade = _ditherShare(nodeTree,pos + Vector((1600,200)),_prop(matInfo,nodeTree,"AlphaValue"))
		opacity = _math(nodeTree,"MULTIPLY",pos + Vector((1800,0)),opacity,fade)

	#Without the alpha test flag the game uses the shader variants that never cut a pixel
	flagNode = nodeTree.nodes.new("ShaderNodeValue")
	flagNode.name = ALPHA_TEST_FLAG_NODE
	flagNode.label = "Alpha Test flag"
	flagNode.location = pos + Vector((1800,200))
	flagNode.outputs["Value"].default_value = 1.0 if flagOn else 0.0
	cutAway = _math(nodeTree,"SUBTRACT",pos + Vector((2000,0)),1.0,opacity)
	cutAway = _math(nodeTree,"MULTIPLY",pos + Vector((2200,0)),cutAway,flagNode.outputs["Value"])
	opacity = _math(nodeTree,"SUBTRACT",pos + Vector((2400,0)),1.0,cutAway,clamp = True)

	if isGlass:
		#Glass: Glass_Alpha in the middle, Glass_EdgeAlpha at the edges, edge = 1.25 * (1 - N.V) ^ Glass_EdgePow - 0.25
		edge = _facing(nodeTree,pos + Vector((0,-900)))
		if "Glass_EdgePow" in props:
			edge = _math(nodeTree,"POWER",pos + Vector((200,-900)),edge,_prop(matInfo,nodeTree,"Glass_EdgePow"))
		edge = _math(nodeTree,"MULTIPLY_ADD",pos + Vector((400,-900)),edge,1.25,-0.25,clamp = True)
		glassAlpha = _prop(matInfo,nodeTree,"Glass_Alpha")
		glassGain = _math(nodeTree,"SUBTRACT",pos + Vector((600,-900)),_prop(matInfo,nodeTree,"Glass_EdgeAlpha"),glassAlpha)
		glass = _math(nodeTree,"MULTIPLY_ADD",pos + Vector((800,-900)),glassGain,edge,glassAlpha,clamp = True)
		opacity = _math(nodeTree,"MULTIPLY",pos + Vector((2600,-400)),opacity,glass)

	nodeTree.links.new(opacity,nodeBSDF.inputs["Alpha"])

	if isBlended:
		if bpy.app.version < (4,2,0):
			matInfo["blenderMaterial"].blend_method = "BLEND"
			matInfo["blenderMaterial"].shadow_method = "NONE"
		else:
			matInfo["blenderMaterial"].surface_render_method = "BLENDED"
			matInfo["blenderMaterial"].use_transparent_shadow = True
		matInfo["disableShadowCast"] = True
	elif isFlagSet(flags,"ShadowCastDisable"):
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
