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

# Name prefix (and delete handle) for lights built from fixture geometry.
_FIXTURE_LIGHT_PREFIX = "btk_fixture_"


class _LightUtilsInternal(object):
    """Internal helpers for LightUtils."""

    @staticmethod
    def _as_objects(objects):
        """Normalize refs / names / a single item to a list of objects (missing -> None)."""
        import bpy

        if objects is None:
            return []
        if isinstance(objects, str) or not hasattr(objects, "__iter__"):
            objects = [objects]
        return [bpy.data.objects.get(o) if isinstance(o, str) else o for o in objects]

    @staticmethod
    def _world_corners(obj):
        """The object's bounding-box corners in world space."""
        from mathutils import Vector

        return [obj.matrix_world @ Vector(c) for c in obj.bound_box]

    @staticmethod
    def _world_center(obj):
        """Centre of the object's world-space bounding box."""
        from mathutils import Vector

        corners = _LightUtilsInternal._world_corners(obj)
        return sum(corners, Vector((0.0, 0.0, 0.0))) / len(corners)

    @staticmethod
    def _world_extents(obj):
        """``(x, y, z)`` size of the object's world-space bounding box."""
        corners = _LightUtilsInternal._world_corners(obj)
        return tuple(
            max(c[i] for c in corners) - min(c[i] for c in corners) for i in range(3)
        )

    @staticmethod
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
            world.use_nodes = (
                True  # pre-6.0 path; 5.x factory worlds already have a tree
            )
            nt = world.node_tree
        return nt

    @staticmethod
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


