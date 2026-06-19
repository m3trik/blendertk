# !/usr/bin/python
# coding=utf-8
"""Light utilities — the world-environment (HDRI) helpers behind the HDR Manager panel
(mirror of mayatk's ``light_utils`` skydome workflow: set an HDR map, drive its
intensity/rotation/visibility, query the current state).

Maya's version manages an Arnold aiSkyDomeLight; here the same contract rides the world
shader (Environment Texture → Background → World Output, with a Mapping node for rotation).
Nodes are found-or-created by fixed names so repeated calls update in place.

``import bpy`` is deferred into the call bodies (no import side effects).
"""
import math
import os

# Fixed node names — the update-in-place handles for the world-HDRI rig.
_ENV_NODE = "btk_hdri_env"
_MAPPING_NODE = "btk_hdri_mapping"
_COORD_NODE = "btk_hdri_coords"


def _world_node_tree(create=True):
    """The scene world's node tree (creating the world / enabling nodes when needed)."""
    import bpy

    scene = bpy.context.scene
    world = scene.world
    if world is None:
        if not create:
            return None
        world = bpy.data.worlds.new("World")
        scene.world = world
    nt = world.node_tree
    if nt is None and create:
        world.use_nodes = True  # pre-6.0 path; 5.x factory worlds already have a tree
        nt = world.node_tree
    return nt


def _node(nt, name, type_):
    """Find-or-create a node by fixed name (recreated if the type doesn't match)."""
    n = nt.nodes.get(name)
    if n is not None and n.bl_idname != type_:
        nt.nodes.remove(n)
        n = None
    if n is None:
        n = nt.nodes.new(type_)
        n.name = name
    return n


def set_world_hdri(filepath=None, strength=1.0, rotation=0.0, visible=True):
    """Set (or update) the world environment from an HDR image.

    Parameters:
        filepath (str/None): Image to load. None keeps the currently assigned map and
            only updates the levels (raises ValueError when nothing is assigned yet).
        strength (float): Background strength (linear light multiplier).
        rotation (float): Environment rotation around Z, in degrees.
        visible (bool): When False the environment still lights the scene but the render
            background goes transparent (``film_transparent`` — engine-agnostic).

    Returns:
        (bpy.types.World) the scene world.
    """
    import bpy

    nt = _world_node_tree()
    env = _node(nt, _ENV_NODE, "ShaderNodeTexEnvironment")
    mapping = _node(nt, _MAPPING_NODE, "ShaderNodeMapping")
    coords = _node(nt, _COORD_NODE, "ShaderNodeTexCoord")
    bg = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBackground"), None)
    out = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputWorld"), None)
    if bg is None:
        bg = nt.nodes.new("ShaderNodeBackground")
    if out is None:
        out = nt.nodes.new("ShaderNodeOutputWorld")

    if filepath:
        env.image = bpy.data.images.load(
            os.path.abspath(os.path.expanduser(filepath)), check_existing=True
        )
    elif env.image is None:
        raise ValueError("No HDR map assigned yet — a filepath is required.")

    nt.links.new(coords.outputs["Generated"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    mapping.inputs["Rotation"].default_value[2] = math.radians(rotation)
    bg.inputs["Strength"].default_value = strength
    bpy.context.scene.render.film_transparent = not visible
    return bpy.context.scene.world


def get_world_hdri():
    """The current world-HDRI state as a dict (``filepath``/``strength``/``rotation``/
    ``visible``), or None when no btk-managed environment map is set."""
    import bpy

    nt = _world_node_tree(create=False)
    if nt is None:
        return None
    env = nt.nodes.get(_ENV_NODE)
    if env is None or env.image is None:
        return None
    mapping = nt.nodes.get(_MAPPING_NODE)
    bg = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBackground"), None)
    return {
        "filepath": bpy.path.abspath(env.image.filepath),
        "strength": bg.inputs["Strength"].default_value if bg else 1.0,
        "rotation": (
            math.degrees(mapping.inputs["Rotation"].default_value[2]) if mapping else 0.0
        ),
        "visible": not bpy.context.scene.render.film_transparent,
    }


def set_world_ray_visibility(diffuse=None, glossy=None):
    """Toggle whether the world environment contributes to **diffuse** / **glossy** lighting — the
    Cycles (boolean) analogue of Arnold's ``aiDiffuse`` / ``aiSpecular`` skydome contribution
    (float in Maya). Only the given components change (``None`` = leave as-is).

    Cycles-only (EEVEE has no per-world ray visibility). Returns the applied ``{diffuse, glossy}``
    state, or ``None`` when there's no world / ``cycles_visibility`` is unavailable.
    """
    import bpy

    world = bpy.context.scene.world
    cv = getattr(world, "cycles_visibility", None) if world else None
    if cv is None:
        return None
    if diffuse is not None:
        cv.diffuse = bool(diffuse)
    if glossy is not None:
        cv.glossy = bool(glossy)
    return {"diffuse": cv.diffuse, "glossy": cv.glossy}


def get_world_ray_visibility():
    """The world's diffuse/glossy ray-visibility as ``{diffuse, glossy}``, or ``None`` (no world /
    not Cycles)."""
    import bpy

    world = bpy.context.scene.world
    cv = getattr(world, "cycles_visibility", None) if world else None
    if cv is None:
        return None
    return {"diffuse": cv.diffuse, "glossy": cv.glossy}


class LightUtils:
    """Namespace mirror of mayatk's ``light_utils`` (helpers also exposed module-level)."""

    set_world_hdri = staticmethod(set_world_hdri)
    get_world_hdri = staticmethod(get_world_hdri)
    set_world_ray_visibility = staticmethod(set_world_ray_visibility)
    get_world_ray_visibility = staticmethod(get_world_ray_visibility)
