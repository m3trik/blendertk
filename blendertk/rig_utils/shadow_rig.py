# !/usr/bin/python
# coding=utf-8
"""Shadow Rig — engine + Switchboard slot wiring for the co-located ``shadow_rig.ui``.

Blender port of mayatk's ``rig_utils.shadow_rig`` (``btk.ShadowRig`` ↔ ``mtk.ShadowRig``): a
projected-shadow rig for engine export — a quad plane on the ground carrying the targets'
shadow as a PNG, its transform driven to follow the source so it reads as a cast shadow.

**The shadow is the real projection.** The targets' geometry is projected onto the ground
plane through the source and rasterized where it lands (``pythontk.ImgUtils.rasterize_shadow``
over ``pythontk.ShadowProjection``): an overhead source draws the footprint, a low sun the long
stretched shape, a near lamp the perspective-grown head, and a source with a size (an area
light, a sun's angular disc) a penumbra widening away from the contact. A ``SUN`` light is
projected along its direction (world -Z) instead of from a position; any other object casts
from where it sits.

**Live placement.** Between rasterizations the plane follows the projection's *model* — the
shadow of the targets' bounding cylinder, a handful of clamps and ratios — so its heading, reach,
perspective growth and slide (a target leaving the ground) track the source and the target. The
canvas the PNG was drawn into is stamped on the plane as fractions of that model
(``canvasU0..canvasW1``), which is what re-places it at any source position; **Recalculate
Silhouette** re-renders the PNG when the silhouette itself has gone stale. ``groundHeight`` is a
prop the drivers read, so a raised floor is a post-create edit.

**Coordinate system.** Maya is Y-up (ground = XZ, plane rotates about Y); Blender is **Z-up**
(ground = XY, plane rotates about **Z**). The shared model takes the up axis as a parameter
(``up=2`` here) and defines the across-bearing axis identically in both (``w = (u[1], -u[0])``,
the plane's local +X), so one rasterized texture reads the same way in either DCC. The plane's
local +Y heads away from the source; ``R_z(t)`` sends +Y to ``(-sin t, cos t)``, so the Z
rotation is ``atan2(-ux, uy)``.

**Drivers, not an expression.** Maya wires the model into one ``expression`` node; Blender caps a
driver expression at 255 characters and evaluates each channel separately, so the model is split
into driven intermediates: level 1 on the rig group (reads the source and contact transforms and
the plane's stamps), level 2 on the contact empty (reads level 1), and the plane's channels read
both — each level on its own ID, so no driver reads a driven property of its own datablock (a
depsgraph cycle). Expressions are **branchless arithmetic** (``min``/``max``/``sqrt``/``atan2``/
``pow``) so they stay on Blender's fast driver parser — a Python ternary would force the slow
full-Python parser (and a security gate). A sun's direction is read straight off its
``matrix_world`` column (a ``SINGLE_PROP`` path), written as a point a very long way back along
the ray so the one model body serves both source kinds. The opacity math lives on the
material's ``opacity`` Value node (a different ID) and the plane's ``opacity`` prop mirrors it;
a bake keys the prop and turns the material's driver into a follower of it.

Material: unlit **black Emission** mixed against a **Transparent BSDF** by ``tex.alpha ×
opacity`` — a node graph, so it renders the silhouette and the fade per pixel. The PNG is
persisted via ``bpy.data.images`` (no cv2/PIL dependency); the rasterizer's rows run from the
light-side edge, which Blender's bottom-up pixel rows put on the plane's ``V = 0`` (near) edge.

**Re-attaching.** The plane links its targets (a JSON name list), source and contact (ID
pointers), and stamps its measurements and canvas, so :meth:`ShadowRig.for_node` /
:meth:`ShadowRig.from_plane` rebuild an instance from any of the rig's objects for the panel's
Utility section: :meth:`set_source`, :meth:`rebuild`, :meth:`unbake_planes`,
:meth:`refresh_silhouette`, :meth:`delete_rigs` — on rigs built in an earlier session too.

**Engine hand-off.** ``refresh_export_metadata`` publishes a ``shadow_metadata`` JSON channel
onto the shared ``data_export`` carrier (``btk.DataNodes``) at authoring time
(create/bake/delete), and is registered in ``FbxUtils._KNOWN_PRODUCERS`` so the Scene Exporter
re-refreshes it at export time. unitytk's ``ShadowPlaneController.cs`` joins records to the
imported planes by GameObject name and finishes the Unity setup automatically. The baked
``opacity`` prop reaches Unity through the same custom-property route as Maya's; the
visibility-pair mirror and the GLB fade channel are produced by mayatk's RenderOpacity tool,
which has no Blender port yet.

``import bpy`` / ``numpy`` are deferred into the call bodies and the Qt-only ``uitk`` helper into
its method, so the module resolves headless and loads under the workspace ``.venv``.
"""

import json
import math
import os

import pythontk as ptk

from blendertk.rig_utils._rig_utils import RigUtils