class LightUtils(_LightUtilsInternal):
    """Namespace mirror of mayatk's ``light_utils`` (helpers also exposed module-level)."""

    @staticmethod
    def set_world_hdri(
        filepath=None,
        strength=None,
        rotation=0.0,
        visible=True,
        intensity=None,
        exposure=None,
    ):
        """Set (or update) the world environment from an HDR image.

        Parameters:
            filepath (str/None): Image to load. None keeps the currently assigned map and
                only updates the levels (raises ValueError when nothing is assigned yet).
            strength (float/None): Background strength (linear light multiplier). Ignored when
                *intensity* or *exposure* is given (below). Defaults to 1.0 when none of the
                three are given.
            rotation (float): Environment rotation around Z, in degrees.
            visible (bool): When False the environment still lights the scene but the render
                background goes transparent (``film_transparent`` — engine-agnostic).
            intensity (float/None): Linear multiplier, mirroring mayatk's Arnold
                ``aiSkyDomeLight.intensity``. Combines with *exposure* as
                ``strength = intensity * 2**exposure`` — Blender's world has a single scalar
                Strength, so this pair is stored as custom properties on the world datablock
                purely so :func:`get_world_hdri` can round-trip them back out (e.g. for a
                panel's live scene-state resync) instead of collapsing to a flat strength.
            exposure (float/None): Photographic stops (log2), mirroring Arnold's ``aiExposure``.

        Returns:
            (bpy.types.World) the scene world.
        """
        import bpy

        nt = _LightUtilsInternal._world_node_tree()
        env = _LightUtilsInternal._node(nt, _ENV_NODE, "ShaderNodeTexEnvironment")
        mapping = _LightUtilsInternal._node(nt, _MAPPING_NODE, "ShaderNodeMapping")
        coords = _LightUtilsInternal._node(nt, _COORD_NODE, "ShaderNodeTexCoord")
        bg = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBackground"), None)
        out = next(
            (n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputWorld"), None
        )
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

        # intensity/exposure (when given) take precedence over a flat strength; either way both
        # are stashed on the world so a later get_world_hdri() reports the same split back.
        if intensity is not None or exposure is not None:
            intensity = 1.0 if intensity is None else intensity
            exposure = 0.0 if exposure is None else exposure
        else:
            intensity = 1.0 if strength is None else strength
            exposure = 0.0
        bg.inputs["Strength"].default_value = intensity * (2.0**exposure)
        bpy.context.scene.render.film_transparent = not visible

        world = bpy.context.scene.world
        world["btk_hdri_intensity"] = intensity
        world["btk_hdri_exposure"] = exposure
        return world

    @staticmethod
    def get_world_hdri():
        """The current world-HDRI state as a dict (``filepath``/``strength``/``intensity``/
        ``exposure``/``rotation``/``visible``), or None when no btk-managed environment map is
        set.

        ``intensity``/``exposure`` come from the custom properties :func:`set_world_hdri` stashes
        on the world (falling back to ``strength``/``0.0`` when absent — e.g. an environment set
        by external means) so a caller can round-trip the Arnold-style split back into its UI
        instead of only ever seeing the flat, collapsed ``strength``.
        """
        import bpy

        nt = _LightUtilsInternal._world_node_tree(create=False)
        if nt is None:
            return None
        env = nt.nodes.get(_ENV_NODE)
        if env is None or env.image is None:
            return None
        mapping = nt.nodes.get(_MAPPING_NODE)
        bg = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBackground"), None)
        strength = bg.inputs["Strength"].default_value if bg else 1.0
        world = bpy.context.scene.world
        return {
            "filepath": bpy.path.abspath(env.image.filepath),
            "strength": strength,
            "intensity": world.get("btk_hdri_intensity", strength)
            if world
            else strength,
            "exposure": world.get("btk_hdri_exposure", 0.0) if world else 0.0,
            "rotation": (
                math.degrees(mapping.inputs["Rotation"].default_value[2])
                if mapping
                else 0.0
            ),
            "visible": not bpy.context.scene.render.film_transparent,
        }

    @staticmethod
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

    @staticmethod
    def get_world_ray_visibility():
        """The world's diffuse/glossy ray-visibility as ``{diffuse, glossy}``, or ``None`` (no world /
        not Cycles)."""
        import bpy

        world = bpy.context.scene.world
        cv = getattr(world, "cycles_visibility", None) if world else None
        if cv is None:
            return None
        return {"diffuse": cv.diffuse, "glossy": cv.glossy}

    @staticmethod
    def set_world_importance_resolution(resolution):
        """Set the world environment's importance-sampling **map resolution** — the Cycles analogue of
        Arnold's skydome importance-sampling Resolution.

        Setting a positive value switches the world to manual sampling (``world.cycles.sampling_method
        = 'MANUAL'``) and applies ``sample_map_resolution``; a falsy value (0/None) restores automatic
        sampling (``'AUTOMATIC'``), where Cycles sizes the map itself. Cycles-only (EEVEE has no world
        sampling map). Returns the applied resolution (``None`` when automatic, or when there's no
        world / ``world.cycles`` is unavailable off-Cycles).
        """
        import bpy

        world = bpy.context.scene.world
        cw = getattr(world, "cycles", None) if world else None
        if cw is None:
            return None
        if resolution and resolution > 0:
            cw.sampling_method = "MANUAL"
            cw.sample_map_resolution = int(resolution)
            return cw.sample_map_resolution
        cw.sampling_method = "AUTOMATIC"
        return None

    @staticmethod
    def get_world_importance_resolution():
        """The world's importance-sampling map resolution when in **manual** mode, else ``None``
        (automatic sampling / no world / not Cycles). Companion to
        :func:`set_world_importance_resolution`."""
        import bpy

        world = bpy.context.scene.world
        cw = getattr(world, "cycles", None) if world else None
        if cw is None:
            return None
        return int(cw.sample_map_resolution) if cw.sampling_method == "MANUAL" else None

    @staticmethod
    def clear_world_hdri():
        """Remove the btk-managed HDRI environment (env / mapping / coord nodes) from the world.

        Blender analogue of mayatk's ``HdrManager.clear`` (which deletes the skydome + file /
        place2d network): drops only the nodes this module creates, leaving any Background → World
        Output the user had. Returns True when a managed environment was present and cleared,
        False when there was nothing to clear.
        """
        nt = _LightUtilsInternal._world_node_tree(create=False)
        if nt is None:
            return False
        removed = False
        for name in (_ENV_NODE, _MAPPING_NODE, _COORD_NODE):
            node = nt.nodes.get(name)
            if node is not None:
                nt.nodes.remove(node)
                removed = True
        return removed

    # ------------------------------------------------------------------ fixture lights

    @staticmethod
    def lights_from_geometry(
        objects,
        power=100.0,
        color=(1.0, 1.0, 1.0),
        direction="auto",
        offset=0.01,
        spread=None,
        prefix=_FIXTURE_LIGHT_PREFIX,
        diffuse_only=False,
    ):
        """Create a real area light matched to each light-fixture *mesh*.

        An emissive map is an **appearance**, not a light source: it makes a fixture look
        lit without emitting anything an artist can aim, colour-temperature or re-time, and
        driving a bake from emissive geometry instead couples the room's illumination to a
        texture's exposure. This builds actual lights from the fixture geometry that is
        already correctly placed and sized in the scene, so a module authored without
        lights (a StingrayPBS/IBL scene exports none) can be lit for a bake without
        re-authoring it upstream.

        Each light is an **area** light matched to the fixture's plate: the mesh's thinnest
        bounding axis is taken as the emitting normal, the other two become the light's
        width and height, and the light is placed at the fixture's centre pushed *offset*
        along the emission direction so it never sits inside its own housing.

        The box is the **world-axis-aligned** bounding box, so a fixture rotated off the
        world axes gets a light that is correctly placed and powered but axis-aligned in
        size and aim — fine for the architectural case this exists for (ceiling and wall
        plates), wrong for a raked or angled fitting. Pass an explicit *direction* for
        those, or author real lights upstream.

        Parameters:
            objects: Fixture meshes (refs or names).
            power: Radiant power per light, in Watts.
            color: Light RGB.
            direction: ``"auto"`` aims each light along its thin axis toward the overall
                centre of the set — a ceiling panel points down, a wall panel points inward,
                with no per-object setup. Pass an explicit ``(x, y, z)`` to force one
                direction for all of them.
            offset: Metres to push the light off the fixture surface along its aim.
            spread: Beam spread in degrees (``None`` keeps Blender's 180° default).
            prefix: Name prefix, also the handle :meth:`remove_lights` deletes by.
            diffuse_only: Drop the light's specular contribution. Useful for a *lightmap*
                bake, where a baked specular highlight would be locked to the baking
                viewpoint and read as a smudge from every other angle.

        Returns:
            (list) names of the light objects created.
        """
        import bpy
        from mathutils import Vector

        meshes = []
        for o in _LightUtilsInternal._as_objects(objects):
            if o is not None and o.type == "MESH":
                meshes.append(o)
        if not meshes:
            return []

        centers = {o.name: _LightUtilsInternal._world_center(o) for o in meshes}
        group_center = sum(centers.values(), Vector((0.0, 0.0, 0.0))) / len(centers)

        created = []
        for obj in meshes:
            extents = _LightUtilsInternal._world_extents(obj)
            axis = min(range(3), key=lambda i: extents[i])  # thinnest = plate normal
            sizes = sorted(extents[i] for i in range(3) if i != axis)
            size_y, size_x = sizes[0], sizes[1]

            normal = Vector((0.0, 0.0, 0.0))
            normal[axis] = 1.0
            if direction == "auto":
                # Aim along the plate's own thin axis, toward the middle of the set: a
                # ceiling plate points down, a wall plate points inward. The aim stays ON
                # that axis even when the position is ambiguous, because the rectangle was
                # sized from the OTHER two — aiming somewhere else would light a shape the
                # fixture does not have.
                toward = group_center - centers[obj.name]
                if toward[axis] < 0:
                    normal = -normal
                elif toward[axis] == 0 and axis == 2:
                    # Dead centre along the aim axis — a lone fixture, or a co-planar row
                    # of them. Only Z has a natural answer, and it is down; an ambiguous
                    # X/Y plate keeps +axis, which is arbitrary but deterministic.
                    normal = -normal
            else:
                normal = Vector(direction).normalized()

            data = bpy.data.lights.new(f"{prefix}{obj.name}", type="AREA")
            data.energy = float(power)
            data.color = tuple(color)[:3]
            data.shape = "RECTANGLE"
            data.size = max(size_x, 1e-4)
            data.size_y = max(size_y, 1e-4)
            if spread is not None:
                data.spread = math.radians(float(spread))
            if diffuse_only:
                data.specular_factor = 0.0

            light = bpy.data.objects.new(f"{prefix}{obj.name}", data)
            light.location = centers[obj.name] + normal * float(offset)
            # An area light emits along its local -Z, so aim that at the emission normal.
            light.rotation_euler = normal.to_track_quat("-Z", "Y").to_euler()
            bpy.context.scene.collection.objects.link(light)
            created.append(light.name)

        return created

    @staticmethod
    def remove_lights(prefix=_FIXTURE_LIGHT_PREFIX):
        """Delete the light objects :meth:`lights_from_geometry` created; return their names."""
        import bpy

        removed = []
        for obj in [o for o in bpy.data.objects if o.type == "LIGHT"]:
            if not obj.name.startswith(prefix):
                continue
            data = obj.data
            removed.append(obj.name)
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.lights.remove(data)
        return removed
