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

import pythontk as ptk

# Fixed node names — the update-in-place handles for the world-HDRI rig.
_ENV_NODE = "btk_hdri_env"
_MAPPING_NODE = "btk_hdri_mapping"
_COORD_NODE = "btk_hdri_coords"

# Name prefix (and delete handle) for lights built from fixture geometry.
_FIXTURE_LIGHT_PREFIX = "btk_fixture_"

# "argument not supplied" sentinel — lets an explicit ``None`` keep its own meaning
# ("no world") where the default means "ask the scene". See LightUtils.world_emits.
_SCENE_WORLD = object()


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
    def _emitter_area(light):
        """The emitting area of a sized AREA light, in scene units squared.

        Shape-aware, because Blender spends ``size``/``size_y`` differently per shape:
        SQUARE and DISK read ``size`` alone (a disk's is its DIAMETER), RECTANGLE and
        ELLIPSE use both. Used to finish a ``radiance`` record into watts, so getting
        the shape wrong would misstate the light's power by a constant factor rather
        than fail visibly.
        """
        width = float(getattr(light, "size", 0.0) or 0.0)
        if light.shape in {"RECTANGLE", "ELLIPSE"}:
            height = float(getattr(light, "size_y", 0.0) or 0.0)
        else:
            height = width
        area = width * height
        # An ellipse/disk inscribes its bounding box: pi/4 of it.
        return area * (math.pi / 4.0) if light.shape in {"DISK", "ELLIPSE"} else area

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
        # The absent-tree case is the 4.x find-or-create. The second clause is
        # 4.x too and is the one a pure `nt is None` guard misses: there, a
        # world can carry a tree while ``use_nodes`` is False, and the renderer
        # ignores the tree in that state -- so the caller would edit nodes,
        # report success, and still render flat. The old unconditional write
        # covered it; the guard that replaced it did not. Version-gated rather
        # than probed, because on 5.x merely READING ``use_nodes`` emits a
        # deprecation warning and it is pinned True regardless, and 6.0 removes
        # it (where reading raises) -- so on 5.x+ this is never evaluated and
        # behaviour is byte-identical to the plain `nt is None` guard.
        if create and (
            nt is None
            or (bpy.app.version < (5, 0) and not world.use_nodes)
        ):
            world.use_nodes = True
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

    @staticmethod
    def world_emits(world=_SCENE_WORLD) -> bool:
        """True when *world* can light a render/bake (a non-black background).

        The world is Cycles' analogue of an ``aiSkyDomeLight``, so "is anything lit here?"
        cannot be answered from the ``LIGHT`` objects alone -- an HDRI-only scene has none.
        Omitting *world* asks about the scene's; passing ``None`` explicitly means "no world"
        and is answered False (the two must stay distinguishable -- a caller forwarding a
        possibly-``None`` ``scene.world`` would otherwise silently re-query the scene).

        Deliberately CONSERVATIVE, because callers use it to decide whether to warn: anything
        it cannot read statically (a linked Strength/Color -- HDRI texture, driver, node
        group) counts as lit rather than risk crying "unlit" at a correctly lit setup. The
        one exception is a readable zero strength, which is definitive -- no colour, however
        it is driven, emits through it.
        """
        import bpy

        if world is _SCENE_WORLD:
            scene = getattr(bpy.context, "scene", None)
            world = getattr(scene, "world", None) if scene is not None else None
        if world is None:
            return False
        # Note ``use_nodes`` is NOT consulted: it is deprecated on 5.x (where it is pinned
        # True, so it decides nothing) and removed in 6.0 (where READING it would raise).
        # The tree's presence is the one test that holds on every supported version -- and
        # the flat-colour fallback below is live, not dead: on the 4.x we also support a
        # world can genuinely carry no tree.
        node_tree = world.node_tree
        if node_tree is None:
            return any(c > 0.0 for c in world.color)
        for node in node_tree.nodes:
            if node.type not in {"BACKGROUND", "EMISSION"}:
                continue
            strength = node.inputs.get("Strength")
            color = node.inputs.get("Color")
            if strength is None:
                continue
            # Test the definitive zero BEFORE the linked-input escape hatch below, or an
            # HDRI plugged into a strength-0 background would read as lit.
            if not strength.is_linked and strength.default_value <= 0.0:
                continue
            if strength.is_linked or color is None or color.is_linked:
                return True
            if any(c > 0.0 for c in color.default_value[:3]):
                return True
        return False

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
        *,
        # Keyword-only, and appended rather than grouped beside the parameters they
        # read with (*kelvin* with *color*, *toward* with *direction*): inserting
        # them there would silently rebind every positional caller of a published
        # signature, and the ``*`` keeps that true if anything is added later.
        kelvin=None,
        toward=None,
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
        width and height, and the light is placed clear of the plate's own half-thickness
        plus *offset* along the emission direction, so it never sits inside its own
        housing (where that housing would block it and the room would bake dark).

        The box is the **world-axis-aligned** bounding box, so a fixture rotated off the
        world axes gets a light that is correctly placed and powered but axis-aligned in
        size and aim — fine for the architectural case this exists for (ceiling and wall
        plates), wrong for a raked or angled fitting. Pass an explicit *direction* for
        those, or author real lights upstream.

        Parameters:
            objects: Fixture meshes (refs or names).
            power: Radiant power per light, in Watts.
            color: Light RGB (a tint on top of *kelvin* when both are given).
            kelvin: (keyword-only) Colour temperature. ``None`` leaves the light white.
                Office troffers are 3500-4100K, which against a warm interior is the
                cue that most decides whether a bake reads as a room or as CG. Needs
                Blender >= 4.2; silently ignored on older builds (no such property).
            direction: ``"auto"`` aims each light along its thin axis toward *toward* —
                a ceiling panel points down, a wall panel points inward, with no
                per-object setup. Pass an explicit ``(x, y, z)`` to force one direction
                for all of them.
            toward: (keyword-only) World point the plates should face. Defaults to the
                centre of *objects*, which is **ambiguous for a coplanar set** — a
                ceiling grid's own centre lies in the ceiling — and resolves to "down".
                Pass the room's centre (e.g. the centre of every mesh being baked) to
                make ceiling AND wall fixtures aim correctly in the same call.
            offset: Metres of clearance between the fixture's surface and the light.
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
        if toward is None:
            reference = sum(centers.values(), Vector((0.0, 0.0, 0.0))) / len(centers)
        else:
            reference = Vector(toward)

        created = []
        for obj in meshes:
            corners = _LightUtilsInternal._world_corners(obj)
            minimum = [min(c[i] for c in corners) for i in range(3)]
            maximum = [max(c[i] for c in corners) for i in range(3)]
            # The plate arithmetic -- thin axis, rectangle, coplanar-aim guard and
            # housing clearance -- is the same in every host, so it lives in
            # pythontk rather than once here and again in mayatk's twin.
            plate = ptk.PlateEmitter.from_bounds(
                minimum,
                maximum,
                toward=reference if direction == "auto" else None,
                offset=offset,
                up_axis=2,
            )
            size_x, size_y = plate.size
            if direction == "auto":
                normal = Vector(plate.normal)
                location = Vector(plate.position)
            else:
                normal = Vector(direction).normalized()
                thickness = maximum[plate.axis] - minimum[plate.axis]
                location = centers[obj.name] + normal * (thickness * 0.5 + float(offset))

            data = bpy.data.lights.new(f"{prefix}{obj.name}", type="AREA")
            data.energy = float(power)
            data.color = tuple(color)[:3]
            if kelvin and hasattr(data, "use_temperature"):
                # Blender >= 4.2 owns the blackbody conversion; *color* stays a tint on
                # top of it. Rolling our own Kelvin->RGB would be a second, worse copy
                # of a conversion the renderer already agrees with.
                data.use_temperature = True
                data.temperature = float(kelvin)
            data.shape = "RECTANGLE"
            data.size = max(size_x, 1e-4)
            data.size_y = max(size_y, 1e-4)
            if spread is not None:
                data.spread = math.radians(float(spread))
            if diffuse_only:
                data.specular_factor = 0.0

            light = bpy.data.objects.new(f"{prefix}{obj.name}", data)
            light.location = location
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

    @classmethod
    def set_world_environment(
        cls,
        hdri=None,
        strength=1.0,
        color=None,
        rotation=0.0,
    ) -> str:
        """Light the world with an equirect HDRI, or a flat ambient colour.

        Thin adapter over :meth:`set_world_hdri` (which owns the managed env / mapping /
        coord node rig and the HDR Manager panel's contract) -- this only adds the no-HDRI
        flat-ambient fallback and a description string for the bake log.

        The world matters to a bridged bake because a Maya scene lit by StingrayPBS IBL
        brings across **no lighting at all**: the cubemaps (``TEX_diffuse_cube.dds`` /
        ``TEX_specular_cube.dds``) are neither exported by FBX nor loadable by Blender as a
        world. The HDRI supplied here stands in for them -- as *ambient*, alongside the
        scene's real lights, not as a replacement for them.

        Parameters:
            hdri: Equirect ``.hdr`` / ``.exr``. ``None`` -> flat *color* ambient.
            strength: World multiplier.
            color: Flat background RGB when no *hdri*. Defaults to near-black.
            rotation: Environment rotation around Z, in degrees.

        Returns:
            A short human-readable description of what was applied (for logs / the footer).
        """
        if hdri:
            if not os.path.isfile(hdri):
                # Never silent: a mistyped HDRI path that quietly becomes flat ambient is
                # a black bake the artist cannot explain.
                raise FileNotFoundError(f"Environment HDRI not found: {hdri}")
            cls.set_world_hdri(hdri, strength=strength, rotation=rotation)
            return f"HDRI {os.path.basename(hdri)} @ {strength}"

        cls.clear_world_hdri()
        # Find-or-create the world AND its tree through the one helper that owns that
        # dance -- hand-rolling it here duplicated the logic and, worse, wrote the
        # deprecated ``use_nodes`` UNGUARDED (removed in Blender 6.0; the helper only
        # touches it when there is genuinely no tree, which 5.x worlds never hit).
        nt = _LightUtilsInternal._world_node_tree(create=True)
        background = next(
            (n for n in nt.nodes if n.type == "BACKGROUND"), None
        ) or nt.nodes.new("ShaderNodeBackground")
        out = next((n for n in nt.nodes if n.type == "OUTPUT_WORLD"), None)
        if out is None:
            out = nt.nodes.new("ShaderNodeOutputWorld")
        if not background.outputs["Background"].links:
            nt.links.new(background.outputs["Background"], out.inputs["Surface"])
        rgb = tuple(color or (0.02, 0.02, 0.024))[:3]
        background.inputs["Color"].default_value = (*rgb, 1.0)
        background.inputs["Strength"].default_value = float(strength)
        return f"flat ambient {rgb} @ {strength}"

    @staticmethod
    def lights_from_records(records):
        """Turn plain light records into real lights, reusing each one's placed Empty.

        The rebuild half of a DCC hand-off's light transport. A light OBJECT often
        cannot cross an interchange format -- FBX carries no plugin light types at
        all, and Blender 5.1's bundled importer aborts the whole import on any FBX
        containing a light -- but the light's TRANSFORM does, as a null the importer
        places correctly. So the sender ships parameters as data and this attaches a
        light datablock to the empty already sitting in the right place: the format
        keeps doing placement, and no coordinate or unit conversion is written by
        hand (an empty that arrived through a cm-to-m import is already in metres).

        Records are plain dicts so any sender can produce them::

            {"name": "keyLight",        # the empty to convert (required)
             "type": "POINT"|"SPOT"|"SUN"|"AREA",
             "color": [r, g, b], "energy": <watts>,
             "radiance": <W/m2/sr>,     # AREA: instead of energy, see below
             "aim": [x, y, z], "axis_up": "Y"|"Z",   # world aim, sender's axes
             "spot_size": <radians>, "spot_blend": <0-1>,        # SPOT
             "shape": "RECTANGLE"|"SQUARE"|"DISK",
             "local_size": [x, y],                               # AREA, LOCAL units
             "cast_shadow": <bool>}   # omit to keep Blender's own default (on)

        ``local_size`` is scaled by the empty's own world scale, so the emitter ends
        up the size the source made it whatever the import did to the scene.

        ``radiance`` is the power field for an AREA light that emits PER UNIT AREA
        (Maya/Arnold with normalize off) rather than at a fixed total power. It
        replaces ``energy``, and is converted here -- ``energy = radiance * pi *
        area`` -- because only this side knows the lamp's area in the scene's own
        metres. A sender must NOT pre-multiply by an area it measured itself: area is
        a squared length, so a sender working in centimetres would be out by 1e4 with
        nothing to show for it (this is a fixed bug, not a hypothetical).

        ``aim`` overrides the empty's own orientation, and senders should provide it:
        a placed null carries POSITION reliably across an interchange format, but its
        rotation may have been reconciled against that format's light-axis convention
        on the way out (measured with Maya -> FBX: a spot aimed straight down arrives
        aiming sideways). ``axis_up`` names the sender's up axis so the vector is
        converted here rather than each sender guessing Blender's.

        Rebalancing the result is a separate step -- compose with
        :meth:`scale_light_energy` when the sender's intensity units are not watts::

            built = LightUtils.lights_from_records(records)
            LightUtils.scale_light_energy(2.5, list(built.values()))

        Parameters:
            records: Iterable of record dicts. A record naming no existing object is
                skipped (it simply did not come across).

        Returns:
            ``{record name: object name}`` for the lights actually built.
        """
        import bpy
        import mathutils

        # ``matrix_world`` is evaluated state: an object whose transform was set since
        # the last depsgraph update still reports identity, and every placement here
        # is read from it. Cheap once per call, and it makes the function correct for
        # a caller that just built the empties itself rather than only for one running
        # after an import operator.
        bpy.context.view_layer.update()

        built = {}
        for record in records or []:
            name = record.get("name")
            empty = bpy.data.objects.get(name) if name else None
            # The record names the light's PLACEHOLDER, and the placeholder is
            # removed below to free its name -- so the type check is not a
            # nicety, it is what stands between a name collision and deleted
            # GEOMETRY. Maya allows a mesh and a light to share a leaf name
            # under different parents; the FBX importer then suffixes one of
            # them, and if the suffix landed on the light's null this lookup
            # returns the MESH. A record that resolves to anything but an
            # empty simply did not come across (the caller's "not rebuilt"
            # report already surfaces the skip).
            if empty is None or getattr(empty, "type", None) != "EMPTY":
                continue

            # Capture what the empty carries, then FREE ITS NAME before creating the
            # light: Blender uniquifies a clashing object name, so building the lamp
            # first would leave "keyLight.001" -- and downstream joins (the lightmap
            # manifest, a send back to Maya) key on that name, so the suffix is not
            # cosmetic.
            matrix = empty.matrix_world.copy()
            parent, parent_inverse = empty.parent, empty.matrix_parent_inverse.copy()
            collections = list(empty.users_collection)
            # Anything parented UNDER the light comes with it. Removing the empty
            # would re-root its children, and Blender keeps their local matrix when
            # that happens -- so they would silently jump to the world origin.
            children = list(empty.children)
            child_inverses = [c.matrix_parent_inverse.copy() for c in children]
            bpy.data.objects.remove(empty, do_unlink=True)

            light = bpy.data.lights.new(name, record.get("type") or "POINT")
            light.color = tuple(record.get("color") or (1.0, 1.0, 1.0))[:3]
            light.energy = float(record.get("energy") or 0.0)
            # Senders whose lights default to NOT casting (Maya's do) have to say
            # so: a Cycles light always casts unless told otherwise, so a
            # deliberately shadowless fill would arrive shadowing. Absent key ==
            # "the sender has no opinion" -> leave Blender's own default.
            if record.get("cast_shadow") is not None:
                light.use_shadow = bool(record["cast_shadow"])
            if light.type == "SPOT":
                if record.get("spot_size") is not None:
                    light.spot_size = float(record["spot_size"])
                if record.get("spot_blend") is not None:
                    light.spot_blend = float(record["spot_blend"])
            elif light.type == "AREA":
                light.shape = record.get("shape") or "SQUARE"
                local = record.get("local_size") or [1.0, 1.0]
                scale = matrix.to_scale()
                light.size = abs(float(local[0]) * scale.x)
                if light.shape in {"RECTANGLE", "ELLIPSE"}:
                    light.size_y = abs(float(local[1]) * scale.y)
                # ``radiance`` instead of ``energy``: a sender whose area light emits
                # per unit area (Maya/Arnold with normalize off) cannot state watts,
                # because watts depend on the emitting AREA and the area is only known
                # once the lamp has been sized -- here, in the scene's own metres,
                # from the empty the import placed. A sender that computed it on its
                # own side would be squaring a length in ITS units (a cm scene puts
                # the factor out by 1e4), which is precisely the bug this field
                # exists to make unrepresentable.
                if record.get("radiance") is not None:
                    light.energy = (
                        float(record["radiance"])
                        * math.pi
                        * _LightUtilsInternal._emitter_area(light)
                    )

            # The empty WAS the light's transform, so the lamp simply takes its place
            # -- same matrix, same parent, same collections. Parent is assigned before
            # matrix_world: assigning it after would reinterpret the local basis
            # against the parent and move the light.
            aim = record.get("aim")
            if aim:
                # Blender is Z-up: a Y-up sender's (x, y, z) is (x, -z, y) here.
                # Applied to the world matrix's ROTATION only -- translation and
                # scale stay as the import placed them.
                vector = mathutils.Vector(
                    (aim[0], -aim[2], aim[1])
                    if str(record.get("axis_up", "Z")).upper() == "Y"
                    else aim
                )
                if vector.length > 1e-9:
                    location, _, scale = matrix.decompose()
                    matrix = (
                        mathutils.Matrix.Translation(location)
                        # Lights emit down local -Z in Blender, so track -Z to the
                        # aim; Y is the roll reference, which only a rectangular
                        # area light can notice.
                        @ vector.to_track_quat("-Z", "Y").to_matrix().to_4x4()
                        @ mathutils.Matrix.Diagonal(scale).to_4x4()
                    )

            lamp = bpy.data.objects.new(name, light)
            for collection in collections:
                collection.objects.link(lamp)
            lamp.parent = parent
            lamp.matrix_parent_inverse = parent_inverse
            lamp.matrix_world = matrix
            # The lamp stands exactly where the empty stood, so re-parenting the
            # children onto it with their original parent-inverse keeps every world
            # transform -- EXCEPT when `aim` re-oriented the lamp, which is the point
            # of the aim override and would drag the children round with it. Their
            # world matrices are therefore restored explicitly.
            for child, inverse in zip(children, child_inverses):
                world = child.matrix_world.copy()
                child.parent = lamp
                child.matrix_parent_inverse = inverse
                child.matrix_world = world
            built[name] = lamp.name
        return built

    @staticmethod
    def scale_light_energy(multiplier, lights=None):
        """Multiply the energy of light objects, returning ``{name: new_energy}``.

        Relative, not absolute, because the use case is *correcting units rather than
        authoring them*: a light that crossed an FBX from another DCC arrives with its
        intensity translated by that DCC's exporter and the importer's own guess, and
        the two rarely agree on watts. The artist's relative brightnesses are intact --
        it is the overall scale that needs a dial -- so a multiplier keeps the lighting
        design and fixes only what the crossing got wrong. ``1.0`` is a no-op.

        Distinct from :meth:`lights_from_geometry` (which CREATES lights at an explicit
        wattage) and from :meth:`set_emission_strength` (an appearance, not a light).

        Parameters:
            multiplier: Factor applied to each light's ``data.energy``.
            lights: Light objects or names. ``None`` -> every light in the file.

        Returns:
            ``{object name: energy after scaling}`` -- empty when there is nothing to
            scale, so a caller can report "no lights" without a second query.
        """
        import bpy

        import pythontk as ptk

        if lights is None:
            targets = [o for o in bpy.data.objects if o.type == "LIGHT"]
        else:
            targets = []
            for light in ptk.make_iterable(lights):
                obj = bpy.data.objects.get(light) if isinstance(light, str) else light
                if obj is not None and obj.type == "LIGHT":
                    targets.append(obj)

        scaled, seen = {}, set()
        for obj in targets:
            data = obj.data
            # Lights can share a datablock (a linked duplicate); scaling is relative,
            # so touching one twice would COMPOUND rather than repeat.
            if data.name_full not in seen:
                seen.add(data.name_full)
                data.energy = float(data.energy) * float(multiplier)
            scaled[obj.name] = float(data.energy)
        return scaled

    @staticmethod
    def set_emission_strength(multiplier, objects=None):
        """Set Emission Strength on every material whose Emission Color is textured.

        **This controls an appearance, not the lighting.** An emissive map makes a fixture
        read as switched-on; it is not the room's light source, and driving a bake from it
        would tie the illumination to a texture's exposure and give the artist nothing to
        aim or re-colour. Real lights come from :meth:`lights_from_geometry` (or from the
        scene's own light objects); this just keeps the glow looking right alongside them.

        Because emission still contributes to a Cycles bake, keep it modest -- push it only
        as far as the fixtures need to *look* correct.

        Parameters:
            multiplier: The Emission Strength to set (not a relative scale).
            objects: Restrict to these objects' materials. ``None`` -> every material.

        Returns:
            Names of the materials touched.
        """
        import bpy

        import pythontk as ptk

        if objects is None:
            materials = list(bpy.data.materials)
        else:
            materials = []
            for obj in ptk.make_iterable(objects):
                obj = bpy.data.objects.get(obj) if isinstance(obj, str) else obj
                for slot in getattr(obj, "material_slots", []) or []:
                    if slot.material is not None and slot.material not in materials:
                        materials.append(slot.material)

        touched = []
        for material in materials:
            if not material.use_nodes or material.node_tree is None:
                continue
            for node in material.node_tree.nodes:
                if node.type != "BSDF_PRINCIPLED":
                    continue
                color = node.inputs.get("Emission Color")
                strength = node.inputs.get("Emission Strength")
                if color is not None and color.is_linked and strength is not None:
                    strength.default_value = float(multiplier)
                    if material.name not in touched:
                        touched.append(material.name)
        return touched