class ShadowRig(ptk.LoggingMixin):
    """Projected-shadow rig for engine export (mirror of mayatk's ``ShadowRig``)."""

    MODES = ("orbit",)
    # Retired modes accepted for one release, mapped to their replacement.
    _DEPRECATED_MODES = {"stretch": "orbit"}
    DEFAULT_SOURCE_NAME = "shadow_source"
    # Lift above the ground plane to avoid z-fighting (build + drivers; Maya parity).
    GROUND_OFFSET = 0.01
    # The plane's fade channel — Maya's RenderOpacity attr of the same name.
    OPACITY_ATTR = "opacity"
    # data_export carrier channel (see refresh_export_metadata).
    SHADOW_METADATA = "shadow_metadata"
    # Rename-proof handles the re-attach paths need once the instance is gone.
    _TARGETS_PROP = "shadowRigTargets"  # JSON list of target object names
    _SOURCE_PROP = "shadowRigSource"  # ID pointer to the source object
    _CONTACT_PROP = "shadowContact"  # ID pointer to the contact empty
    # The unit 3D direction (source -> contact; a sun's own direction) the
    # silhouette was rasterized from.
    _BEARING_PROPS = ("silhouetteBearingX", "silhouetteBearingY", "silhouetteBearingZ")
    # The canvas the PNG covers, as fractions of the projection model.
    _CANVAS_PROPS = ("canvasU0", "canvasU1", "canvasW0", "canvasW1")
    _RECURSIVE_PROP = "silhouetteRecursive"
    _STALE_BEARING_DEG = 10.0
    _MATERIAL_OPACITY_PATH = 'nodes["opacity"].outputs[0].default_value'
    #: Rig types, in the order the panel lists them. ``projected`` draws one
    #: silhouette the drivers re-place; ``horizon`` adds a coverage-aware
    #: horizon map (``pythontk.ShadowHorizon``) the engine samples per frame
    #: so the outline follows a runtime light — see
    #: ``mayatk/docs/shadow_rig_morphing.md``.
    RIG_TYPES = ("projected", "horizon")
    _TYPE_PROP = "shadowRigType"
    #: The engine places the quad from the source object at runtime while this
    #: is on; off leaves the imported keys alone.
    FOLLOW_ATTR = "followSource"
    #: Silhouette atlas stamps: the atlas PNG's basename and the plane's inset
    #: rect in it (``scaleX, scaleY, offsetX, offsetY``, bottom-left origin).
    _ATLAS_TEX_PROP = "atlasTexture"
    _ATLAS_RECT_PROPS = ("atlasScaleX", "atlasScaleY", "atlasOffsetX", "atlasOffsetY")
    #: Horizon map stamps (the record's ``horizon`` block).
    _HORIZON_TEX_PROP = "horizonTexture"
    _HORIZON_INT_PROPS = (
        "horizonBins",
        "horizonTileW",
        "horizonTileH",
        "horizonCols",
        "horizonRows",
    )
    # ``horizonMaxStretch`` is the scale the map's cotangents were encoded
    # with, kept apart from the plane's live ``maxStretch`` (which the user
    # can retune afterwards, and which caps the placement): decoding the
    # map with anything but its own scale mis-reads every shadow length.
    _HORIZON_FLOAT_PROPS = ("horizonRmin", "horizonRmax", "horizonMaxStretch")
    _HORIZON_RECT_PROPS = (
        "horizonScaleX",
        "horizonScaleY",
        "horizonOffsetX",
        "horizonOffsetY",
    )
    _HORIZON_HASH_PROP = "horizonHash"
    #: The atlas the horizon block was packed into (the record's
    #: ``horizon.texture`` while packed) and the per-plane silhouette PNG a
    #: packed plane's image node no longer names.
    _HORIZON_ATLAS_PROP = "horizonAtlas"
    _SILHOUETTE_PROP = "silhouetteTexture"
    #: Each packed tile's pixel rect (row0, row1, col0, col1): Recalculate
    #: rewrites the texels in place without a repack.
    _ATLAS_PIXEL_PROPS = ("atlasRow0", "atlasRow1", "atlasCol0", "atlasCol1")
    _HORIZON_PIXEL_PROPS = ("horizonRow0", "horizonRow1", "horizonCol0", "horizonCol1")
    #: The map's bearing frame in FBX / glTF axes (right-handed Y-up), the
    #: contract's ``frame_a`` / ``frame_b``: the contact's local +X and the
    #: axis bearings increase toward. **The one place the twins differ by
    #: axis.** The frame is the contact's own local frame, so both DCCs mean
    #: "local +X, then the horizontal axis 90 deg from it" — but the vectors
    #: are written in the EXPORTER's axes: Maya's local +Z exports as FBX +Z,
    #: while Blender's exporter maps local +Y to FBX -Z, so the same rotation
    #: is ``(0, 0, -1)`` here (``mayatk/docs/shadow_rig_morphing.md``).
    HORIZON_FRAME = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0))
    #: The ``shadow_metadata`` schema this producer writes.
    METADATA_VERSION = 2
    #: Atlas PNG per rig type, beside the silhouettes in the output dir.
    ATLAS_BASENAMES = {
        "projected": "shadow_atlas_projected.png",
        "horizon": "shadow_atlas_horizon.png",
    }
    # The plane mesh is built at unit size, so a baked scale reads as the
    # canvas extent in world units.
    PLANE_SIZE = 1.0
    # Driven intermediates of the model: level 1 on the rig group, level 2 on
    # the contact empty (see the module docstring on why two IDs).
    _GROUP_PROPS = (
        "sr_Lz",
        "sr_kb",
        "sr_kt",
        "sr_reach",
        "sr_ux",
        "sr_uy",
        "sr_Sx",
        "sr_Sy",
    )
    _CONTACT_PROPS = ("sr_len", "sr_wid", "sr_cu", "sr_cw", "sr_clen")
    # Short driver-variable names for the plane's stamps.
    _PLANE_VARS = {
        "gh": "groundHeight",
        "lim": "maxStretch",
        "objH": "objectHeight",
        "r": "footprintRadius",
        "size": "basePlaneSize",
        "u0": "canvasU0",
        "u1": "canvasU1",
        "w0": "canvasW0",
        "w1": "canvasW1",
        "i": "shadowIntensity",
        "pw": "falloffPower",
        "fh": "fadeHeight",
    }

    def __init__(
        self,
        targets=None,
        ground_height=0.0,
        mode="orbit",
        source_name=None,
        name_base=None,
    ):
        super().__init__()
        objs = ptk.make_iterable(targets) if targets is not None else []
        self.targets = [o for o in (RigUtils.resolve_object(t) for t in objs) if o]
        self.ground_height = float(ground_height)
        self.mode = self._resolve_mode(mode)

        self.light = None
        self.contact = None
        self.shadow_plane = None
        self.material = None
        self.image = None
        self.texture_path = None
        self.group = None
        self.plane_size = self.PLANE_SIZE
        self.object_height = 0.0
        self.footprint_radius = 0.0
        self.canvas = None  # (u0, u1, w0, w1) fractions, once rasterized
        self.rig_type = self.RIG_TYPES[0]
        self.horizon_path = None  # the horizon map PNG, once baked

        if name_base is not None:
            # Re-attaching to an existing rig: the base is the plane's own and
            # must not be uniquified against itself.
            self._base = str(name_base)
            return

        # Naming base — first target, or "combined" for a multi-target shadow —
        # uniquified against existing rigs (Maya parity): every rig object,
        # material, image, and texture file is named off this base, and a
        # collision (two multi-target "combined" rigs, or re-creating a
        # target's rig) would hijack the older rig's material — create_material
        # rebuilds the reused node tree, killing that rig's opacity driver —
        # and overwrite its silhouette PNG.
        import bpy

        base = self.targets[0].name if len(self.targets) == 1 else "combined"
        # One plane per source: a non-default source joins the base so two
        # sources on one target get distinct objects and PNGs, while the
        # default keeps every existing name (Box_shadow, Box_shadow.png).
        if source_name and str(source_name) != self.DEFAULT_SOURCE_NAME:
            base = f"{base}_{source_name}"
        i, unique = 0, base
        while bpy.data.objects.get(f"{unique}_shadow_grp") is not None:
            i += 1
            unique = f"{base}{i}"
        self._base = unique

    @classmethod
    def _resolve_mode(cls, mode):
        """The live mode for *mode*, warning once per build on a retired alias."""
        mode = str(mode or "orbit").lower()
        if mode in cls._DEPRECATED_MODES:
            live = cls._DEPRECATED_MODES[mode]
            cls.logger.warning(
                f"ShadowRig mode '{mode}' is retired and builds as '{live}': the "
                "axis-aligned plane placed the silhouette upside down for any "
                "light on the +Y side."
            )
            return live
        return mode if mode in cls.MODES else "orbit"

    # ------------------------------------------------------------------ handles
    @staticmethod
    def has_mesh_geometry(obj, recursive=True):
        """Does *obj* carry (or, with *recursive*, contain) mesh geometry a
        shadow can be cast from? False for lights, empties, empty groups."""
        obj = RigUtils.resolve_object(obj)
        if obj is None:
            return False
        if obj.type == "MESH":
            return True
        return bool(recursive) and any(c.type == "MESH" for c in obj.children_recursive)

    def create_contact_locator(self):
        """Empty at the footprint's lowest point (min-Z), parented to the first target so it tracks."""
        lo, hi = self._world_bounds()
        loc = (
            (lo[0] + hi[0]) * 0.5,
            (lo[1] + hi[1]) * 0.5,
            lo[2],
        )  # center XY, min Z (ground-up)
        self.contact = RigUtils.create_locator(
            f"{self._base}_contact", location=loc, display_type="PLAIN_AXES", size=0.2
        )
        RigUtils.parent_keep_transform(self.contact, self.targets[0])
        return self.contact

    @classmethod
    def ensure_source(cls, source_name=DEFAULT_SOURCE_NAME, position=(5.0, 5.0, 10.0)):
        """The object named *source_name*, created as an Empty if absent.

        Any object is a valid source — the drivers read its world position —
        so a light (e.g. one built by ``LightUtils.lights_from_geometry``)
        resolves here as-is; a ``SUN`` light is projected along its direction
        instead. The panel calls this at preview enable, OUTSIDE the
        preview's datablock snapshot, so a source the user then positions
        survives every refresh and the commit (built inside the pass it was
        purged and recreated at the default position on each).
        """
        import bpy

        if not isinstance(source_name, str) and source_name is not None:
            return source_name  # an object
        source_name = str(source_name or cls.DEFAULT_SOURCE_NAME)
        existing = bpy.data.objects.get(source_name)
        if existing is not None:
            return existing
        return RigUtils.create_locator(
            source_name, location=position, display_type="SPHERE", size=1.0
        )

    def get_or_create_shadow_source(
        self, position=(5.0, 5.0, 10.0), source_name=DEFAULT_SOURCE_NAME
    ):
        """Bind this rig to :meth:`ensure_source`'s object for *source_name*."""
        self.light = self.ensure_source(source_name, position)
        return self.light

    @staticmethod
    def source_is_directional(obj):
        """Is *obj* projected along its direction (a ``SUN`` light) rather than
        from its position?"""
        obj = RigUtils.resolve_object(obj)
        return (
            obj is not None
            and obj.type == "LIGHT"
            and getattr(obj.data, "type", None) == "SUN"
        )

    def _source_ray(self):
        """``(position, direction)`` of the source in world space — one of the
        two is None: a sun shines along its world -Z axis, any other object
        casts from where it sits."""
        if self.light is None:
            raise ValueError("The shadow source is missing.")
        mw = self.light.matrix_world
        if self.source_is_directional(self.light):
            d = -(mw.col[2].xyz)
            if d.length < 1e-12:
                return None, (0.0, 0.0, -1.0)
            d.normalize()
            return None, (float(d.x), float(d.y), float(d.z))
        t = mw.translation
        return (float(t.x), float(t.y), float(t.z)), None

    def _source_size(self):
        """The source's physical size, the penumbra's cause: a sun's angular
        diameter (radians), an area light's world diameter (its size x world
        scale), a point/spot light's soft radius x2; 0 (sharp) for an empty."""
        obj = self.light
        if obj is None or obj.type != "LIGHT":
            return 0.0
        data = obj.data
        if data.type == "SUN":
            return float(data.angle)
        if data.type == "AREA":
            sx, sy, _ = obj.matrix_world.to_scale()
            w = float(data.size)
            h = float(
                data.size_y if data.shape in ("RECTANGLE", "ELLIPSE") else data.size
            )
            return 0.5 * (w * abs(sx) + h * abs(sy))
        return 2.0 * float(getattr(data, "shadow_soft_size", 0.0))

    # ------------------------------------------------------------------ measure
    def _world_bounds(self):
        """``(min_xyz, max_xyz)`` world AABB over the targets **and their descendant geometry**
        (Maya's ``exactWorldBoundingBox`` includes descendants regardless of the silhouette's
        recursive flag — so a group/empty parent gets a footprint from its mesh children, not the
        empty's meaningless unit ``bound_box``). Measured on the EVALUATED depsgraph: Maya's
        bbox is post-deformation and the silhouette gather already reads evaluated meshes, so a
        modifier (array/mirror/subsurf) must widen the footprint and contact the same way it
        widens the silhouette."""
        import bpy
        from mathutils import Vector

        deps = bpy.context.evaluated_depsgraph_get()
        objs = []
        for o in self.targets:
            objs.append(o)
            objs.extend(o.children_recursive)
        pts = []
        for o in objs:
            if (
                o is None or o.type == "EMPTY"
            ):  # an empty's bound_box is a unit cube → skip
                continue
            ev = o.evaluated_get(deps)
            for corner in ev.bound_box:
                pts.append(ev.matrix_world @ Vector(corner))
        if not pts:
            return (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
        lo = tuple(min(p[i] for p in pts) for i in range(3))
        hi = tuple(max(p[i] for p in pts) for i in range(3))
        return lo, hi

    def _measure_targets(self):
        """Stamp ``object_height`` / ``footprint_radius`` from the targets' world bounds
        (the bounding cylinder the model projects)."""
        lo, hi = self._world_bounds()
        self.object_height = max(hi[2] - lo[2], 0.001)
        self.footprint_radius = max(
            0.5 * math.hypot(hi[0] - lo[0], hi[1] - lo[1]), 0.001
        )
        return lo, hi

    def _contact_point(self):
        """The contact empty's world position (the model's base centre), or the measured
        footprint centre on the targets' underside before it exists."""
        if self.contact is not None:
            t = self.contact.matrix_world.translation
            return (float(t.x), float(t.y), float(t.z))
        lo, hi = self._world_bounds()
        return ((lo[0] + hi[0]) * 0.5, (lo[1] + hi[1]) * 0.5, lo[2])

    def _max_stretch(self):
        """The plane's ``maxStretch`` (the reach cap, in object heights)."""
        if (
            self.shadow_plane is not None
            and self.shadow_plane.get("maxStretch") is not None
        ):
            return float(self.shadow_plane["maxStretch"])
        return ptk.ShadowProjection.DEFAULT_MAX_STRETCH

    def current_model(self):
        """The projection model at the source's CURRENT position, for the stamped
        measurements — what the drivers evaluate right now."""
        position, direction = self._source_ray()
        return ptk.ShadowProjection.model(
            self._contact_point(),
            position,
            self.ground_height,
            self.footprint_radius,
            self.object_height,
            up=2,
            direction=direction,
            max_stretch=self._max_stretch(),
        )

    def _current_bearing(self):
        """Unit 3D direction from the source to the contact (a sun: its direction) — the
        stale check's yardstick."""
        position, direction = self._source_ray()
        if direction is not None:
            return tuple(direction)
        c = self._contact_point()
        d = [c[i] - position[i] for i in range(3)]
        n = math.sqrt(sum(v * v for v in d))
        return tuple(v / n for v in d) if n > 1e-9 else (0.0, 0.0, -1.0)

    # ------------------------------------------------------------------ plane
    def create_shadow_plane(self):
        """Create the unit quad on the XY ground (normal +Z), centred at the footprint, with
        the keyable shadow props (``shadowIntensity`` / ``falloffPower`` / ``maxStretch`` /
        ``fadeHeight`` / ``groundHeight`` / ``opacity``) + the measured constants and canvas
        stamps the drivers read (``basePlaneSize`` / ``objectHeight`` / ``footprintRadius`` /
        ``canvasU0..W1``) and the silhouette bearing stamp."""
        if not self.targets:
            raise ValueError("Target object(s) required")

        lo, hi = self._measure_targets()
        self.plane_size = self.PLANE_SIZE
        cx, cy = (lo[0] + hi[0]) * 0.5, (lo[1] + hi[1]) * 0.5

        self.shadow_plane = self._build_plane(
            f"{self._base}_shadow", self.plane_size, "center"
        )
        self.shadow_plane.location = (cx, cy, self.ground_height + self.GROUND_OFFSET)

        p = self.shadow_plane
        RigUtils.ensure_custom_prop(p, "shadowIntensity", 1.0, 0.0, 1.0)
        RigUtils.ensure_custom_prop(p, "falloffPower", 1.2, 0.0, 5.0)
        # Cap on the shadow's reach, in object heights (a sun at 9.5 deg).
        RigUtils.ensure_custom_prop(
            p, "maxStretch", ptk.ShadowProjection.DEFAULT_MAX_STRETCH, 0.0, 20.0
        )
        # Rise above the ground at which the shadow has fully faded out.
        RigUtils.ensure_custom_prop(
            p, "fadeHeight", max(2.0 * self.object_height, 0.001), 0.0
        )
        # The fade channel (Maya's RenderOpacity preset: keyable, 0..1).
        RigUtils.ensure_custom_prop(p, self.OPACITY_ATTR, 1.0, 0.0, 1.0)
        # World Z of the ground — a prop the drivers read, not a literal.
        RigUtils.ensure_custom_prop(p, "groundHeight", self.ground_height)
        # Measured constants are read live by the drivers; always (re)stamp to this build's values.
        RigUtils.ensure_custom_prop(p, "basePlaneSize", self.plane_size, 0.0)
        RigUtils.ensure_custom_prop(p, "objectHeight", self.object_height, 0.0)
        RigUtils.ensure_custom_prop(p, "footprintRadius", self.footprint_radius, 0.0)
        for prop, dv in zip(self._CANVAS_PROPS, (-1.0, 1.0, -0.5, 0.5)):
            RigUtils.ensure_custom_prop(p, prop, dv)
        for prop in self._BEARING_PROPS:
            RigUtils.ensure_custom_prop(p, prop, 0.0)
        RigUtils.ensure_custom_prop(p, "sourceSize", 0.0, 0.0)
        # The rig type (the record's ``type``) and the runtime-placement flag.
        p[self._TYPE_PROP] = self.rig_type
        RigUtils.ensure_custom_prop(p, self.FOLLOW_ATTR, 1, 0, 1)
        p["groundHeight"] = self.ground_height
        p["basePlaneSize"] = self.plane_size
        p["objectHeight"] = self.object_height
        p["footprintRadius"] = self.footprint_radius
        p[self._RECURSIVE_PROP] = True
        return p

    @staticmethod
    def _build_plane(name, size, origin="center"):
        """A single-quad XY plane mesh (4 verts, full 0-1 UV) — no ``bpy.ops`` (so it is
        context-free / preview-safe). ``origin='center'`` centres the verts (the canvas centre
        is what the drivers place); ``origin='edge'`` puts the origin on the -Y edge. ``V = 0``
        lies on the local -Y edge — the light-side edge of the rasterized canvas, which
        Blender's bottom-up pixel rows put there — and ``U = 0`` on the local -X edge."""
        import bpy

        r = size * 0.5
        y0, y1 = (0.0, size) if origin == "edge" else (-r, r)
        me = bpy.data.meshes.new(f"{name}_mesh")
        verts = [(-r, y0, 0.0), (r, y0, 0.0), (r, y1, 0.0), (-r, y1, 0.0)]
        me.from_pydata(verts, [], [(0, 1, 2, 3)])
        me.update()
        uv = me.uv_layers.new(name="UVMap")
        for i, co in enumerate(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))):
            uv.data[i].uv = co
        obj = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(obj)
        return obj

    # ------------------------------------------------------------------ silhouette
    def _stamp_bearing(self, bearing, recursive):
        """Record the unit 3D direction the silhouette was rasterized from and the
        descendant flag on the plane."""
        p = self.shadow_plane
        if p is None:
            return
        for prop, value in zip(self._BEARING_PROPS, bearing):
            p[prop] = float(value)
        p[self._RECURSIVE_PROP] = bool(recursive)
        p.update_tag()

    def _stamp_canvas(self, fractions, source_size):
        """Record the canvas fractions the PNG covers (the drivers re-place the plane from
        them) and the source size drawn into it."""
        self.canvas = tuple(float(f) for f in fractions)
        p = self.shadow_plane
        if p is None:
            return
        for prop, value in zip(self._CANVAS_PROPS, self.canvas):
            p[prop] = value
        p["sourceSize"] = float(source_size)
        # A restamp must reach the drivers reading these props (the
        # depsgraph does not watch ID-property writes on its own).
        p.update_tag()

    def create_silhouette_texture(
        self,
        size=512,
        axis="auto",
        recursive=True,
        path=None,
        *,
        refit=True,
        source_size=None,
        uniform_alpha=True,
        falloff_power=0.8,
        vertical_weight=0.3,
        blur_amount=1.0,
    ):
        """Rasterize the targets' shadow — their evaluated geometry projected onto the ground
        through the source — via ``pythontk.ImgUtils.rasterize_shadow``, and load it as a
        reusable image datablock.

        ``axis`` is retired (the silhouette is always the projection; any other value warns).
        ``path`` overwrites a rig's existing PNG in place (:meth:`refresh_silhouette`) so the
        image datablock and the engine join key stay valid. ``refit`` fits the canvas to the new
        projection and restamps the plane (a live rig); False draws into the canvas the stamped
        fractions denote at the source's current position (a baked plane, whose keys already
        place it there). ``source_size`` overrides the size read off the source (a sun's angular
        diameter in radians, otherwise world units).
        """
        if str(axis).lower() not in ("auto", "light"):
            self.logger.warning(
                f"ShadowRig axis={axis!r} is retired and ignored: the silhouette is "
                "the target's projection through the source."
            )
        meshes = self._gather_world_meshes(recursive)
        if not meshes:
            raise ValueError("No mesh geometry found on the target(s).")
        if not self.object_height or not self.footprint_radius:
            self._measure_targets()

        position, direction = self._source_ray()
        if source_size is None:
            source_size = self._source_size()
        canvas = None
        if not refit and self.canvas is not None:
            canvas = self.current_model().rect(self.canvas)
        # The canvas is measured in the frame the DRIVERS place the plane in —
        # the contact empty and the stamped constants — not one the raster
        # would re-derive from the meshes (a rotated target moves the two apart).
        arr, raster = ptk.ImgUtils.rasterize_shadow(
            meshes,
            position,
            self.ground_height,
            size=size,
            up=2,
            direction=direction,
            source_size=source_size,
            max_stretch=self._max_stretch(),
            canvas=canvas,
            contact=self._contact_point(),
            radius=self.footprint_radius,
            height=self.object_height,
            uniform_alpha=uniform_alpha,
            falloff_power=falloff_power,
            vertical_weight=vertical_weight,
            blur_amount=blur_amount,
        )

        self.texture_path = (
            str(path)
            if path
            else os.path.join(self._output_dir(), f"{self._base}_shadow.png")
        )
        # Row 0 of the array is the light-side edge; Blender's pixel rows run
        # bottom-up, so it lands on V = 0 — the plane's local -Y (near) edge.
        self.image = self._save_image(
            f"{self._base}_shadow", arr, self.texture_path, flip=False
        )
        self._stamp_canvas(raster.fractions, source_size)
        self._stamp_bearing(self._current_bearing(), recursive)
        return self.texture_path

    def _gather_world_meshes(self, recursive):
        """``[(points, tris)]`` world-space arrays for every target mesh (evaluated depsgraph)."""
        import numpy as np
        import bpy

        deps = bpy.context.evaluated_depsgraph_get()
        objs = []
        for t in self.targets:
            objs.append(t)
            if recursive:
                objs.extend(c for c in t.children_recursive)
        seen, out = set(), []
        for o in objs:
            if o is None or o.type != "MESH" or o.name in seen:
                continue
            seen.add(o.name)
            ev = o.evaluated_get(deps)
            me = ev.to_mesh()
            try:
                n = len(me.vertices)
                if not n:
                    continue
                co = np.empty(n * 3, dtype=np.float64)
                me.vertices.foreach_get("co", co)
                local = np.column_stack([co.reshape(-1, 3), np.ones(n)])
                world = (local @ np.array(o.matrix_world, dtype=np.float64).T)[:, :3]
                me.calc_loop_triangles()
                m = len(me.loop_triangles)
                if not m:
                    continue
                tri = np.empty(m * 3, dtype=np.int64)
                me.loop_triangles.foreach_get("vertices", tri)
                out.append((world, tri.reshape(-1, 3)))
            finally:
                ev.to_mesh_clear()
        return out

    @staticmethod
    def _output_dir():
        """Where the silhouette PNG is written — a ``sourceimages`` next to the .blend if saved,
        else a temp dir."""
        import tempfile
        import bpy

        if bpy.data.filepath:
            d = os.path.join(os.path.dirname(bpy.data.filepath), "sourceimages")
        else:
            d = os.path.join(tempfile.gettempdir(), "blendertk_shadows")
        os.makedirs(d, exist_ok=True)
        return d

    # ------------------------------------------------------------------ image IO
    # mayatk reads and writes the rig's PNGs with PIL; blendertk has no such
    # dependency and goes through ``bpy.data.images`` — one datablock per file,
    # so the material samples exactly what was written and a rewrite reaches
    # the viewport without a reload.
    @classmethod
    def _save_image(cls, name, arr, path, *, flip=False):
        """Write the ``(h, w, 4)`` uint8 array *arr* to *path* through the
        ``bpy.data.images`` datablock *name*, and return the datablock.

        An existing image of a different size cannot be resized in place, so
        it is removed and recreated; a read-only dir / locked file warns and
        keeps the in-memory datablock. Blender's pixel buffer runs BOTTOM-up:
        ``flip=False`` lays row 0 of *arr* on the image's ``V = 0`` edge (the
        silhouette's light-side edge), ``flip=True`` writes *arr* top-down so
        the saved PNG's TOP row is row 0 — what the horizon map's contract
        pins (``r_min`` on the top row) and what the atlas' top-down pixel
        rects assume. ``Non-Color``: the maps are DATA (cotangents, occupancy
        bits, a coverage alpha), so the sRGB transform ``Image.pixels`` would
        otherwise apply must not touch them; the silhouette's RGB is black
        either way.
        """
        import numpy as np
        import bpy

        arr = np.asarray(arr)
        h, w = int(arr.shape[0]), int(arr.shape[1])
        img = bpy.data.images.get(name)
        if img is not None and tuple(img.size) != (w, h):
            bpy.data.images.remove(img)  # resolution changed — no in-place resize
            img = None
        if img is None:
            img = bpy.data.images.new(name, w, h, alpha=True)
        # BEFORE the pixel write — the transform is applied on assignment.
        img.colorspace_settings.name = "Non-Color"
        buf = arr[::-1] if flip else arr
        img.pixels.foreach_set((np.asarray(buf, dtype=np.float32) / 255.0).ravel())
        img.update()
        img.filepath_raw = str(path)
        img.file_format = "PNG"
        try:
            img.save()
        except RuntimeError as e:  # read-only dir / locked file
            cls.logger.warning(
                f"Could not save {os.path.basename(str(path))} ({e}); "
                "using the in-memory texture."
            )
        return img

    @staticmethod
    def _read_png(path):
        """The PNG at *path* as a ``(h, w, 4)`` uint8 array, TOP row first —
        the orientation mayatk's PIL reads give, so the atlas arithmetic is
        the same in both twins. Returns None when it cannot be read."""
        import numpy as np
        import bpy

        img = None
        try:
            img = bpy.data.images.load(str(path), check_existing=False)
            img.colorspace_settings.name = "Non-Color"  # data, not colour
            w, h = int(img.size[0]), int(img.size[1])
            if not (w and h):
                return None
            buf = np.empty(w * h * int(img.channels), dtype=np.float32)
            img.pixels.foreach_get(buf)
            arr = buf.reshape(h, w, int(img.channels))[::-1]
            return np.clip(np.rint(arr * 255.0), 0, 255).astype(np.uint8)
        except (RuntimeError, ValueError):
            return None
        finally:
            if img is not None:
                bpy.data.images.remove(img)

    @classmethod
    def _texture_size(cls, path):
        """Pixel width of the PNG at *path*, or None (mirror of mayatk's)."""
        import bpy

        if not path or not os.path.exists(path):
            return None
        img = None
        try:
            img = bpy.data.images.load(str(path), check_existing=False)
            return int(img.size[0]) or None
        except RuntimeError:
            return None
        finally:
            if img is not None:
                bpy.data.images.remove(img)

    # ------------------------------------------------------------------ material (unlit transparent)
    def create_material(self):
        """Unlit black Emission mixed with a Transparent BSDF by ``tex.alpha × opacity`` (opacity a
        driven Value node). Reused by name so a preview refresh / rebuild doesn't orphan materials."""
        import bpy

        if self.image is None:
            raise ValueError("Texture not created yet")

        mat_name = f"{self._base}_shadow_mat"
        mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()

        out = nt.nodes.new("ShaderNodeOutputMaterial")
        mix = nt.nodes.new("ShaderNodeMixShader")
        transp = nt.nodes.new("ShaderNodeBsdfTransparent")
        emis = nt.nodes.new("ShaderNodeEmission")
        emis.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)  # black shadow
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.name = "shadow_tex"
        tex.image = self.image
        opacity = nt.nodes.new("ShaderNodeValue")
        opacity.name = "opacity"  # nt.nodes.clear() above frees this name → no suffix
        opacity.outputs[0].default_value = 1.0
        mult = nt.nodes.new("ShaderNodeMath")
        mult.operation = "MULTIPLY"

        nt.links.new(tex.outputs["Alpha"], mult.inputs[0])
        nt.links.new(opacity.outputs[0], mult.inputs[1])
        nt.links.new(mult.outputs[0], mix.inputs["Fac"])
        nt.links.new(transp.outputs[0], mix.inputs[1])
        nt.links.new(emis.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs["Surface"])

        # Legacy-EEVEE transparency knobs; EEVEE-Next (Blender 4.2+) dropped them (alpha is
        # socket-driven via the Transparent/Mix graph), so guard rather than require.
        for attr, val in (("blend_method", "BLEND"), ("shadow_method", "NONE")):
            try:
                setattr(mat, attr, val)
            except (AttributeError, TypeError):
                pass

        self.shadow_plane.data.materials.clear()
        self.shadow_plane.data.materials.append(mat)
        self.material = mat
        return mat

    # ------------------------------------------------------------------ drivers
    def setup_drivers(self):
        """Build the model's driver chain (group -> contact -> plane), the material's opacity
        driver and the plane's opacity mirror, then force one recompile."""
        if self.group is None or self.contact is None or self.material is None:
            raise ValueError(
                "The rig's group, contact and material must exist before its drivers."
            )
        self._drivers_model()
        self._prop_mirror_driver()
        # Script-built drivers cache a stale compile until the depsgraph settles + each expression is
        # re-assigned (the shared RigUtils gotcha).
        RigUtils.refresh_drivers(
            [self.group, self.contact, self.shadow_plane, self.material.node_tree]
        )

    def _plane_vars(self, *names):
        """``SINGLE_PROP`` var specs for the plane's stamps by their short driver names."""
        return [(n, self.shadow_plane, f'["{self._PLANE_VARS[n]}"]') for n in names]

    def _level1(self):
        """Level-1 driver set on the group: ``{prop: (expression, transform vars,
        property vars)}`` — the effective source height (for the height fade), the
        base/top projection factors, the reach, the bearing and the anchor, each read
        from primitives only (the source's transform or a sun's ``matrix_world``
        column, the contact's transform, the plane's stamps).

        A positional source is the model verbatim. A sun gets the parallel-projection
        closed forms instead of the far-point trick: with the intermediates stored as
        single-precision properties, ``dist x (k_top - k_base)`` at a far point
        multiplies a cancelling difference and lands 10-20% off; ``k = 1``,
        ``reach = height x |d_h| / |d_z|`` and ``anchor = C + d_h x (C_z - G) / |d_z|``
        are exact and short. ``m0..m2`` are the sun's world Z axis (its direction is
        ``-Z``), so ``d_h = (-m0, -m1)`` and ``|d_z| = m2``. Read as
        ``matrix_world[2][i]``: an RNA path indexes Blender's column-major matrix
        storage (``[column][row]``), the transpose of ``mathutils.Matrix``'s
        ``[row][col]`` — ``[i][2]`` would read each axis's Z component instead.
        """
        L, C = self.light, self.contact
        cv = [("Cx", C, "LOC_X"), ("Cy", C, "LOC_Y"), ("Cz", C, "LOC_Z")]
        pv = self._plane_vars("gh", "lim", "objH")
        if self.source_is_directional(L):
            mv = [(f"m{i}", L, f"matrix_world[2][{i}]") for i in range(3)]
            H = "sqrt(m0**2+m1**2)"  # |d_h|
            return {
                # Above the horizon (m2 > 0) the height fade saturates at 1.
                "sr_Lz": ("Cz+m2*1e4", cv, mv),
                "sr_kb": ("1", cv, []),
                "sr_kt": ("1", cv, []),
                "sr_reach": (f"min(lim*objH,objH*{H}/max(m2,1e-6))", [], mv + pv),
                "sr_ux": (f"-m0/max({H},1e-6)", [], mv),
                "sr_uy": (f"-m1/max({H},1e-6)+max(0,1-{H}*1e6)", [], mv),
                "sr_Sx": ("Cx-m0*(Cz-gh)/max(m2,1e-6)", cv, mv + pv),
                "sr_Sy": ("Cy-m1*(Cz-gh)/max(m2,1e-6)", cv, mv + pv),
            }
        lv = [("Lx", L, "LOC_X"), ("Ly", L, "LOC_Y"), ("Lz", L, "LOC_Z")]
        # k = (L - G) / (L - disk height), each clamped; the top's factor is
        # additionally capped so the reach stays under maxStretch heights.
        KB = "min(max((Lz-gh)/max(Lz-Cz,1e-4),0),1+lim)"
        DIST = "sqrt((Cx-Lx)**2+(Cy-Ly)**2)"
        KT = (
            f"min(max((Lz-gh)/max(Lz-Cz-objH,1e-4),0),"
            f"min(1+lim,{KB}+lim*objH/max({DIST},1e-6)))"
        )
        return {
            "sr_Lz": ("Lz", cv + lv, pv),
            "sr_kb": (KB, cv + lv, pv),
            "sr_kt": (KT, cv + lv, pv),
            "sr_reach": (f"max(0,{DIST}*({KT}-{KB}))", cv + lv, pv),
            # Bearing u (away from the source); straight overhead falls back to +Y.
            "sr_ux": (f"(Cx-Lx)/max({DIST},1e-6)", cv + lv, []),
            "sr_uy": (f"(Cy-Ly)/max({DIST},1e-6)+max(0,1-{DIST}*1e6)", cv + lv, []),
            # Anchor: where the base centre lands on the ground.
            "sr_Sx": (f"Lx+(Cx-Lx)*{KB}", cv + lv, pv),
            "sr_Sy": (f"Ly+(Cy-Ly)*{KB}", cv + lv, pv),
        }

    def _drivers_model(self):
        """The projection model (pythontk ``ShadowProjection.model``) as a driver chain.

        Level 1 (group, :meth:`_level1`): the source height, the base/top projection
        factors, the reach, the bearing and the anchor. Level 2 (contact): the model's
        length and width, and the canvas's centre offsets and length — the canvas's
        near edge is stamped in projected-footprint radii from the anchor (pinned to a
        grounded target's feet), its far edge in projected-head radii from where the
        head lands. The plane's channels place the canvas from both. Every expression
        is branchless and under the 255-char cap.
        """
        p, C, g = self.shadow_plane, self.contact, self.group
        for name, (expr, loc_vars, prop_vars) in self._level1().items():
            RigUtils.ensure_custom_prop(g, name, 0.0)
            self._scripted_driver(g, f'["{name}"]', None, expr, loc_vars, prop_vars)

        grp_vars = [(n, g, f'["sr_{n}"]') for n in ("reach", "kb", "kt")]
        level2 = {
            "sr_len": "reach+r*(kt+kb)",
            "sr_wid": "2*r*max(kt,kb)",
            "sr_cu": "0.5*(u0*r*kb+reach+u1*r*kt)",
            "sr_cw": "(w0+w1)*r*max(kt,kb)",
            "sr_clen": "max(1e-4,reach+r*(u1*kt-u0*kb))",
        }
        for name, expr in level2.items():
            RigUtils.ensure_custom_prop(C, name, 0.0)
            self._scripted_driver(
                C,
                f'["{name}"]',
                None,
                expr,
                [],
                grp_vars + self._plane_vars("r", "u0", "u1", "w0", "w1"),
            )

        bearing_vars = [(n, g, f'["sr_{n}"]') for n in ("ux", "uy", "Sx", "Sy")]
        canvas_vars = [
            (n, C, f'["sr_{n}"]') for n in ("cu", "cw", "len", "wid", "clen")
        ]
        # w (the plane's local +X) = (uy, -ux).
        self._scripted_driver(
            p, "location", 0, "Sx+ux*cu+uy*cw", [], bearing_vars + canvas_vars
        )
        self._scripted_driver(
            p, "location", 1, "Sy+uy*cu-ux*cw", [], bearing_vars + canvas_vars
        )
        self._scripted_driver(
            p, "location", 2, f"gh+{self.GROUND_OFFSET}", [], self._plane_vars("gh")
        )
        # R_z(t) sends +Y to (-sin t, cos t): the head (+Y) points along u.
        self._scripted_driver(p, "rotation_euler", 2, "atan2(-ux,uy)", [], bearing_vars)
        self._scripted_driver(
            p,
            "scale",
            0,
            "max(1e-4,(w1-w0)*wid/size)",
            [],
            canvas_vars + self._plane_vars("w0", "w1", "size"),
        )
        self._scripted_driver(
            p,
            "scale",
            1,
            "max(1e-4,clen/size)",
            [],
            canvas_vars + self._plane_vars("size"),
        )
        self.shadow_plane.scale[2] = 1.0
        # Opacity = elongation falloff x source-height fade x rise fade. Lives on the
        # material's Value node (a different ID), reading the contact's driven length.
        self._scripted_driver(
            self.material.node_tree,
            self._MATERIAL_OPACITY_PATH,
            None,
            "min(max(i/max(0.001,pow(max(1,len/max(1e-4,2*r)),pw))"
            "*min(max(Lz-Cz,0),1)*min(max(1-max(Cz-gh,0)/max(0.001,fh),0),1),0),1)",
            [("Cz", C, "LOC_Z")],
            [("Lz", g, '["sr_Lz"]'), ("len", C, '["sr_len"]')]
            + self._plane_vars("i", "pw", "fh", "gh", "r"),
        )

    @staticmethod
    def _scripted_driver(obj, data_path, index, expression, loc_vars, prop_vars):
        """Build one re-entrant SCRIPTED driver on ``obj.<data_path>[index]`` from
        world-transform + property variables. ``loc_vars``: ``[(name, target,
        transform_type)]``; ``prop_vars``: ``[(name, id_obj, data_path)]``. Returns the fcurve."""
        RigUtils.remove_driver(obj, data_path, index)
        fc = RigUtils._driver_add(obj, data_path, index)
        fc.driver.type = "SCRIPTED"
        for name, tgt, tt in loc_vars:
            RigUtils.add_transform_var(fc, name, tgt, tt)
        for name, idobj, dpath in prop_vars:
            RigUtils.add_prop_var(fc, name, idobj, dpath)
        fc.driver.expression = expression
        return fc

    def _prop_mirror_driver(self):
        """The plane's ``opacity`` prop follows the material's driven value — the keyable,
        exportable mirror of Maya's expression-written attr."""
        p = self.shadow_plane
        RigUtils.remove_driver(p, f'["{self.OPACITY_ATTR}"]', None)
        fc = p.driver_add(f'["{self.OPACITY_ATTR}"]')
        fc.driver.type = "SCRIPTED"
        RigUtils.add_prop_var(
            fc,
            "op",
            self.material,
            f"node_tree.{self._MATERIAL_OPACITY_PATH}",
            id_type="MATERIAL",
        )
        fc.driver.expression = "op"
        return fc

    @classmethod
    def _material_follow_driver(cls, plane, material):
        """After a bake the roles swap: the material's Value node FOLLOWS the plane's keyed
        ``opacity`` prop, so the viewport shows the baked fade."""
        nt = material.node_tree
        RigUtils.remove_driver(nt, cls._MATERIAL_OPACITY_PATH, None)
        fc = nt.driver_add(cls._MATERIAL_OPACITY_PATH)
        fc.driver.type = "SCRIPTED"
        RigUtils.add_prop_var(fc, "op", plane, f'["{cls.OPACITY_ATTR}"]')
        fc.driver.expression = "op"
        RigUtils.refresh_drivers([nt])
        return fc

    @classmethod
    def _strip_drivers(cls, plane):
        """Remove the whole driver chain of *plane*'s rig: the plane's channels and opacity
        mirror, the material's opacity driver, the group's and contact's intermediates."""
        for path in ("location", "rotation_euler", "scale"):
            for i in range(3):
                RigUtils.remove_driver(plane, path, i)
        RigUtils.remove_driver(plane, f'["{cls.OPACITY_ATTR}"]', None)
        for mat in (m for m in plane.data.materials if m and m.node_tree):
            RigUtils.remove_driver(mat.node_tree, cls._MATERIAL_OPACITY_PATH, None)
        group = plane.parent
        if group is not None:
            for name in cls._GROUP_PROPS:
                RigUtils.remove_driver(group, f'["{name}"]', None)
        contact = cls._plane_contact(plane)
        if contact is not None:
            for name in cls._CONTACT_PROPS:
                RigUtils.remove_driver(contact, f'["{name}"]', None)

    @classmethod
    def plane_is_live(cls, plane):
        """Does *plane* still carry its drivers (not baked)?"""
        ad = getattr(plane, "animation_data", None)
        return bool(ad and ad.drivers)

    @classmethod
    def plane_is_baked(cls, plane):
        """Does *plane* carry baked keys (an action) on its channels?"""
        ad = getattr(plane, "animation_data", None)
        return bool(ad and ad.action is not None)

    # ------------------------------------------------------------------ bake
    def bake(self, start=None, end=None):
        """Bake this rig's driven channels to keyframes and remove the drivers (FBX-ready).
        See :meth:`bake_planes`."""
        return self.bake_planes([self.shadow_plane], start=start, end=end)

    @classmethod
    def find_shadow_planes(cls, objects=None):
        """Shadow planes = objects carrying the stamped ``basePlaneSize`` custom prop.
        ``objects`` limits the search (their descendants included, so a selected
        ``*_shadow_grp`` finds its plane); None scans the file."""
        import bpy

        if objects:
            pool = []
            for o in objects:
                o = RigUtils.resolve_object(o)
                if o is None:
                    continue
                try:
                    pool.append(o)
                    pool.extend(o.children_recursive)
                except ReferenceError:
                    continue  # stale ref (already deleted) — mirror Maya's no-op
        else:
            pool = list(bpy.data.objects)
        # Dedup: an overlapping selection (group + its plane child) lists the
        # plane twice — delete_rigs would then hit a removed object (mirror of
        # mayatk's dict.fromkeys).
        out = []
        for o in dict.fromkeys(pool):
            try:
                if o.get("basePlaneSize") is not None:
                    out.append(o)
            except ReferenceError:
                continue
        return out

    @classmethod
    def planes_for_nodes(cls, objects):
        """Shadow planes the given objects touch: the planes themselves (or their
        ``*_shadow_grp``), plus every plane whose stamps lead back to an object — a target,
        the source, the contact empty."""
        planes = cls.find_shadow_planes(objects)
        names = {
            o.name for o in (RigUtils.resolve_object(x) for x in objects or []) if o
        }
        if not names:
            return planes
        found = {p.name for p in planes}
        for plane in cls.find_shadow_planes():
            if plane.name in found:
                continue
            targets, source = cls._rig_links(plane)
            contact = cls._plane_contact(plane)
            group = plane.parent
            if (
                (source is not None and source.name in names)
                or any(t.name in names for t in targets)
                or (contact is not None and contact.name in names)
                or (group is not None and group.name in names)
            ):
                found.add(plane.name)
                planes.append(plane)
        return planes

    @classmethod
    def for_node(cls, obj):
        """The rig *obj* belongs to (a plane, its group, a target, the source, the contact),
        re-attached via :meth:`from_plane`; None when it belongs to none."""
        for plane in cls.planes_for_nodes([obj]):
            rig = cls.from_plane(plane)
            if rig is not None:
                return rig
        return None

    @classmethod
    def for_nodes(cls, objects):
        """Distinct rigs the *objects* touch (see :meth:`for_node`)."""
        rigs = []
        for plane in cls.planes_for_nodes(objects):
            rig = cls.from_plane(plane)
            if rig is not None:
                rigs.append(rig)
        return rigs

    @classmethod
    def bake_planes(cls, planes=None, start=None, end=None):
        """Bake shadow planes' driven channels — transform AND the ``opacity`` fade — to
        keyframes and remove the drivers so the result exports cleanly to FBX (mirror of
        mayatk's ``bake_planes``). The baked Z rotation is unrolled into a continuous curve
        (``atan2`` wraps at ±180° when the light crosses behind the target).

        Args:
            planes: Shadow plane object(s)/name(s); None bakes every shadow plane in the
                file that still has live drivers.
            start/end: Frame range; defaults to the scene frame range.

        Returns:
            The list of planes that were baked.
        """
        planes = cls.find_shadow_planes(planes)
        baked = []
        for p in planes:
            if p.library or p.override_library:
                # A linked/overridden rig's drivers can't be stripped from
                # here — bake in the source file instead of half-baking this
                # one (mirror of mayatk's referenced-plane skip).
                cls.logger.warning(f"Skipping linked shadow plane: {p.name}")
                continue
            if not cls.plane_is_live(p):
                continue  # already baked / hand-keyed
            cls._bake_plane(p, start, end)
            baked.append(p)
        if baked:
            cls.refresh_export_metadata()
        return baked

    @classmethod
    def _bake_plane(cls, plane, start=None, end=None):
        """Sample the evaluated (driver-driven) transform + fade per frame, strip the drivers,
        then key the samples — a context-free visual bake (no ``bpy.ops.nla.bake``)."""
        import bpy

        scene = bpy.context.scene
        start = int(scene.frame_start if start is None else start)
        end = int(scene.frame_end if end is None else end)
        cur = scene.frame_current
        materials = [m for m in plane.data.materials if m and m.node_tree]
        opacity_node = next(
            (
                m.node_tree.nodes.get("opacity")
                for m in materials
                if m.node_tree.nodes.get("opacity")
            ),
            None,
        )

        samples = []
        prev_rz = None
        for f in range(start, end + 1):
            scene.frame_set(f)
            ev = plane.evaluated_get(bpy.context.evaluated_depsgraph_get())
            rot = list(ev.rotation_euler)
            # Unroll Z: atan2 wraps at ±pi when the light crosses behind the target, and a
            # keyed -179 -> +179 pair would interpolate as a spin.
            if prev_rz is not None:
                while rot[2] - prev_rz > math.pi:
                    rot[2] -= 2.0 * math.pi
                while rot[2] - prev_rz < -math.pi:
                    rot[2] += 2.0 * math.pi
            prev_rz = rot[2]
            opacity = (
                float(opacity_node.outputs[0].default_value)
                if opacity_node is not None
                else float(ev.get(cls.OPACITY_ATTR, 1.0))
            )
            samples.append(
                (f, tuple(ev.location), tuple(rot), tuple(ev.scale), opacity)
            )
        scene.frame_set(cur)

        cls._strip_drivers(plane)
        has_opacity = plane.get(cls.OPACITY_ATTR) is not None

        for f, loc, rot, scl, opacity in samples:
            plane.location = loc
            plane.rotation_euler = rot
            plane.scale = scl
            plane.keyframe_insert("location", frame=f)
            plane.keyframe_insert("rotation_euler", frame=f)
            plane.keyframe_insert("scale", frame=f)
            if has_opacity:
                plane[cls.OPACITY_ATTR] = opacity
                plane.keyframe_insert(f'["{cls.OPACITY_ATTR}"]', frame=f)
        # The material now FOLLOWS the keyed prop (the viewport shows the baked fade); a
        # rig built before the prop existed keeps its frozen last value instead.
        for mat in materials:
            if has_opacity and mat.node_tree.nodes.get("opacity") is not None:
                cls._material_follow_driver(plane, mat)
            elif opacity_node is not None:
                opacity_node.outputs[0].default_value = (
                    samples[-1][4] if samples else 1.0
                )
        return plane

    @classmethod
    def _clear_baked_keys(cls, plane):
        """Drop the baked action (the plane carries nothing but the bake's keys) so the
        drivers can drive the channels again."""
        import bpy

        ad = plane.animation_data
        if ad is None or ad.action is None:
            return
        action = ad.action
        ad.action = None
        if action.users == 0:
            bpy.data.actions.remove(action)

    @classmethod
    def unbake_planes(cls, planes=None):
        """Restore the live drivers on baked shadow planes — the reverse of
        :meth:`bake_planes`, so a rig exported earlier can be edited again. Works off the
        stamps; a plane that predates them is skipped with a warning.

        Returns:
            The list of planes whose drivers were restored.
        """
        restored = []
        for plane in cls.find_shadow_planes(planes):
            if cls.plane_is_live(plane):
                continue
            rig = cls.from_plane(plane)
            if (
                rig is None
                or rig.group is None
                or rig.contact is None
                or rig.material is None
            ):
                cls.logger.warning(
                    f"{plane.name}: built before the stamps or missing its rig objects; "
                    "re-create the rig to restore its drivers."
                )
                continue
            cls._clear_baked_keys(plane)
            rig.setup_drivers()
            restored.append(plane)
        if restored:
            cls.refresh_export_metadata()
        return restored

    # ------------------------------------------------------------------ delete
    def delete(self, delete_textures=False):
        """Delete this rig completely. See :meth:`delete_rigs`."""
        return self.delete_rigs([self.shadow_plane], delete_textures=delete_textures)

    @classmethod
    def delete_rigs(cls, planes=None, delete_textures=False):
        """Tear down shadow rig(s) completely — live or baked (mirror of
        mayatk's ``delete_rigs``).

        Removes, per plane: the plane and its enclosing ``*_shadow_grp``
        empty (when it holds nothing else), the contact empty (via the
        ``shadowContact`` ID-pointer prop stamped at create, with a
        name-based fallback for rigs built before it), and the material +
        silhouette image datablocks once nothing else uses them — drivers
        die with their datablocks. The targets and the shared shadow-source
        empty are left untouched; the ``shadow_metadata`` channel is
        republished afterwards.

        Args:
            planes: Shadow plane object(s)/name(s); None deletes every
                shadow rig in the file.
            delete_textures: Also remove the silhouette PNG from disk.

        Returns:
            The list of deleted planes' names.
        """
        import bpy

        planes = cls.find_shadow_planes(planes)
        deleted = []
        repack = False
        for p in planes:
            if p.library or p.override_library:
                # Linked/overridden datablocks can't be torn down from here —
                # delete the rig in its source file (mirror of mayatk's
                # referenced-plane skip).
                cls.logger.warning(f"Skipping linked shadow plane: {p.name}")
                continue
            name = p.name
            # dict.fromkeys de-dups: the same material in two mesh slots would
            # otherwise appear twice, and the second pass through the removal
            # loop below would dereference a freed datablock (ReferenceError).
            mats = list(dict.fromkeys(m for m in p.data.materials if m))
            tex_paths = (
                [cls._plane_texture_path(p), cls._plane_horizon_path(p)]
                if delete_textures
                else []
            )
            # The plane's own tile datablock, which packing displaced out of
            # the material's image node (so the loop below can free it too).
            own_image = cls._plane_own_image(p) if cls.plane_is_atlased(p) else None
            # An atlased rig leaves its tile behind; the survivors repack below.
            atlased = any(cls._packed_in(p, kind) for kind in cls.RIG_TYPES)
            contact = cls._plane_contact(p)
            group = p.parent
            if group is not None and not (
                group.type == "EMPTY"
                and group.name.endswith("_shadow_grp")
                and len(group.children) == 1
            ):
                group = None  # never a user's own parent

            mesh = p.data
            bpy.data.objects.remove(p, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            if group is not None:
                bpy.data.objects.remove(group, do_unlink=True)
            try:
                if isinstance(contact, bpy.types.Object):
                    bpy.data.objects.remove(contact, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass  # already gone (dead pointer)
            images = [own_image] if own_image is not None else []
            for mat in mats:
                node = mat.node_tree.nodes.get("shadow_tex") if mat.node_tree else None
                img = getattr(node, "image", None) if node is not None else None
                if img is not None:
                    images.append(img)
                if mat.users == 0:
                    bpy.data.materials.remove(mat)
            # De-dup by name: two materials can share an image, and the second
            # remove() would dereference a freed datablock (ReferenceError).
            for block in {b.name: b for b in images}.values():
                if block.users == 0:
                    bpy.data.images.remove(block)
            for tex_path in tex_paths:
                if tex_path and os.path.exists(tex_path):
                    try:
                        os.remove(tex_path)
                    except OSError:
                        pass  # locked/read-only — datablock teardown already done
            deleted.append(name)
            repack = repack or atlased
        if repack:
            cls._repack_atlased()
        if deleted:
            cls.refresh_export_metadata()
        return deleted

    # ------------------------------------------------------------------ export metadata
    @staticmethod
    def _plane_texture_image(plane):
        """The plane material's silhouette image datablock (SSoT; survives retexturing)."""
        for mat in (m for m in plane.data.materials if m and m.node_tree):
            node = mat.node_tree.nodes.get("shadow_tex")
            img = getattr(node, "image", None) if node is not None else None
            if img is not None:
                return img
        return None

    @classmethod
    def _plane_texture_path(cls, plane):
        """Full path of the plane's OWN silhouette PNG (see
        :meth:`_plane_texture_image`). A packed plane's image node names the
        atlas; its tile stays beside it under the stamped ``silhouetteTexture``
        name, which is what Recalculate rewrites and the record carries."""
        import bpy

        img = cls._plane_texture_image(plane)
        bound = (
            bpy.path.abspath(img.filepath_raw)
            if (img is not None and img.filepath_raw)
            else None
        )
        own = cls._plane_prop(plane, cls._SILHOUETTE_PROP, "")
        if own and cls.plane_is_atlased(plane):
            folder = os.path.dirname(bound) if bound else cls._output_dir()
            return os.path.join(folder, own).replace("\\", "/")
        return bound

    @classmethod
    def _plane_own_image(cls, plane):
        """The datablock holding the plane's OWN silhouette — the image node's
        while unpacked, else the one packing displaced (by name, then loaded
        from disk for a rig built in an earlier session)."""
        import bpy

        if not cls.plane_is_atlased(plane):
            return cls._plane_texture_image(plane)
        path = cls._plane_texture_path(plane)
        if not path:
            return None
        img = bpy.data.images.get(os.path.splitext(os.path.basename(path))[0])
        if img is not None:
            return img
        if os.path.exists(path):
            try:
                return bpy.data.images.load(path, check_existing=True)
            except RuntimeError:
                return None
        return None

    @classmethod
    def _plane_contact(cls, plane):
        """The plane's contact empty (the ID-pointer stamp, else the naming convention)."""
        import bpy

        contact = plane.get(cls._CONTACT_PROP)
        try:
            if isinstance(contact, bpy.types.Object):
                contact.name  # a dead pointer raises here
                return contact
        except ReferenceError:
            pass
        if plane.name.endswith("_shadow"):
            return bpy.data.objects.get(f"{plane.name[: -len('_shadow')]}_contact")
        return None

    def _stamp_rig_links(self):
        """Link the plane to its targets (a JSON name list), source and contact (ID-pointer
        props, rename-proof) — the handles the re-attach paths need after the Python instance
        is gone."""
        p = self.shadow_plane
        p[self._TARGETS_PROP] = json.dumps([t.name for t in self.targets])
        p[self._SOURCE_PROP] = self.light
        if self.contact is not None:
            p[self._CONTACT_PROP] = self.contact

    @classmethod
    def _rig_links(cls, plane):
        """``(targets, source)`` the plane was built from; ``([], None)`` for rigs built
        before the stamps."""
        import bpy

        targets = []
        try:
            names = json.loads(plane.get(cls._TARGETS_PROP) or "[]")
        except (TypeError, ValueError):
            names = []
        for name in names:
            o = bpy.data.objects.get(str(name))
            if o is not None:
                targets.append(o)
        source = plane.get(cls._SOURCE_PROP)
        try:
            if not isinstance(source, bpy.types.Object):
                source = None
            else:
                source.name
        except ReferenceError:
            source = None
        return targets, source

    @classmethod
    def from_plane(cls, plane):
        """A rig instance re-attached to an existing *plane* via its stamps, or None when the
        plane predates them. Resolves what the Utility actions need: targets, source, contact,
        group, material, image, texture path, and the measured constants / canvas fractions."""
        targets, source = cls._rig_links(plane)
        if not targets or source is None:
            return None
        name = plane.name
        base = name[: -len("_shadow")] if name.endswith("_shadow") else name
        rig = cls(targets, name_base=base)
        rig.shadow_plane = plane
        rig.light = source
        rig.image = cls._plane_texture_image(plane)
        rig.texture_path = cls._plane_texture_path(plane)
        for prop, field in (
            ("groundHeight", "ground_height"),
            ("objectHeight", "object_height"),
            ("footprintRadius", "footprint_radius"),
            ("basePlaneSize", "plane_size"),
        ):
            if plane.get(prop) is not None:
                setattr(rig, field, float(plane[prop]))
        if not rig.footprint_radius:
            rig._measure_targets()  # a plane stamped before the model props
        if all(plane.get(k) is not None for k in cls._CANVAS_PROPS):
            rig.canvas = tuple(float(plane[k]) for k in cls._CANVAS_PROPS)
        rig.contact = cls._plane_contact(plane)
        group = plane.parent
        if group is not None and group.name.endswith("_shadow_grp"):
            rig.group = group
        rig.material = next((m for m in plane.data.materials if m), None)
        rig.rig_type = cls.plane_type(plane)
        rig.horizon_path = cls._plane_horizon_path(plane)
        return rig

    # Retained for one release: the pre-public spelling.
    _from_plane = from_plane

    def set_source(self, source_name, position=(5.0, 5.0, 10.0), size=None):
        """Re-point this rig at another source — an existing object or light, or an empty to
        create at *position* — and re-render its silhouette from there. A baked plane has its
        drivers restored first (its keys described the old source).

        Returns:
            The source object now linked.
        """
        self.light = self.ensure_source(source_name, position)
        if self.shadow_plane is not None and self.plane_is_baked(self.shadow_plane):
            self._clear_baked_keys(self.shadow_plane)
        self._stamp_rig_links()
        self.setup_drivers()
        self.refresh_silhouette([self.shadow_plane], size=size, refit=True)
        return self.light

    @classmethod
    def rebuild(cls, plane, texture_res=None, recursive=None):
        """Tear a rig down and build it again from its own stamps — the same targets, source
        and ground — with the targets' CURRENT geometry and, optionally, a new resolution /
        descendant rule. The name base is kept, so the engine join key survives.

        Returns:
            The new :class:`ShadowRig`, or None when *plane* predates the stamps or its
            targets / source are gone.
        """
        rig = cls.from_plane(plane)
        if rig is None:
            cls.logger.warning(
                f"{plane.name}: built before the stamps; cannot rebuild."
            )
            return None
        if recursive is None:
            recursive = bool(plane.get(cls._RECURSIVE_PROP, True))
        if texture_res is None:
            texture_res = cls._texture_size(rig.texture_path)
            if (
                not texture_res
                and rig.image is not None
                and not cls.plane_is_atlased(plane)
            ):
                texture_res = int(rig.image.size[0]) or None
            texture_res = texture_res or 512
        targets = [t for t in rig.targets if t is not None]
        source = rig.light
        if not targets or source is None:
            cls.logger.warning(
                f"{plane.name}: its targets or source are gone; cannot rebuild."
            )
            return None
        ground = rig.ground_height
        rig_type = rig.rig_type
        horizon = cls._horizon_params(plane)
        atlased = cls.plane_is_atlased(plane)
        cls.delete_rigs([plane])
        rebuilt = cls.create(
            targets,
            texture_res=texture_res,
            source_name=source.name,
            recursive=recursive,
            ground_height=ground,
            rig_type=rig_type,
            horizon_bins=horizon.get("bins"),
            horizon_size=horizon.get("tile"),
        )
        if atlased:
            # The rig left its atlas on delete; a rebuilt rig rejoins it.
            cls.pack_atlas([rebuilt.shadow_plane])
        return rebuilt

    @classmethod
    def silhouette_is_stale(cls, plane):
        """Has the source moved past :attr:`_STALE_BEARING_DEG` from the direction the plane's
        silhouette was rasterized from? False when unknowable (a rig built before the stamps)."""
        rig = cls.from_plane(plane)
        if rig is None or any(plane.get(k) is None for k in cls._BEARING_PROPS):
            return False
        stamped = [float(plane.get(k)) for k in cls._BEARING_PROPS]
        norm = math.sqrt(sum(v * v for v in stamped))
        if norm < 1e-6:
            return True  # never rasterized against a source
        current = rig._current_bearing()
        dot = sum(a * b for a, b in zip(current, stamped)) / norm
        dot = max(-1.0, min(1.0, dot))
        return math.degrees(math.acos(dot)) > cls._STALE_BEARING_DEG

    @classmethod
    def refresh_silhouette(cls, planes=None, size=None, refit=None):
        """Re-rasterize shadow planes' silhouettes from their source's CURRENT position,
        overwriting each plane's PNG (and image datablock) in place — the Recalculate action,
        mirror of mayatk's. Works off the stamps, so it needs no Python instance.

        Args:
            planes: Shadow plane object(s)/name(s); None refreshes every stamped plane.
            size: Texture resolution; None keeps each plane's current size.
            refit: Fit the canvas to the new projection and restamp the plane (its drivers
                re-place it). None = yes for a live rig, no for a baked one — its keys already
                place the plane, so the PNG is drawn into the canvas those keys describe.

        Returns:
            The list of planes whose silhouette was rewritten.
        """
        refreshed = []
        for plane in cls.find_shadow_planes(planes):
            rig = cls.from_plane(plane)
            if rig is None:
                cls.logger.warning(
                    f"{plane.name}: built before the target/source stamps; re-create "
                    "the rig to recalculate its silhouette."
                )
                continue
            # The plane's OWN tile, never the atlas its image node names while
            # packed: the raster overwrites this file and the tile is then
            # copied into the atlas in place.
            path = cls._plane_texture_path(plane)
            res = size or cls._texture_size(path)
            if not res and rig.image is not None and not cls.plane_is_atlased(plane):
                res = int(rig.image.size[0]) or None
            recursive = bool(plane.get(cls._RECURSIVE_PROP, True))
            fit = cls.plane_is_live(plane) if refit is None else bool(refit)
            rig.create_silhouette_texture(
                size=res or 512, recursive=recursive, path=path, refit=fit
            )
            if cls.plane_is_atlased(plane):
                # The tile is rewritten in place; the plane keeps sampling the
                # atlas, so nothing else repacks.
                cls._write_atlas_tile(plane, "projected")
            if rig.rig_type == "horizon":
                # The map depends on the geometry, not the source: re-bake only
                # when the target changed since the last bake.
                params = cls._horizon_params(plane)
                rig.bake_horizon(
                    bins=params.get("bins"),
                    size=params.get("tile"),
                    only_if_changed=True,
                )
                if cls._packed_in(plane, "horizon"):
                    cls._write_atlas_tile(plane, "horizon")
            refreshed.append(plane)
        if refreshed:
            cls.refresh_export_metadata()
        return refreshed

    @classmethod
    def refresh_export_metadata(cls):
        """Republish the ``shadow_metadata`` channel on the ``data_export`` carrier
        from the file's shadow planes (mirror of mayatk's producer). Published at
        authoring time — create/bake/delete — and re-run at export time by the
        Scene Exporter via ``FbxUtils._KNOWN_PRODUCERS`` (non-exporter export
        paths ship the authoring-time state). Payload joins Unity-side by
        GameObject name (unitytk's ``ShadowPlaneController.cs``):

        ``{"version": 1, "planes": [{"name", "texture", "intensity"}]}``

        Clears the channel when the file has no shadow planes. Warns about planes whose
        silhouette was rasterized from a bearing the source has since left (Recalculate
        fixes it; nothing is rewritten here).

        Returns:
            The published JSON string, or None when cleared.
        """
        from blendertk.node_utils.data_nodes import DataNodes

        planes = cls.find_shadow_planes()
        if not planes:
            DataNodes.set_export_string(cls.SHADOW_METADATA, "")
            return None
        records = []
        stale = []
        for p in planes:
            records.append(cls.export_record(p))
            if cls.silhouette_is_stale(p):
                stale.append(p.name)
        if stale:
            cls.logger.warning(
                "Shadow silhouette rasterized from a bearing the source has since "
                f"left: {', '.join(stale)}. Press Recalculate Silhouette (or "
                "ShadowRig.refresh_silhouette) before exporting."
            )
        payload = json.dumps(
            {
                "version": cls.METADATA_VERSION,
                "unit_scale": cls.unit_scale(),
                "planes": records,
            }
        )
        DataNodes.set_export_string(cls.SHADOW_METADATA, payload)
        return payload

    @staticmethod
    def unit_scale():
        """Metres per scene linear unit (the record's ``unit_scale``): an
        engine that imported in metres multiplies the record's lengths.
        Blender's ``unit_settings.scale_length`` IS that number (1.0 in a
        default file, where one Blender unit is one metre)."""
        import bpy

        scene = bpy.context.scene
        scale = getattr(getattr(scene, "unit_settings", None), "scale_length", 1.0)
        return float(scale or 1.0)

    @classmethod
    def _plane_prop(cls, plane, name, default=None):
        """A stamped custom property's value, or *default* when unstamped."""
        try:
            value = plane.get(name)
        except ReferenceError:
            return default
        return default if value is None else value

    @classmethod
    def export_record(cls, plane):
        """One plane's ``shadow_metadata`` v2 record (the engine contract in
        ``mayatk/docs/shadow_rig_morphing.md``): the join key, the type, the
        textures, the source and contact objects the engine reads at runtime,
        the projection model's inputs, and the atlas / horizon blocks when the
        rig carries them. Works off the stamps, so it needs no Python instance
        and survives a rig built in an earlier session."""
        prop = cls._plane_prop
        tex = cls._plane_texture_path(plane)
        _, source = cls._rig_links(plane)
        contact = cls._plane_contact(plane)
        directional = source is not None and cls.source_is_directional(source)
        size = float(prop(plane, "sourceSize", 0.0) or 0.0)
        record = {
            "name": plane.name,
            "type": cls.plane_type(plane),
            "texture": os.path.basename(tex) if tex else "",
            "intensity": round(float(prop(plane, "shadowIntensity", 1.0)), 4),
            "source": source.name if source is not None else "",
            "source_type": "directional" if directional else "point",
            "source_size": 0.0 if directional else round(size, 6),
            "source_angle": round(size, 6) if directional else 0.0,
            "follow_source": bool(prop(plane, cls.FOLLOW_ATTR, True)),
            "contact": contact.name if contact is not None else "",
            "ground": round(float(prop(plane, "groundHeight", 0.0)), 6),
            "radius": round(float(prop(plane, "footprintRadius", 0.0)), 6),
            "height": round(float(prop(plane, "objectHeight", 0.0)), 6),
            "max_stretch": round(
                float(
                    prop(plane, "maxStretch", ptk.ShadowProjection.DEFAULT_MAX_STRETCH)
                ),
                6,
            ),
            "canvas": [
                round(float(prop(plane, p, d)), 6)
                for p, d in zip(cls._CANVAS_PROPS, (-1.0, 1.0, -0.5, 0.5))
            ],
        }
        atlas = prop(plane, cls._ATLAS_TEX_PROP, "")
        if atlas:
            record["atlas"] = {
                "texture": atlas,
                "rect": cls._read_rect(plane, cls._ATLAS_RECT_PROPS),
            }
        horizon = cls._horizon_params(plane)
        if horizon:
            record["horizon"] = horizon
        return record

    # ------------------------------------------------------------------ rig type
    @classmethod
    def plane_type(cls, plane):
        """The rig type stamped on *plane* (``projected`` for a rig built
        before the stamp existed)."""
        value = cls._plane_prop(plane, cls._TYPE_PROP, "") or cls.RIG_TYPES[0]
        return value if value in cls.RIG_TYPES else cls.RIG_TYPES[0]

    @classmethod
    def _read_rect(cls, plane, props):
        """A stamped ``(scaleX, scaleY, offsetX, offsetY)`` rect, identity when
        unstamped."""
        return [
            round(float(cls._plane_prop(plane, p, d)), 6)
            for p, d in zip(props, (1.0, 1.0, 0.0, 0.0))
        ]

    @staticmethod
    def _stamp_rect(plane, props, rect):
        for name, value in zip(props, rect):
            plane[name] = float(value)

    @staticmethod
    def _stamp_pixel_rect(plane, props, rect):
        for name, value in zip(props, rect):
            plane[name] = int(value)

    # ------------------------------------------------------------- horizon map
    def _recursive_flag(self):
        if self.shadow_plane is None:
            return True
        return bool(self._plane_prop(self.shadow_plane, self._RECURSIVE_PROP, True))

    def _contact_frame(self):
        """The contact empty's world matrix — the horizon map's frame: origin
        at the contact, axes the target's own."""
        import numpy as np

        node = self.contact if self.contact is not None else self.targets[0]
        return np.array(node.matrix_world, dtype=float)

    def horizon_output_path(self):
        """``<base>_horizon.png`` beside the silhouette."""
        folder = (
            os.path.dirname(self.texture_path)
            if self.texture_path
            else self._output_dir()
        )
        return os.path.join(folder, f"{self._base}_horizon.png").replace("\\", "/")

    @staticmethod
    def _geometry_hash(meshes, scale):
        """A digest of the meshes' points and triangles (millimetre-rounded)
        and the encoding scale, so Recalculate re-bakes the map when the
        target changed — or when ``maxStretch`` was retuned, which the map's
        cotangents are encoded against."""
        import hashlib

        import numpy as np

        digest = hashlib.sha1()
        digest.update(repr(round(float(scale), 6)).encode())
        for pts, tris in meshes:
            digest.update(np.round(np.asarray(pts, dtype=float), 3).tobytes())
            digest.update(np.asarray(tris, dtype=np.int64).tobytes())
        return digest.hexdigest()[:16]

    def bake_horizon(self, bins=None, size=None, path=None, *, only_if_changed=False):
        """Bake the targets' coverage-aware horizon map
        (``pythontk.ShadowHorizon``) in the contact empty's frame and write it
        beside the silhouette as ``<base>_horizon.png``; stamps the record's
        ``horizon`` block and turns the rig into the ``horizon`` type. The
        engine samples the map per frame from the source object, so the
        outline follows a runtime light; the silhouette stays as the fallback
        and the DCC preview.

        Parameters:
            bins, size: Azimuth bins and ``(W, H)`` tile texels;
                ``ShadowHorizon``'s measured defaults when None.
            path: Write here instead of beside the silhouette.
            only_if_changed: Skip the bake when the targets' geometry hash
                matches the stamped one (Recalculate).

        Returns:
            The PNG path.
        """
        import numpy as np

        meshes = self._gather_world_meshes(self._recursive_flag())
        if not meshes:
            raise ValueError("No mesh geometry found on the target(s).")
        digest = self._geometry_hash(meshes, self._max_stretch())
        plane = self.shadow_plane
        current = self._plane_prop(plane, self._HORIZON_HASH_PROP, "")
        if (
            only_if_changed
            and current == digest
            and self.horizon_path
            and os.path.exists(self.horizon_path)
        ):
            return self.horizon_path
        # Blender's matrix_world is the column-vector convention (M @ v), so
        # the row-stacked points multiply by the inverse's TRANSPOSE (Maya's
        # row-vector matrices multiply by the inverse itself).
        inverse = np.linalg.inv(self._contact_frame())
        local = []
        for pts, tris in meshes:
            hom = np.hstack([pts, np.ones((len(pts), 1))])
            local.append(((hom @ inverse.T)[:, :3], tris))
        contact = self._contact_point()
        ground_pt = (
            np.array([contact[0], contact[1], self.ground_height, 1.0]) @ inverse.T
        )
        if not self.object_height or not self.footprint_radius:
            self._measure_targets()
        bins = int(bins or ptk.ShadowHorizon.DEFAULT_BINS)
        size = tuple(int(v) for v in (size or ptk.ShadowHorizon.DEFAULT_SIZE))
        hmap = ptk.ShadowHorizon.bake(
            local,
            ground=float(ground_pt[2]),
            up=2,  # Blender is Z-up (Maya passes up=1)
            radius=self.footprint_radius,
            height=self.object_height,
            bins=bins,
            size=size,
            max_stretch=self._max_stretch(),
        )
        self.horizon_path = (str(path) if path else self.horizon_output_path()).replace(
            "\\", "/"
        )
        os.makedirs(os.path.dirname(self.horizon_path) or ".", exist_ok=True)
        # flip: the contract pins the PNG's TOP row to the r_min ring.
        self._save_image(
            f"{self._base}_horizon", hmap.to_rgba(), self.horizon_path, flip=True
        )
        self.rig_type = "horizon"
        plane[self._TYPE_PROP] = "horizon"
        plane[self._HORIZON_TEX_PROP] = os.path.basename(self.horizon_path)
        cols, rows = hmap.layout
        for name, value in zip(
            self._HORIZON_INT_PROPS, (hmap.bins, hmap.size[0], hmap.size[1], cols, rows)
        ):
            plane[name] = int(value)
        for name, value in zip(
            self._HORIZON_FLOAT_PROPS,
            (hmap.r_min, hmap.r_max, hmap.max_stretch),
        ):
            plane[name] = float(value)
        if plane.get(self._HORIZON_RECT_PROPS[0]) is None:
            self._stamp_rect(plane, self._HORIZON_RECT_PROPS, (1.0, 1.0, 0.0, 0.0))
        plane[self._HORIZON_HASH_PROP] = digest
        self.logger.info(
            f"Baked horizon map: {self.horizon_path} ({hmap.bins} bins, "
            f"{hmap.size[0]}x{hmap.size[1]} tiles)"
        )
        return self.horizon_path

    @classmethod
    def _plane_horizon_path(cls, plane):
        """The plane's own horizon PNG (beside its silhouette), or None."""
        name = cls._plane_prop(plane, cls._HORIZON_TEX_PROP, "")
        if not name:
            return None
        tex = cls._plane_texture_path(plane)
        folder = os.path.dirname(tex) if tex else cls._output_dir()
        return os.path.join(folder, name).replace("\\", "/")

    @classmethod
    def _horizon_params(cls, plane):
        """The record's ``horizon`` block from the stamps; ``{}`` for a rig
        without a map."""
        if cls.plane_type(plane) != "horizon":
            return {}
        if not cls._plane_prop(plane, cls._HORIZON_TEX_PROP, ""):
            return {}
        ints = [int(cls._plane_prop(plane, p, 0) or 0) for p in cls._HORIZON_INT_PROPS]
        atlas = cls._plane_prop(plane, cls._HORIZON_ATLAS_PROP, "")
        return {
            "texture": atlas or cls._plane_prop(plane, cls._HORIZON_TEX_PROP, ""),
            "bins": ints[0],
            "layers": ptk.ShadowHorizon.LAYERS,
            "tile": [ints[1], ints[2]],
            "layout": [ints[3], ints[4]],
            "mapping": ptk.ShadowHorizon.MAPPING,
            "r_min": round(float(cls._plane_prop(plane, "horizonRmin", 0.0)), 6),
            "r_max": round(float(cls._plane_prop(plane, "horizonRmax", 0.0)), 6),
            "max_stretch": round(
                float(
                    cls._plane_prop(
                        plane,
                        "horizonMaxStretch",
                        ptk.ShadowProjection.DEFAULT_MAX_STRETCH,
                    )
                ),
                6,
            ),
            "frame_a": list(cls.HORIZON_FRAME[0]),
            "frame_b": list(cls.HORIZON_FRAME[1]),
            "encoding": ptk.ShadowHorizon.ENCODING,
            "rect": cls._read_rect(plane, cls._HORIZON_RECT_PROPS),
        }

    # ------------------------------------------------------------------- atlas
    @classmethod
    def plane_is_atlased(cls, plane):
        """True while the plane samples the shared silhouette atlas."""
        return bool(cls._plane_prop(plane, cls._ATLAS_TEX_PROP, ""))

    @classmethod
    def _atlased_planes(cls):
        """Every plane still sampling an atlas (silhouette or horizon)."""
        return [
            p
            for p in cls.find_shadow_planes()
            if any(cls._packed_in(p, kind) for kind in cls.RIG_TYPES)
        ]

    @classmethod
    def _packed_in(cls, plane, kind):
        """Is *plane* currently sampling the *kind* atlas?"""
        prop = cls._ATLAS_TEX_PROP if kind == "projected" else cls._HORIZON_ATLAS_PROP
        return bool(cls._plane_prop(plane, prop, ""))

    @classmethod
    def _clear_atlas_stamps(cls, plane, kind):
        """Take *plane* out of the *kind* atlas: identity rect, and for the
        silhouette its own UVs and image node back."""
        if kind == "projected":
            own = cls._plane_own_image(plane)
            cls._set_plane_uvs(plane, (1.0, 1.0, 0.0, 0.0))
            plane[cls._ATLAS_TEX_PROP] = ""
            cls._stamp_rect(plane, cls._ATLAS_RECT_PROPS, (1.0, 1.0, 0.0, 0.0))
            cls._rebind_image(plane, own)
        else:
            plane[cls._HORIZON_ATLAS_PROP] = ""
            cls._stamp_rect(plane, cls._HORIZON_RECT_PROPS, (1.0, 1.0, 0.0, 0.0))

    @classmethod
    def _repack_atlased(cls):
        """Rewrite the atlases from the planes that are still packed — after a
        rig is deleted or unpacked. Removes an atlas nothing samples."""
        import bpy

        atlased = cls._atlased_planes()
        if atlased:
            return cls.pack_atlas(atlased)
        for kind in cls.RIG_TYPES:
            path = cls._atlas_path(kind)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            img = bpy.data.images.get(os.path.splitext(cls.ATLAS_BASENAMES[kind])[0])
            if img is not None and img.users == 0:
                bpy.data.images.remove(img)
        return {}

    @classmethod
    def _atlas_path(cls, kind, folder=None):
        return os.path.join(folder or cls._output_dir(), cls.ATLAS_BASENAMES[kind])

    @classmethod
    def _set_plane_uvs(cls, plane, rect):
        """Remap the quad's unit UVs into *rect* (undoing the rect they were
        last remapped into), so a fallback viewer samples the tile with no
        transform at all — the mirror of mayatk's ``polyEditUV`` pass."""
        layers = getattr(plane.data, "uv_layers", None)
        uv = (layers.get("UVMap") or layers.active) if layers else None
        if uv is None:
            return
        prev = (
            cls._read_rect(plane, cls._ATLAS_RECT_PROPS)
            if cls.plane_is_atlased(plane)
            else [1.0, 1.0, 0.0, 0.0]
        )
        sx, sy, ox, oy = (float(v) for v in rect)
        for loop in uv.data:
            u, v = float(loop.uv[0]), float(loop.uv[1])
            unit_u = round((u - prev[2]) / prev[0]) if prev[0] else 0
            unit_v = round((v - prev[3]) / prev[1]) if prev[1] else 0
            loop.uv = (ox + unit_u * sx, oy + unit_v * sy)

    @classmethod
    def _rebind_image(cls, plane, image):
        """Point the plane material's ``shadow_tex`` node at *image* (the
        atlas while packed, the plane's own tile once unpacked)."""
        if image is None:
            return
        for mat in (m for m in plane.data.materials if m and m.node_tree):
            node = mat.node_tree.nodes.get("shadow_tex")
            if node is not None:
                node.image = image

    @classmethod
    def pack_atlas(cls, planes=None, *, gutter=None):
        """Pack the file's shadow tiles into one atlas per kind — every plane's
        silhouette into ``shadow_atlas_projected.png`` and every horizon rig's
        map block into ``shadow_atlas_horizon.png`` — beside the tiles.

        A packed plane keeps its own PNG (Recalculate rewrites the tile in
        place through :meth:`_write_atlas_tile`), has its quad UVs remapped
        into its inset rect and its image node pointed at the atlas, and
        carries the rect in its record (``atlas`` / ``horizon.rect``) so the
        engines batch and instance planes that share a type. Any plane already
        packed is repacked with *planes* — the atlas is one file, and a partial
        repack would move rects out from under the others.

        Parameters:
            planes: The planes to pack (all when None).
            gutter: Texels inset on every side of a published rect
                (``ShadowAtlas.GUTTER`` when None).

        Returns:
            ``{kind: atlas path}`` for the kinds that packed anything.
        """
        gutter = ptk.ShadowAtlas.GUTTER if gutter is None else int(gutter)
        # None = every plane; an explicit empty sequence = none of them (the
        # repack paths pass exactly the planes that must stay packed).
        pool = list(cls.find_shadow_planes(planes) if planes or planes is None else [])
        pool += cls._atlased_planes()
        # bpy objects have no ordering — dedup and order by name (mayatk sorts
        # its node strings), so the same set packs the same way every time.
        by_name = {p.name: p for p in pool}
        planes = [by_name[n] for n in sorted(by_name)]
        out = {}
        for kind in cls.RIG_TYPES:
            members, orphans = [], []
            for plane in planes:
                if kind == "projected":
                    tex = cls._plane_texture_path(plane)
                elif cls.plane_type(plane) == "horizon":
                    tex = cls._plane_horizon_path(plane)
                else:
                    tex = None
                if tex and os.path.exists(tex):
                    members.append((plane, tex))
                elif cls._packed_in(plane, kind):
                    # Its tile is gone (deleted, or the rig came from another
                    # project): leaving the stamps would aim its UVs at a rect
                    # the repack hands to a different plane — a shadow wearing
                    # someone else's shape. Drop it out of the atlas instead.
                    orphans.append(plane)
            for plane in orphans:
                cls._clear_atlas_stamps(plane, kind)
                cls.logger.warning(
                    f"{plane.name}: its "
                    f"{'silhouette' if kind == 'projected' else 'horizon map'} PNG is "
                    "missing, so it was dropped from the atlas — Recalculate "
                    "Silhouette rewrites it, then Pack Atlas re-joins it."
                )
            atlas_path = cls._atlas_path(
                kind, os.path.dirname(members[0][1]) if members else None
            )
            if not members:
                if os.path.exists(atlas_path):
                    try:
                        os.remove(atlas_path)
                    except OSError:
                        pass
                continue
            tiles = {}
            for plane, tex in members:
                pixels = cls._read_png(tex)
                if pixels is not None:
                    tiles[plane.name] = pixels
                elif cls._packed_in(plane, kind):
                    # Present but unreadable — the same hazard as a missing
                    # tile, so it leaves the atlas the same way.
                    cls._clear_atlas_stamps(plane, kind)
                    cls.logger.warning(
                        f"{plane.name}: its "
                        f"{'silhouette' if kind == 'projected' else 'horizon map'} PNG "
                        "could not be read, so it was dropped from the atlas — "
                        "Recalculate Silhouette rewrites it, then Pack Atlas "
                        "re-joins it."
                    )
            if not tiles:
                continue
            members = [(p, t) for p, t in members if p.name in tiles]
            atlas, rects, pixel_rects = ptk.ShadowAtlas.pack(tiles, gutter=gutter)
            image = cls._save_image(
                os.path.splitext(cls.ATLAS_BASENAMES[kind])[0],
                atlas,
                atlas_path,
                flip=True,
            )
            base = os.path.basename(atlas_path)
            for plane, tex in members:
                name = plane.name
                if kind == "projected":
                    cls._set_plane_uvs(plane, rects[name])
                    plane[cls._SILHOUETTE_PROP] = os.path.basename(tex)
                    plane[cls._ATLAS_TEX_PROP] = base
                    cls._stamp_rect(plane, cls._ATLAS_RECT_PROPS, rects[name])
                    cls._stamp_pixel_rect(
                        plane, cls._ATLAS_PIXEL_PROPS, pixel_rects[name]
                    )
                    cls._rebind_image(plane, image)
                else:
                    plane[cls._HORIZON_ATLAS_PROP] = base
                    cls._stamp_rect(plane, cls._HORIZON_RECT_PROPS, rects[name])
                    cls._stamp_pixel_rect(
                        plane, cls._HORIZON_PIXEL_PROPS, pixel_rects[name]
                    )
            out[kind] = atlas_path
        if planes:
            cls.refresh_export_metadata()
        return out

    @classmethod
    def unpack_atlas(cls, planes=None):
        """Undo :meth:`pack_atlas` for *planes* (all when None): unit UVs, the
        image node back on the plane's own PNG, the rect stamps cleared.
        Returns the planes that were unpacked."""
        done = []
        for plane in cls.find_shadow_planes(planes):
            touched = False
            for kind in cls.RIG_TYPES:
                if cls._packed_in(plane, kind):
                    cls._clear_atlas_stamps(plane, kind)
                    touched = True
            if touched:
                done.append(plane)
        if done:
            # The survivors' atlas is rewritten without the leavers.
            cls._repack_atlased()
            cls.refresh_export_metadata()
        return done

    @classmethod
    def _write_atlas_tile(cls, plane, kind):
        """Rewrite one packed tile in place from the plane's own PNG and return
        the atlas path (no repack: the rect is the stamped one)."""
        if kind == "projected":
            tex, props, atlas_name = (
                cls._plane_texture_path(plane),
                cls._ATLAS_PIXEL_PROPS,
                cls._plane_prop(plane, cls._ATLAS_TEX_PROP, ""),
            )
        else:
            tex, props, atlas_name = (
                cls._plane_horizon_path(plane),
                cls._HORIZON_PIXEL_PROPS,
                cls._plane_prop(plane, cls._HORIZON_ATLAS_PROP, ""),
            )
        if not (tex and atlas_name and os.path.exists(tex)):
            return None
        atlas_path = os.path.join(os.path.dirname(tex), atlas_name)
        if not os.path.exists(atlas_path):
            return None
        rect = tuple(int(cls._plane_prop(plane, p, 0) or 0) for p in props)
        atlas = cls._read_png(atlas_path)
        tile = cls._read_png(tex)
        if atlas is None or tile is None:
            return None
        ptk.ShadowAtlas.write_tile(atlas, rect, tile)
        image = cls._save_image(
            os.path.splitext(cls.ATLAS_BASENAMES[kind])[0],
            atlas,
            atlas_path,
            flip=True,
        )
        if kind == "projected":
            # The rewritten datablock reaches the viewport through the node;
            # re-point it in case the file was reopened with a differently
            # named atlas datablock (mayatk re-sets the file node's path here).
            cls._rebind_image(plane, image)
        return atlas_path

    # ------------------------------------------------------------------ orchestration
    @classmethod
    def create(
        cls,
        targets,
        light_pos=(5.0, 5.0, 10.0),
        texture_res=512,
        axis="auto",
        source_name=DEFAULT_SOURCE_NAME,
        recursive=True,
        mode="orbit",
        ground_height=0.0,
        rig_type="projected",
        horizon_bins=None,
        horizon_size=None,
    ):
        """Build a projected-shadow rig for ``targets`` (mirror of mayatk's ``ShadowRig.create``).

        ``source_name`` is any existing object's name (a light included; a ``SUN`` projects
        along its direction) or the name of an Empty to create at ``light_pos``; one plane is
        built per source, so call :meth:`create_for_sources` for several. ``axis`` is retired
        (the silhouette is always the projection through the source). ``mode="stretch"`` is
        retired and builds as orbit with a warning.

        ``rig_type`` is ``"projected"`` (default) or ``"horizon"`` — the latter also bakes the
        targets' horizon map (:meth:`bake_horizon`) so the engine can follow a runtime light;
        the silhouette stays as the fallback and the DCC preview. ``horizon_bins`` /
        ``horizon_size`` are that map's azimuth bins and ``(W, H)`` tile size
        (``pythontk.ShadowHorizon``'s measured defaults when None).

        Note: a failed build rolls itself back — every datablock created up to
        the failure (including a source empty this call created) and any
        half-written texture are removed before the exception re-raises
        (mirror of mayatk's node-diff rollback).
        """
        import bpy

        rig = cls(
            targets=targets,
            ground_height=ground_height,
            mode=mode,
            source_name=source_name,
        )
        if not rig.targets:
            raise ValueError("Shadow Rig needs at least one target object.")
        pre = {
            coll: {d.name for d in getattr(bpy.data, coll)}
            for coll in ("objects", "meshes", "materials", "images")
        }
        try:
            rig.get_or_create_shadow_source(position=light_pos, source_name=source_name)
            rig.create_contact_locator()
            rig.create_shadow_plane()
            rig.create_silhouette_texture(
                size=texture_res, axis=axis, recursive=recursive
            )
            rig.create_material()
            # The group BEFORE the drivers: it carries the model's level-1
            # intermediates the plane's channels read.
            rig.group = RigUtils.create_group(
                f"{rig._base}_shadow_grp", children=[rig.shadow_plane]
            )
            rig._stamp_rig_links()
            rig.setup_drivers()
            if rig_type not in cls.RIG_TYPES:
                raise ValueError(
                    f"rig_type {rig_type!r} is not one of {cls.RIG_TYPES}."
                )
            if rig_type == "horizon":
                rig.bake_horizon(bins=horizon_bins, size=horizon_size)
        except Exception:
            # Roll back the partial build — a failed create() must not leave
            # orphan datablocks (or a half-written texture) behind. Objects
            # first, so dependent datablocks drop to zero users; a datablock
            # a reused-name path merely mutated is pre-existing and stays.
            for coll in ("objects", "meshes", "materials", "images"):
                data = getattr(bpy.data, coll)
                for d in [d for d in data if d.name not in pre[coll]]:
                    try:
                        data.remove(d)
                    except (ReferenceError, RuntimeError):
                        pass  # already cascaded away
            for stale in (rig.texture_path, rig.horizon_path):
                if stale and os.path.exists(stale):
                    try:
                        os.remove(stale)
                    except OSError:
                        pass
            raise

        # Publish the engine hand-off record onto the data_export carrier
        # (authoring-time publish; the Scene Exporter re-refreshes at export).
        cls.refresh_export_metadata()
        rig.logger.success(
            f"Shadow rig '{rig._base}' ({rig.mode}) — plane {rig.shadow_plane.name}, "
            f"source {rig.light.name}, texture {rig.texture_path}"
        )
        return rig

    @classmethod
    def create_for_sources(cls, targets, sources, **kwargs):
        """One shadow rig per source — N lights cast N shadows (mirror of mayatk's).

        Each source gets its own plane, drivers, and silhouette PNG, named
        ``<target>_<source>_shadow`` for any source but the default ``shadow_source``
        (which keeps the plain ``<target>_shadow``). ``kwargs`` pass through to :meth:`create`.
        """
        names = [str(s) for s in ptk.make_iterable(sources) if str(s).strip()]
        if not names:
            names = [cls.DEFAULT_SOURCE_NAME]
        kwargs.pop("source_name", None)
        return [cls.create(targets, source_name=name, **kwargs) for name in names]

    @classmethod
    def create_horizon_for_sources(cls, targets, sources, **kwargs):
        """:meth:`create_for_sources` for the ``horizon`` rig type."""
        kwargs["rig_type"] = "horizon"
        return cls.create_for_sources(targets, sources, **kwargs)

    @classmethod
    def create_per_object(cls, targets, sources, **kwargs):
        """One rig per target per source — the panel's *Per object* planes.

        Each object gets its own contact, quad, tile and record (a table with
        props on it is a *Combined* rig instead); ``kwargs`` pass through to
        :meth:`create`. Returns the rigs, targets-major.
        """
        rigs = []
        for target in ptk.make_iterable(targets):
            rigs.extend(cls.create_for_sources([target], sources, **kwargs))
        return rigs


class ShadowRigSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Shadow Rig panel.

    Self-contained (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` helper is deferred into
    ``header_init``. Mirrors the Maya panel: a live :class:`blendertk.Preview` rebuilds the rig as
    options change; **Create Shadow** commits; the **Utility** section acts on whatever rig the
    selection touches (Recalculate, with Apply Source / Rebuild in its option box; Bake,
    with Restore in its; Delete).
    """

    #: Rig types the panel offers, keyed by the ``Rig:`` combo's label ->
    #: the engine builder ``(targets, source_names, **options) -> [rigs]``.
    #: The strategy seam a new rig type lands in: one row here, one combo
    #: item, no branching in :meth:`perform_operation`.
    RIG_BUILDERS = {
        "Projected": ShadowRig.create_for_sources,
        "Horizon": ShadowRig.create_horizon_for_sources,
    }
    #: A combo item carrying this suffix is a rig type only planned for —
    #: listed so the panel shows the direction, disabled until it lands.
    PLANNED_SUFFIX = "(planned)"
    #: ``Atlas:`` combo → whether to pack the built rigs' tiles: Auto packs
    #: once two rigs of a kind exist in the file.
    ATLAS_MODES = ("Auto", "Off", "On")

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shadow_rig
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[shadow_rig] ")

        from blendertk.core_utils.preview import Preview

        # Preview's object-level rollback can't restore a mutated custom prop
        # on a PRE-EXISTING data_export carrier, nor un-write the silhouette
        # PNGs — restore_func repairs both on cancel (the Maya slots' analogue
        # is contract.record_modification + contract.add_file).
        self._preview_textures = []
        # The rigs the last pass built, packed by the COMMIT hook only — see
        # :meth:`_pack_after_commit`.
        self._built_rigs = []
        self.preview = Preview(
            self,
            self.ui.chk_preview,
            self.ui.b000,
            finalize_func=self._pack_after_commit,
            restore_func=self._restore_after_preview,
            message_func=self.sb.message_box,
        )
        # Any option change re-bakes the previewed rig (mirror of the Maya panel).
        self.ui.cmb_type.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb_planes.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb_atlas.currentIndexChanged.connect(self.preview.refresh)
        self.ui.chk_combine.toggled.connect(self.preview.refresh)
        self.ui.s000.currentIndexChanged.connect(self.preview.refresh)
        # A renamed source must exist BEFORE the refresh rebuilds against it,
        # and outside the preview's datablock snapshot (see prepare_operation).
        self._built_sources = None  # the names the live preview was built from
        self.ui.txt_source.editingFinished.connect(self._on_sources_edited)
        # b000-b004 and b009 are auto-wired by the switchboard (method name
        # == objectName); a raw connect here on one of those stacked a second
        # connection → double-fire. The deeper Utility actions (Apply Source,
        # Rebuild Rig, Restore Expression) hang off b003's / b002's option
        # boxes — see b003_init / b002_init.

        self._init_tooltips()

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Shadow Rig",
                body="Create a projected-shadow plane rig that exports cleanly "
                "for game engines (Unity, WebXR). The plane carries the "
                "target's shadow as a PNG — its geometry projected onto the "
                "ground through the source, the way a real shadow forms: an "
                "overhead source draws the footprint, a low one the long "
                "stretched shape, an area light a penumbra that softens away "
                "from the contact. Drivers keep the plane's direction, "
                "reach and fade tracking the source and the target live.",
                steps=[
                    "Select one or more target meshes.",
                    "Pick the <b>Rig</b> type — <b>Projected</b> (one silhouette "
                    "the model re-places) or <b>Horizon</b> (also bakes a "
                    "horizon map the engine samples per frame, so the outline "
                    "follows a runtime light).",
                    "Pick <b>Planes</b> — <b>Combined</b> builds one plane for "
                    "the whole selection, <b>Per object</b> one per object — and "
                    "<b>Atlas</b>: <b>Auto</b> packs the planes' tiles into one "
                    "texture per kind once two rigs exist, so the engines batch "
                    "and instance them.",
                    "Enable <b>Preview</b> to build the rig live. The source "
                    "empty is created once and survives every refresh — "
                    "move it to place the light, or pick a real light with "
                    "<b>Source From Selection</b>.",
                    "Tweak <b>Resolution</b> and <b>Include Children</b>; the "
                    "preview refreshes on each change.",
                    "Press <b>Create Shadow</b> to commit, or disable Preview "
                    "to discard.",
                    "Moved the source afterwards? Press <b>Recalculate "
                    "Silhouette</b> to re-render the PNG from where it is now.",
                    "Export through the <b>Scene Exporter</b> — the rig's "
                    "<i>shadow_metadata</i> rides the data_export carrier. "
                    "Press <b>Bake to Keyframes</b> first for a plain FBX export.",
                ],
                sections=[
                    (
                        "Sources",
                        [
                            "<b>Source Name</b> — one or more object names, "
                            "comma-separated; one shadow plane is built per source.",
                            "<b>Source From Selection</b> — use the selected "
                            "object(s), lights included, as the source(s). A "
                            "Sun light projects along its direction. A mesh in "
                            "<b>Edit Mode</b> is a fixture: a real area light is "
                            "built per mesh, the "
                            "way the Lighting panel does, and becomes the source.",
                        ],
                    ),
                    (
                        "Utility",
                        [
                            "Every Utility button acts on the rig(s) the "
                            "selection touches — the plane, its group, a target, "
                            "the source, or the contact — also on rigs built in "
                            "an earlier session.",
                            "<b>Recalculate Silhouette</b> re-renders the PNG; "
                            "its option box holds the deeper updates: <b>Apply "
                            "Source</b> (re-point the rig at the Source Name "
                            "field) and <b>Rebuild Rig</b> (re-create it from the "
                            "target's current geometry with the panel's options). "
                            "<b>Bake to Keyframes</b>' option box holds <b>Restore "
                            "Expression</b>, its inverse; <b>Delete Rig</b> tears "
                            "the rig down.",
                        ],
                    ),
                    (
                        "Plane properties",
                        [
                            "<b>shadowIntensity</b> / <b>falloffPower</b> — overall "
                            "strength and how fast an elongated shadow lightens.",
                            "<b>maxStretch</b> — cap on the shadow's reach, in "
                            "object heights.",
                            "<b>fadeHeight</b> — rise off the ground at which the "
                            "shadow has fully faded.",
                            "<b>groundHeight</b> — world Z of the ground the "
                            "shadow lies on.",
                        ],
                    ),
                ],
                notes=[
                    "Unity plug-and-play: deploy unitytk's C# templates once "
                    "and export via the Scene Exporter — the import sets up "
                    "the unlit-transparent material and shadow flags "
                    "automatically. Other engines: assign an unlit/transparent "
                    "shader with the PNG by hand.",
                    "The fade is the plane's keyable <i>opacity</i> property: it "
                    "bakes with the transform and rides the FBX as an animated "
                    "custom property.",
                ],
            )
        )

    def _init_tooltips(self):
        """Set the polished (uitk ``fmt``) tooltips for every option and action."""
        ui = self.ui

        ui.cmb_type.setToolTip(
            self.sb.tooltip.fmt(
                title="Rig Type",
                body="Which shadow rig to build.",
                sections=[
                    (
                        "Types",
                        [
                            "<b>Projected</b> — one silhouette, the target's "
                            "projection through the source, re-placed live by the "
                            "projection model; <b>Recalculate Silhouette</b> "
                            "re-renders it when the source has moved.",
                            "<b>Horizon</b> — the projected rig plus a "
                            "coverage-aware horizon map (<i>&lt;name&gt;_horizon.png</i>) "
                            "baked in the target's own frame: the engine samples "
                            "it per frame from the source object, so the outline "
                            "follows a moving light — and a moved prop carries its "
                            "shadow — without a re-render. The silhouette stays as "
                            "the fallback and the viewport preview. Design: "
                            "<i>mayatk/docs/shadow_rig_morphing.md</i>.",
                        ],
                    )
                ],
            )
        )
        ui.cmb_planes.setToolTip(
            self.sb.tooltip.fmt(
                title="Planes",
                body="How many shadow planes the selection builds.",
                sections=[
                    (
                        "Modes",
                        [
                            "<b>Combined</b> — one plane for the whole selection "
                            "(a table with the props on it casts one shadow).",
                            "<b>Per object</b> — one plane per selected object, "
                            "each with its own contact, tile and record; the "
                            "planes share an atlas and the engines instance them.",
                        ],
                    )
                ],
                notes=["Either way, one plane is built per source."],
            )
        )
        ui.cmb_atlas.setToolTip(
            self.sb.tooltip.fmt(
                title="Atlas",
                body="Pack the planes' tiles into one texture per kind — the "
                "silhouettes into <i>shadow_atlas_projected.png</i>, the horizon "
                "maps into <i>shadow_atlas_horizon.png</i> — so the engines draw "
                "every plane of a kind with one material and instance them.",
                sections=[
                    (
                        "Modes",
                        [
                            "<b>Auto</b> — pack once the file holds two rigs.",
                            "<b>Off</b> — every plane keeps its own texture.",
                            "<b>On</b> — always pack.",
                        ],
                    )
                ],
                notes=[
                    "Each plane keeps its own PNG: Recalculate rewrites its "
                    "tile in place, and a fallback viewer samples the atlas "
                    "through the plane's own UVs with no transform at all.",
                    "<b>Pack Atlas</b> in the Utility section packs or "
                    "repacks every rig in the file.",
                ],
            )
        )
        ui.b010.setToolTip(
            self.sb.tooltip.fmt(
                title="Pack Atlas",
                body="Pack (or repack) every shadow rig's tiles into the "
                "file's atlases — see the <b>Atlas</b> option.",
                notes=[
                    "Acts on the whole file: the atlas is one image, so a "
                    "partial repack would move rects out from under the "
                    "other planes.",
                ],
            )
        )
        ui.chk_combine.setToolTip(
            self.sb.tooltip.fmt(
                title="Include Children",
                body="Include the selected objects' descendant meshes in the "
                "baked silhouette.",
                notes=[
                    "The selection always shares a single combined shadow plane.",
                    "Off — only the selected meshes themselves are rasterized.",
                ],
            )
        )
        ui.txt_source.setToolTip(
            self.sb.tooltip.fmt(
                title="Source Name",
                body="The shadow source(s) the projection is cast from — any "
                "object name (a light included), comma-separated for "
                "several. A missing name is created as an empty when the "
                "preview starts.",
                notes=[
                    "Reuse a name to share one source across rigs; one shadow "
                    "plane is built per source.",
                    "A Sun light projects along its direction; anything else "
                    "casts from where it sits.",
                    "Move the source in the viewport — the preview keeps it.",
                ],
            )
        )
        ui.s000.setToolTip(
            self.sb.tooltip.fmt(
                title="Texture Resolution",
                body="Pixel resolution of the baked silhouette PNG carried by "
                "the shadow plane.",
                notes=[
                    "Higher = crisper shadow edge, but a larger texture on disk.",
                ],
            )
        )
        ui.chk_horizon_preview.setToolTip(
            self.sb.tooltip.fmt(
                title="Live Horizon Preview",
                body="Shows a Horizon rig's shadow the way the engines will: "
                "the baked map evaluated in the viewport from the live source, "
                "so the outline morphs as you move the light.",
                notes=[
                    "Acts on the Horizon rig(s) the selection touches, or all "
                    "when nothing is selected.",
                    "Display only: nothing about the rig or its export changes, "
                    "and the preview is stood down before any FBX export.",
                    "Needs a hardware viewport (a windowed Blender; the GPU module has no backend headless).",
                ],
            )
        )
        ui.chk_preview.setToolTip(
            self.sb.tooltip.fmt(
                title="Preview",
                body="Builds the shadow rig live so you can judge it before "
                "committing.",
                notes=[
                    "Tweaking any option refreshes the preview; the source "
                    "keeps its position.",
                    "<b>Create Shadow</b> commits it; disabling Preview discards it.",
                ],
            )
        )
        ui.b000.setToolTip(
            self.sb.tooltip.fmt(
                title="Create Shadow",
                body="Commits the previewed shadow rig for the selected target(s), "
                "or builds one straight from the selection.",
                steps=[
                    "Select one or more target meshes.",
                    "Enable <b>Preview</b> and dial in the options.",
                    "Press <b>Create Shadow</b>.",
                ],
            )
        )
        ui.b001.setToolTip(
            self.sb.tooltip.fmt(
                title="Reset to Defaults",
                body="Restores every option on this panel to its default value.",
            )
        )
        ui.b002.setToolTip(
            self.sb.tooltip.fmt(
                title="Bake to Keyframes",
                body="Bakes the shadow plane's driven motion and fade to "
                "keyframes over the scene frame range and removes the live drivers — "
                "leaving an FBX-ready plane.",
                notes=[
                    "Applies to the rig(s) the selection touches, or all planes "
                    "if nothing is selected.",
                    "Its option box holds <b>Restore Expression</b>, which "
                    "reverses it.",
                ],
            )
        )
        ui.b003.setToolTip(
            self.sb.tooltip.fmt(
                title="Recalculate Silhouette",
                body="Re-renders the silhouette PNG from the source's current "
                "position and the target's current geometry, overwriting the "
                "plane's texture in place.",
                notes=[
                    "Applies to the rig(s) the selection touches, or all planes "
                    "if nothing is selected.",
                    "Works on a baked rig — the PNG is drawn into the canvas "
                    "its keys describe.",
                    "Its option box holds the deeper updates: <b>Apply "
                    "Source</b> and <b>Rebuild Rig</b>.",
                ],
            )
        )
        ui.b004.setToolTip(
            self.sb.tooltip.fmt(
                title="Source From Selection",
                body="Uses the selected object(s) — lights included — as the "
                "shadow source(s), writing their names into Source Name.",
                notes=[
                    "Several selected objects build one shadow plane each.",
                    "A mesh in <b>Edit Mode</b> is a fixture: a real area light "
                    "is built per mesh — the Lighting panel's <i>Lights From "
                    "Geometry</i> — and becomes the source; its size draws the "
                    "shadow's penumbra.",
                    "With the preview running, the previewed targets are "
                    "rebuilt against the new source(s) at once.",
                ],
            )
        )
        ui.b009.setToolTip(
            self.sb.tooltip.fmt(
                title="Delete Rig",
                body="Tears down the rig(s) the selection touches — plane, "
                "group, drivers, material and contact empty. The targets "
                "and the source are kept.",
            )
        )

    # -------------------------------------------------------- option boxes
    def cmb_type_init(self, widget):
        """A rig type the panel only plans for is listed but not selectable."""
        model = widget.model()
        for i in range(widget.count()):
            if widget.itemText(i).strip().endswith(self.PLANNED_SUFFIX):
                model.item(i).setEnabled(False)

    def b003_init(self, widget):
        """Recalculate Silhouette's option box: the deeper updates of an
        existing rig — Apply Source and Rebuild Rig."""
        self._add_option_actions(
            widget,
            "Update Rig",
            [
                (
                    "btn_apply_source",
                    "Apply Source",
                    self.apply_source,
                    self.sb.tooltip.fmt(
                        title="Apply Source",
                        body="Re-points the rig(s) the selection touches at the "
                        "first Source Name, re-rendering their silhouettes from "
                        "there.",
                        notes=[
                            "A baked rig has its drivers restored first.",
                            "Select the plane, its group, a target, the old "
                            "source, or the contact.",
                        ],
                    ),
                ),
                (
                    "btn_rebuild",
                    "Rebuild Rig",
                    self.rebuild_rig,
                    self.sb.tooltip.fmt(
                        title="Rebuild Rig",
                        body="Re-creates the rig(s) the selection touches from "
                        "the target's current geometry, keeping their targets, "
                        "source and ground, with this panel's Resolution and "
                        "Include Children.",
                        notes=[
                            "The plane keeps its name, so an engine-side join "
                            "survives.",
                        ],
                    ),
                ),
            ],
        )

    def b002_init(self, widget):
        """Bake to Keyframes' option box: Restore Expression, its inverse."""
        self._add_option_actions(
            widget,
            "Bake",
            [
                (
                    "btn_restore",
                    "Restore Expression",
                    self.restore_expression,
                    self.sb.tooltip.fmt(
                        title="Restore Expression",
                        body="Un-bakes the rig(s) the selection touches: drops the "
                        "baked keys and rebuilds the live drivers from the rig's "
                        "stamped source and targets.",
                    ),
                ),
            ],
        )

    @staticmethod
    def _add_option_actions(widget, title, actions):
        """Fill *widget*'s option box with push-button *actions* — the rarer,
        deeper operations behind a Utility button — from ``(objectName, text,
        handler, tooltip)`` rows. Idempotent — the menu exposes its items as
        attributes by objectName, so a built menu is detectable: the switchboard
        runs an ``_init`` once per widget, but a test may run it by hand."""
        menu = widget.option_box.menu
        if getattr(menu, actions[0][0], None) is not None:
            return
        menu.setTitle(title)
        for name, text, handler, tooltip in actions:
            button = menu.add(
                "QPushButton", setText=text, setObjectName=name, setToolTip=tooltip
            )
            button.clicked.connect(handler)

    # ------------------------------------------------------------- sources
    def _source_names(self):
        """The source names typed into the panel (comma-separated), or the default."""
        text = self.ui.txt_source.text() or ""
        names = [n.strip() for n in text.replace(";", ",").split(",")]
        names = list(dict.fromkeys(n for n in names if n))
        return names or [ShadowRig.DEFAULT_SOURCE_NAME]

    def _set_source_names(self, names):
        self.ui.txt_source.setText(", ".join(names))

    def _ensure_sources(self):
        """Every named source exists (missing ones become empties at the default position).
        Runs OUTSIDE the preview's datablock snapshot — see :meth:`prepare_operation`."""
        for name in self._source_names():
            ShadowRig.ensure_source(name)

    def _on_sources_edited(self):
        """Source Name edited: create any new name first (outside the snapshot), then
        refresh the preview. ``editingFinished`` also fires on focus loss, so an
        unchanged field rebuilds nothing."""
        if self.preview.is_enabled:
            if self._source_names() == self._built_sources:
                return
            self._ensure_sources()
        self.preview.refresh()

    def prepare_operation(self, objects):
        """Preview's one-shot precondition, run at enable outside the datablock snapshot:
        the source empties exist before the rig is built (built inside the pass, the source
        was purged and recreated at the default position on every refresh and the commit)."""
        self._ensure_sources()

    def _pack_after_commit(self):
        """Preview's commit hook: pack the tiles the just-committed rigs wrote.

        blendertk's Preview keeps the last rehearsal's result on commit (it
        does not re-run the operation), so this hook — which fires on both
        commit paths and never on a discard — is the mirror of the Maya
        panel's ``contract is None`` branch. See :meth:`_pack_if_wanted` for
        why packing must not happen inside a preview pass.
        """
        rigs, self._built_rigs = self._built_rigs, []
        # Committed textures are no longer the discard path's to remove.
        self._preview_textures = []
        alive = []
        for rig in rigs:
            try:
                if rig.shadow_plane is not None and rig.shadow_plane.name:
                    alive.append(rig)
            except ReferenceError:
                continue  # the pass that built it was rolled back
        self._pack_if_wanted(alive)

    def _restore_after_preview(self):
        """Repair what Preview's rollback can't after a canceled preview: a
        pre-existing ``data_export`` carrier keeps the mutated
        ``shadow_metadata`` prop (republish scans the restored file state),
        and the previewed silhouette PNGs are orphaned on disk (removed unless
        a surviving plane — e.g. a committed rig — still references them)."""
        ShadowRig.refresh_export_metadata()
        self._built_rigs = []
        textures, self._preview_textures = self._preview_textures, []
        # normcase: bpy.path.abspath and our own join can differ in slash
        # style/case on Windows — a miss here would delete a live texture.
        live = {
            os.path.normcase(os.path.normpath(t))
            for p in ShadowRig.find_shadow_planes()
            for t in (
                ShadowRig._plane_texture_path(p),
                ShadowRig._plane_horizon_path(p),
            )
            if t
        }
        for tex in textures:
            if tex and os.path.exists(tex):
                if os.path.normcase(os.path.normpath(tex)) not in live:
                    try:
                        os.remove(tex)
                    except OSError:
                        pass  # locked/read-only — scene state is already repaired

    def _rig_builder(self):
        """The engine builder for the ``Rig:`` combo's type.

        Raises:
            ValueError: the type is only planned for (its item is disabled, so
                this is a programmatic selection) — the Preview reports it and
                turns itself off.
        """
        label = self.ui.cmb_type.currentText().split(":", 1)[-1].strip()
        key = label.replace(self.PLANNED_SUFFIX, "").strip()
        builder = self.RIG_BUILDERS.get(key)
        if builder is None:
            raise ValueError(
                f"The {key} rig is not available yet — a planned rig type "
                "(see mayatk/docs/shadow_rig_morphing.md)."
            )
        return builder

    def _resolution(self):
        """The Resolution combo's value (``"Resolution: 512"`` -> 512)."""
        res_text = self.ui.s000.currentText()
        try:
            return int(res_text.replace("Resolution: ", "").strip())
        except (ValueError, AttributeError):
            return 512

    def _per_object(self):
        """True when the ``Planes:`` combo says one rig per selected object."""
        return "per object" in self.ui.cmb_planes.currentText().lower()

    def _atlas_mode(self):
        """The ``Atlas:`` combo's mode (``Auto`` / ``Off`` / ``On``)."""
        label = self.ui.cmb_atlas.currentText().split(":", 1)[-1].strip()
        return label if label in self.ATLAS_MODES else self.ATLAS_MODES[0]

    def _pack_if_wanted(self, rigs):
        """Pack the built rigs' tiles per the ``Atlas:`` combo: ``On`` always,
        ``Auto`` once the file holds two rigs of a kind (a lone plane gains
        nothing from an atlas), ``Off`` never. Returns the atlas paths.

        Called from the COMMIT hook only, never from a preview pass: the atlas
        is one shared file the rigs already in the scene sample, so packing
        during a rehearsal would rewrite theirs and discarding the preview
        would delete a file committed rigs depend on. The preview looks
        identical either way — a plane reaches its tile through its own UVs.
        """
        mode = self._atlas_mode()
        if mode == "Off" or not rigs:
            return {}
        if mode == "Auto" and len(ShadowRig.find_shadow_planes()) < 2:
            return {}
        return ShadowRig.pack_atlas([r.shadow_plane for r in rigs])

    def _shadow_targets(self, objects, recursive):
        """The selection's shadow casters: mesh-bearing objects that are not one of the
        named sources — a light or empty in the selection is the source, never a target.

        Raises:
            ValueError: nothing in the selection can cast a shadow (one line: the
                Preview shows an error's first line).
        """
        sources = {n for n in self._source_names()}
        targets, rejected = [], []
        for obj in objects or []:
            if obj is None or obj.name in sources or obj.type == "LIGHT":
                continue
            if ShadowRig.has_mesh_geometry(obj, recursive):
                if obj not in targets:
                    targets.append(obj)
            else:
                rejected.append(obj.name)
        if targets:
            return targets
        reasons = []
        if rejected:
            reasons.append("not mesh geometry: " + ", ".join(rejected[:6]))
        if objects and all(
            o is not None and (o.name in sources or o.type == "LIGHT") for o in objects
        ):
            reasons.append("the selection is the shadow source itself")
        if not recursive:
            reasons.append("Include Children is off")
        raise ValueError(
            "Select the mesh(es) to cast a shadow from"
            + (" — " + "; ".join(reasons) if reasons else "")
            + "."
        )

    def b001(self):
        """Reset to Defaults — restore all UI widgets to their default values."""
        self.ui.state.reset_all()

    # ------------------------------------------------------------- utility
    def _selected_planes(self, action, allow_all=False):
        """The shadow planes the selection touches for a Utility *action*; with
        *allow_all*, every plane when nothing is selected. Reports and returns an empty
        list when there is nothing to act on."""
        import blendertk as btk

        sel = [o for o in (btk.selected_objects() or []) if o is not None]
        if not sel:
            if allow_all:
                planes = ShadowRig.find_shadow_planes()
                if not planes:
                    self.sb.message_box("No shadow planes in the file.")
                return planes
            self.sb.message_box(
                f"Select the shadow plane(s) to {action} — or the rig's group, a "
                "target, or its source."
            )
            return []
        planes = ShadowRig.planes_for_nodes(sel)
        if not planes:
            self.sb.message_box(
                "The selection touches no shadow rig. Select the plane(s) to "
                f"{action}, the rig's group, a target, or its source"
                + (", or clear the selection to act on all." if allow_all else ".")
            )
        return planes

    @staticmethod
    def _undo_push(message):
        import bpy

        try:
            bpy.ops.ed.undo_push(message=message)
        except RuntimeError:
            pass  # headless / no undo stack

    def chk_horizon_preview(self, checked):
        """Live Horizon Preview: a viewport overlay on the horizon plane(s) the
        selection touches (or all) that evaluates the baked map from the live
        source, so the outline morphs as the light moves -- what Unity and
        the WebXR viewer will show. Display state only: the plane's material
        is untouched and its visibility comes back when the box is cleared,
        and every preview is stood down before an FBX export."""
        from blendertk.rig_utils.shadow_preview import ShadowPreview

        planes = self._selected_planes("preview", allow_all=True)
        horizon = [p for p in planes if ShadowRig.plane_type(p) == "horizon"]
        if not horizon:
            if planes:
                self.sb.message_box(
                    "The selection touches no <b>Horizon</b> rig. The live "
                    "preview evaluates a baked horizon map; a Projected rig's "
                    "silhouette already is its preview."
                )
            self.ui.chk_horizon_preview.blockSignals(True)
            self.ui.chk_horizon_preview.setChecked(False)
            self.ui.chk_horizon_preview.blockSignals(False)
            return
        if checked:
            refusal = ShadowPreview.refusal()
            if refusal:
                self.sb.message_box(refusal)
                self.ui.chk_horizon_preview.blockSignals(True)
                self.ui.chk_horizon_preview.setChecked(False)
                self.ui.chk_horizon_preview.blockSignals(False)
                return
        done, failed = ShadowPreview.toggle(horizon, on=checked)
        if failed:
            self.sb.message_box(
                self._summary(
                    "Horizon preview " + ("on" if checked else "off"), done, failed
                )
            )

    def b002(self):
        """Bake to Keyframes: bake the rig(s) the selection touches (or all) to keys over
        the scene frame range and remove the live drivers."""
        planes = self._selected_planes("bake", allow_all=True)
        if not planes:
            return
        baked = ShadowRig.bake_planes(planes)
        self._undo_push("Shadow Rig: Bake")
        if baked:
            self.sb.message_box(f"Baked {len(baked)} shadow plane(s) to keyframes.")
        else:
            self.sb.message_box("No shadow planes with live drivers found.")

    def b003(self):
        """Recalculate Silhouette: re-render the rig(s) the selection touches (or all)
        from their source's current position."""
        planes = self._selected_planes("recalculate", allow_all=True)
        if not planes:
            return
        refreshed = ShadowRig.refresh_silhouette(planes)
        self._undo_push("Shadow Rig: Recalculate Silhouette")
        if refreshed:
            self.sb.message_box(f"Recalculated {len(refreshed)} silhouette(s).")
        else:
            self.sb.message_box(
                "No shadow planes to recalculate (rigs built before the "
                "target/source stamps must be re-created)."
            )

    def b004(self):
        """Source From Selection: the selected object(s) become the source(s).
        A mesh in EDIT mode is a fixture — the mirror of the Maya panel's face
        selection: a real area light is built per mesh
        (``LightUtils.lights_from_geometry``) and those lights become the
        sources."""
        import blendertk as btk

        sel = [o for o in (btk.selected_objects() or []) if o is not None]
        fixtures = [o for o in sel if o.type == "MESH" and o.mode == "EDIT"]
        if fixtures:
            from blendertk.light_utils._light_utils import LightUtils

            created = LightUtils.lights_from_geometry(fixtures)
            if not created:
                self.sb.message_box(
                    "No area light could be built from the edited mesh(es)."
                )
                return
            self._set_source_names(list(created))
            self.sb.message_box(
                f"Built {len(created)} area light(s) from the edited mesh(es); "
                "they are now the shadow source(s). Select the target(s) and "
                "enable Preview."
            )
            self._on_sources_edited()
            return
        if not sel:
            self.sb.message_box(
                "Select the object(s) to use as shadow source(s) — or edit a "
                "fixture mesh to build an area light from it."
            )
            return
        self._set_source_names([o.name for o in sel])
        self._on_sources_edited()

    def apply_source(self):
        """Apply Source (Recalculate's option box): re-point the rig(s) the
        selection touches at the first Source Name and re-render their
        silhouettes."""
        planes = self._selected_planes("re-source")
        if not planes:
            return
        source = ShadowRig.ensure_source(self._source_names()[0])
        done, failed = [], []
        for plane in planes:
            rig = ShadowRig.from_plane(plane)
            if rig is None:
                failed.append(f"{plane.name}: built before the stamps")
                continue
            try:
                rig.set_source(source, size=self._resolution())
                done.append(plane.name)
            except Exception as e:
                failed.append(f"{plane.name}: {e}")
                self.logger.error(f"Apply Source ({plane.name}): {e}", exc_info=True)
        self._undo_push("Shadow Rig: Apply Source")
        self.sb.message_box(self._summary(f"Source now {source.name}", done, failed))

    def rebuild_rig(self):
        """Rebuild Rig (Recalculate's option box): re-create the rig(s) the
        selection touches from the target's current geometry with this
        panel's options."""
        planes = self._selected_planes("rebuild")
        if not planes:
            return
        done, failed = [], []
        for plane in planes:
            name = plane.name
            try:
                rig = ShadowRig.rebuild(
                    plane,
                    texture_res=self._resolution(),
                    recursive=self.ui.chk_combine.isChecked(),
                )
            except Exception as e:
                failed.append(f"{name}: {e}")
                self.logger.error(f"Rebuild ({name}): {e}", exc_info=True)
                continue
            if rig is None:
                failed.append(
                    f"{name}: built before the stamps, or its objects are gone"
                )
            else:
                done.append(name)
        self._undo_push("Shadow Rig: Rebuild")
        self.sb.message_box(self._summary("Rebuilt", done, failed))

    def restore_expression(self):
        """Restore Expression (Bake's option box): un-bake the rig(s) the
        selection touches."""
        planes = self._selected_planes("restore")
        if not planes:
            return
        restored = ShadowRig.unbake_planes(planes)
        self._undo_push("Shadow Rig: Restore Expression")
        if restored:
            self.sb.message_box(f"Restored the drivers on {len(restored)} plane(s).")
        else:
            self.sb.message_box(
                "Nothing to restore: the selected rig(s) are live already, or "
                "predate the target/source stamps."
            )

    def b009(self):
        """Delete Rig: tear down the rig(s) the selection touches."""
        planes = self._selected_planes("delete")
        if not planes:
            return
        deleted = ShadowRig.delete_rigs(planes)
        self._undo_push("Shadow Rig: Delete")
        self.sb.message_box(f"Deleted {len(deleted)} shadow rig(s).")

    def b010(self):
        """Pack Atlas: pack or repack every shadow rig's tiles into the file's
        atlases (one per kind)."""
        planes = ShadowRig.find_shadow_planes()
        if not planes:
            self.sb.message_box("No shadow planes in the file.")
            return
        packed = ShadowRig.pack_atlas(planes)
        self._undo_push("Shadow Rig: Pack Atlas")
        if packed:
            names = ", ".join(os.path.basename(p) for p in packed.values())
            self.sb.message_box(f"Packed {len(planes)} plane(s) into {names}.")
        else:
            self.sb.message_box("No tiles to pack — the planes' PNGs are missing.")

    @staticmethod
    def _summary(what, done, failed):
        lines = []
        if done:
            lines.append(f"{what}: {', '.join(done)}")
        if failed:
            lines.append("Failed:\n  " + "\n  ".join(failed))
        return "\n".join(lines) or "Nothing done."

    def perform_operation(self, objects):
        """Build one shadow rig per source for the selected target(s). Called by Preview on
        enable/refresh and on commit."""
        recursive = self.ui.chk_combine.isChecked()
        targets = self._shadow_targets(objects, recursive)
        names = self._source_names()
        builder = self._rig_builder()
        groups = [[t] for t in targets] if self._per_object() else [targets]
        rigs = []
        for group in groups:
            rigs.extend(
                builder(
                    group, names, texture_res=self._resolution(), recursive=recursive
                )
            )
        self._built_sources = names
        # Packing is deferred to the commit hook (see _pack_after_commit).
        self._built_rigs = rigs
        # Tracked for _restore_after_preview (a canceled preview removes them):
        # each rig's OWN PNGs. Never the atlas — it is a shared file that rigs
        # already in the scene sample.
        for rig in rigs:
            self._preview_textures.extend(
                p for p in (rig.texture_path, rig.horizon_path) if p
            )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("shadow_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
