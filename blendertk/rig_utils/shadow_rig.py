# !/usr/bin/python
# coding=utf-8
"""Shadow Rig — engine + Switchboard slot wiring for the co-located ``shadow_rig.ui``.

Blender port of mayatk's ``rig_utils.shadow_rig`` (``btk.ShadowRig`` ↔ ``mtk.ShadowRig``): a
projected-shadow rig for engine export — a quad plane carrying the targets' silhouette baked to a
PNG, its transform driven to follow a light/source so it reads as a contact/cast shadow.

Two modes mirror Maya:
  - ``"stretch"`` (default, bake-friendly): plane stays axis-aligned; scales along X/Y with a
    compensatory translation that anchors the edge facing the light.
  - ``"orbit"``: plane rotates about the up axis to face away from the light; scales along its
    depth.

**Coordinate system.** Maya is Y-up (ground = XZ, plane rotates about Y); Blender is **Z-up**
(ground = XY, plane rotates about **Z**). The port remaps Maya-Y→Blender-Z (up) and Maya-Z→Blender-Y
(the second horizontal axis) throughout — the silhouette, the driver math, and the driven channels.

**Drivers, not an expression.** Maya wires the transform with one ``expression`` node; Blender uses
one **driver per driven channel** (built on the shared ``RigUtils``), each reading the light's and
contact's WORLD-space position via ``TRANSFORMS`` variables (so parent animation is captured without
Maya's ``decomposeMatrix``) plus the plane's keyable props via ``SINGLE_PROP`` vars. Expressions are
**branchless arithmetic** (``min``/``max``/``sqrt``/``atan2``/``pow``) so they stay on Blender's fast
driver parser — a Python ternary would force the slow full-Python parser (and a security gate). The
two places Maya branched (the ``px``/``py`` pivot sign and the ``scaleInfluence`` height gate) are
expressed branchlessly with an ``abs``-sign and a clamped ``max``; they match Maya exactly except in
a degenerate pose (light exactly on a horizontal axis / at-or-below the contact **with**
``scaleInfluence`` engaged), where the bounded result differs harmlessly — documented inline.

Silhouette: world tris are pulled from the evaluated depsgraph and handed to the shared, Qt-free
``pythontk.ImgUtils.rasterize_silhouette`` (a Y/Z column swap presents Blender-Z as its "up" so its
axis semantics match Maya's), then persisted to PNG via ``bpy.data.images`` (no cv2/PIL dependency).
Material: unlit **black Emission** mixed against a **Transparent BSDF** by ``tex.alpha × opacity``
(opacity a driven Value node) — the Blender analogue of Maya's StingrayPBS unlit-transparent.

``import bpy`` / ``numpy`` are deferred into the call bodies and the Qt-only ``uitk`` helper into its
method, so the module resolves headless and loads under the workspace ``.venv``.
"""
import os

import pythontk as ptk

from blendertk.rig_utils._rig_utils import RigUtils


class ShadowRig(ptk.LoggingMixin):
    """Projected-shadow rig for engine export (mirror of mayatk's ``ShadowRig``)."""

    MODES = ("orbit", "stretch")

    # UI axis letter → ``rasterize_silhouette`` axis. The combo's item TEXT is copied verbatim from
    # mayatk's Y-up labels ("Y (Top)" / "Z (Front)"), but the letter each maps to is unchanged
    # (``ShadowRigSlots._AXIS_MAP``, identical to mayatk's). ``create_silhouette_texture`` already Y/Z-swaps
    # the point columns before rasterizing so Blender-Z sits in the rasterizer's "up" (frame-col1)
    # slot and Blender-Y sits in its "front" (frame-col2) slot — i.e. the swap alone reproduces
    # Maya's Y-up/Z-front frame, so the UI letter needs NO further translation: 'y' → rasterize 'y'
    # (excludes frame-col1 = Blender Z → true top-down, matching "Y (Top)") and 'z' → rasterize 'z'
    # (excludes frame-col2 = Blender Y, matching "Z (Front)"). Identity map, kept explicit for
    # readability / as the single place this reasoning is anchored.
    _RASTER_AXIS = {"auto": "auto", "x": "x", "y": "y", "z": "z"}

    def __init__(self, targets=None, ground_height=0.0, mode="stretch"):
        super().__init__()
        objs = ptk.make_iterable(targets) if targets is not None else []
        self.targets = [o for o in (RigUtils.resolve_object(t) for t in objs) if o]
        self.ground_height = float(ground_height)
        self.mode = mode if mode in self.MODES else "stretch"

        self.light = None
        self.contact = None
        self.shadow_plane = None
        self.material = None
        self.image = None
        self.texture_path = None
        self.plane_size = 1.0

        # Naming base — first target, or "combined" for a multi-target shadow (Maya parity).
        self._base = self.targets[0].name if len(self.targets) == 1 else "combined"

    # ------------------------------------------------------------------ handles
    def create_contact_locator(self):
        """Empty at the footprint's lowest point (min-Z), parented to the first target so it tracks."""
        import bpy

        lo, hi = self._world_bounds()
        loc = ((lo[0] + hi[0]) * 0.5, (lo[1] + hi[1]) * 0.5, lo[2])  # center XY, min Z (ground-up)
        self.contact = RigUtils.create_locator(
            f"{self._base}_contact", location=loc, display_type="PLAIN_AXES", size=0.2
        )
        RigUtils.parent_keep_transform(self.contact, self.targets[0])
        return self.contact

    def get_or_create_shadow_source(self, position=(5.0, 5.0, 10.0), source_name="shadow_source"):
        """Reuse an existing source Empty by name, else create one (Z-up default: high on +Z)."""
        import bpy

        existing = bpy.data.objects.get(source_name)
        if existing is not None:
            self.light = existing
        else:
            self.light = RigUtils.create_locator(
                source_name, location=position, display_type="SPHERE", size=1.0
            )
        return self.light

    def create_shadow_plane(self):
        """Create a flat quad on the XY ground (normal +Z), centered at the footprint, with the
        keyable shadow props (``shadowIntensity`` / ``falloffPower`` / ``scaleInfluence`` /
        ``basePlaneSize``)."""
        import bpy

        if not self.targets:
            raise ValueError("Target object(s) required")

        lo, hi = self._world_bounds()
        self.plane_size = max((hi[0] - lo[0]) * 1.1, (hi[1] - lo[1]) * 1.1, 1.0)
        cx, cy = (lo[0] + hi[0]) * 0.5, (lo[1] + hi[1]) * 0.5

        self.shadow_plane = self._build_plane(f"{self._base}_shadow", self.plane_size)
        self.shadow_plane.location = (cx, cy, self.ground_height + 0.01)

        RigUtils.ensure_custom_prop(self.shadow_plane, "shadowIntensity", 1.0, 0.0, 1.0)
        RigUtils.ensure_custom_prop(self.shadow_plane, "falloffPower", 1.2, 0.0, 5.0)
        RigUtils.ensure_custom_prop(self.shadow_plane, "scaleInfluence", 0.0, 0.0, 1.0)
        # basePlaneSize is read live by the drivers; always (re)stamp it to this build's size.
        RigUtils.ensure_custom_prop(self.shadow_plane, "basePlaneSize", self.plane_size, 0.0)
        self.shadow_plane["basePlaneSize"] = self.plane_size
        return self.shadow_plane

    @staticmethod
    def _build_plane(name, size):
        """A centered, single-quad XY plane mesh (4 verts, full 0-1 UV) — no ``bpy.ops`` (so it is
        context-free / preview-safe)."""
        import bpy

        r = size * 0.5
        me = bpy.data.meshes.new(f"{name}_mesh")
        verts = [(-r, -r, 0.0), (r, -r, 0.0), (r, r, 0.0), (-r, r, 0.0)]
        me.from_pydata(verts, [], [(0, 1, 2, 3)])
        me.update()
        uv = me.uv_layers.new(name="UVMap")
        for i, co in enumerate(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))):
            uv.data[i].uv = co
        obj = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(obj)
        return obj

    # ------------------------------------------------------------------ silhouette
    def create_silhouette_texture(self, size=512, axis="auto", recursive=True, **kwargs):
        """Rasterize the targets' world silhouette to an RGBA PNG via
        ``pythontk.ImgUtils.rasterize_silhouette`` and load it as a reusable image datablock.

        ``kwargs`` (``uniform_alpha`` / ``falloff_power`` / ``vertical_weight`` / ``blur_amount``)
        pass straight through to the shared rasterizer.
        """
        import numpy as np
        import bpy

        meshes = self._gather_world_meshes(recursive)
        if not meshes:
            raise ValueError("No mesh geometry found on the target(s).")
        # Present Blender-Z as the rasterizer's up by swapping the Y/Z columns; then its axis
        # semantics (and 'auto' = perpendicular-to-widest-horizontal) match Maya's exactly.
        swapped = [(p[:, [0, 2, 1]], t) for p, t in meshes]
        raster_axis = self._RASTER_AXIS.get(axis, "auto")
        arr = ptk.ImgUtils.rasterize_silhouette(swapped, size=size, axis=raster_axis, **kwargs)

        self.texture_path = os.path.join(self._output_dir(), f"{self._base}_shadow.png")
        img_name = f"{self._base}_shadow"
        img = bpy.data.images.get(img_name)
        if img is not None and tuple(img.size) != (size, size):
            bpy.data.images.remove(img)  # resolution changed — can't resize in place
            img = None
        if img is None:
            img = bpy.data.images.new(img_name, size, size, alpha=True)
        img.pixels.foreach_set((arr.astype(np.float32) / 255.0).ravel())
        img.update()
        img.filepath_raw = self.texture_path
        img.file_format = "PNG"
        try:
            img.save()
        except RuntimeError as e:  # read-only dir / locked file — keep the in-memory datablock
            self.logger.warning(f"Could not save silhouette PNG ({e}); using in-memory texture.")
        self.image = img
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

    def _world_bounds(self):
        """``(min_xyz, max_xyz)`` world AABB over the targets **and their descendant geometry**
        (Maya's ``exactWorldBoundingBox`` includes descendants regardless of the silhouette's
        recursive flag — so a group/empty parent gets a footprint from its mesh children, not the
        empty's meaningless unit ``bound_box``)."""
        from mathutils import Vector

        objs = []
        for o in self.targets:
            objs.append(o)
            objs.extend(o.children_recursive)
        pts = []
        for o in objs:
            if o is None or o.type == "EMPTY":  # an empty's bound_box is a unit cube → skip
                continue
            for corner in o.bound_box:
                pts.append(o.matrix_world @ Vector(corner))
        if not pts:
            return (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
        lo = tuple(min(p[i] for p in pts) for i in range(3))
        hi = tuple(max(p[i] for p in pts) for i in range(3))
        return lo, hi

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
        """Build the transform + opacity drivers for the active mode, then force one recompile."""
        if self.mode == "orbit":
            self._drivers_orbit()
        else:
            self._drivers_stretch()
        # Script-built drivers cache a stale compile until the depsgraph settles + each expression is
        # re-assigned (the shared RigUtils gotcha). The opacity driver lives on the material's tree.
        RigUtils.refresh_drivers([self.shadow_plane, self.material.node_tree])

    # Shared sub-expression fragments (Blender Z-up: up = Z; horizontals = X, Y). ``G`` is the
    # ground height baked as a literal (Maya bakes it the same way).
    def _frags(self):
        G = repr(self.ground_height)
        relH = f"max(0.1, Lz - {G})"
        # baseScale (scaleInfluence): Maya gates on (si>0 and hDiff>0.1); branchless here via
        # max(Lz-Cz, 0.1). Identical when si==0 (default) for any pose; differs only with si>0 AND
        # the light at/below the contact, where the clamped result is harmlessly bounded.
        base = "min(max(1.0 + ((Lz / max(Lz - Cz, 0.1)) - 1.0) * si, 0.5), 3.0)"
        return G, relH, base

    def _plane_props(self, *, influence=False, size=False, opacity=False):
        """Convenience: the SINGLE_PROP var specs this driver needs off the shadow plane."""
        specs = []
        if influence:
            specs.append(("si", self.shadow_plane, '["scaleInfluence"]'))
        if size:
            specs.append(("size", self.shadow_plane, '["basePlaneSize"]'))
        if opacity:
            specs.append(("intensity", self.shadow_plane, '["shadowIntensity"]'))
            specs.append(("power", self.shadow_plane, '["falloffPower"]'))
        return specs

    def _drivers_stretch(self):
        """Stretch mode: axis-aligned plane, scale X/Y + compensatory translation, no rotation."""
        p, L, C = self.shadow_plane, self.light, self.contact
        _, relH, base = self._frags()
        sx = f"((1.0 + min(max(abs(Cx - Lx) / {relH}, 0.0), 4.0)) * {base})"
        sy = f"((1.0 + min(max(abs(Cy - Ly) / {relH}, 0.0), 4.0)) * {base})"
        # px/py: Maya's (d>0 ? -radius : +radius). eps-guarded sign — exact except at d==0, where
        # tx = px*(1-sx) is 0 anyway unless scaleInfluence pushes sx≠1 (a degenerate, bounded case).
        px = "(-(size * 0.5) * (Cx - Lx) / (abs(Cx - Lx) + 1e-6))"
        py = "(-(size * 0.5) * (Cy - Ly) / (abs(Cy - Ly) + 1e-6))"

        # location: world XY anchored to the contact + compensatory shift; Z static on the ground.
        self._driver(p, "location", 0, f"Cx + {px} * (1.0 - {sx})",
                     [("Cx", C, "LOC_X"), ("Lx", L, "LOC_X"), ("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")],
                     self._plane_props(influence=True, size=True))
        self._driver(p, "location", 1, f"Cy + {py} * (1.0 - {sy})",
                     [("Cy", C, "LOC_Y"), ("Ly", L, "LOC_Y"), ("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")],
                     self._plane_props(influence=True, size=True))
        self._driver(p, "scale", 0, sx,
                     [("Cx", C, "LOC_X"), ("Lx", L, "LOC_X"), ("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")],
                     self._plane_props(influence=True))
        self._driver(p, "scale", 1, sy,
                     [("Cy", C, "LOC_Y"), ("Ly", L, "LOC_Y"), ("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")],
                     self._plane_props(influence=True))
        self._set_static(rotation_z=0.0)
        # Opacity falls off with the larger stretch (reads the plane's own driven scale X/Y).
        self._opacity_driver(
            "min(max((intensity / max(0.001, pow(max(sclX, sclY), power))) "
            "* min(max(Lz - Cz, 0.0), 1.0), 0.0), 1.0)",
            [("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")],
            self._plane_props(opacity=True)
            + [("sclX", p, "scale[0]"), ("sclY", p, "scale[1]")],
        )

    def _drivers_orbit(self):
        """Orbit mode: plane rotates about Z to face away from the light; scales along its depth (Y)."""
        p, L, C = self.shadow_plane, self.light, self.contact
        _, relH, base = self._frags()
        dist = "sqrt((Cx - Lx) * (Cx - Lx) + (Cy - Ly) * (Cy - Ly))"
        sy = f"((1.0 + min(max(({dist}) / {relH}, 0.0), 4.0)) * {base})"
        offset = f"((size * 0.5) * ({sy} - 1.0))"
        nx = f"((Cx - Lx) / (({dist}) + 1e-6))"
        ny = f"((Cy - Ly) / (({dist}) + 1e-6))"

        common = [("Cx", C, "LOC_X"), ("Lx", L, "LOC_X"), ("Cy", C, "LOC_Y"),
                  ("Ly", L, "LOC_Y"), ("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")]
        self._driver(p, "location", 0, f"Cx + {nx} * {offset}", common,
                     self._plane_props(influence=True, size=True))
        self._driver(p, "location", 1, f"Cy + {ny} * {offset}", common,
                     self._plane_props(influence=True, size=True))
        self._driver(p, "scale", 1, sy, common, self._plane_props(influence=True))
        # rotation about Z (radians — Blender, unlike Maya's degrees): face away from the light.
        self._driver(p, "rotation_euler", 2, "atan2(Cx - Lx, Cy - Ly)",
                     [("Cx", C, "LOC_X"), ("Lx", L, "LOC_X"), ("Cy", C, "LOC_Y"), ("Ly", L, "LOC_Y")], [])
        self._set_static(scale_x=1.0)
        self._opacity_driver(
            "min(max((intensity / max(0.001, pow(sclY, power))) "
            "* min(max(Lz - Cz, 0.0), 1.0), 0.0), 1.0)",
            [("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")],
            self._plane_props(opacity=True) + [("sclY", p, "scale[1]")],
        )

    def _set_static(self, rotation_z=None, scale_x=None):
        """Set the channels that are constant in a given mode (Z stays on the ground always)."""
        self.shadow_plane.location[2] = self.ground_height + 0.005
        if rotation_z is not None:
            self.shadow_plane.rotation_euler[2] = rotation_z
        if scale_x is not None:
            self.shadow_plane.scale[0] = scale_x

    @staticmethod
    def _driver(obj, data_path, index, expression, loc_vars, prop_vars):
        """Build one re-entrant SCRIPTED driver on ``obj.<data_path>[index]`` from world-transform
        + custom-prop variables. ``loc_vars``: ``[(name, target, transform_type)]``;
        ``prop_vars``: ``[(name, id_obj, data_path)]``. Returns the fcurve."""
        RigUtils.remove_driver(obj, data_path, index)
        name0, tgt0, tt0 = loc_vars[0]
        fc = RigUtils.add_transform_driver(obj, data_path, index, tgt0, tt0, var_name=name0)
        for name, tgt, tt in loc_vars[1:]:
            RigUtils.add_transform_var(fc, name, tgt, tt)
        for name, idobj, dpath in prop_vars:
            RigUtils.add_prop_var(fc, name, idobj, dpath)
        fc.driver.expression = expression
        return fc

    def _opacity_driver(self, expression, loc_vars, prop_vars):
        """The opacity driver lives on the material node tree's ``opacity`` Value node."""
        nt = self.material.node_tree
        path = 'nodes["opacity"].outputs[0].default_value'
        return self._driver(nt, path, None, expression, loc_vars, prop_vars)

    # ------------------------------------------------------------------ orchestration
    @classmethod
    def create(
        cls, targets, light_pos=(5.0, 5.0, 10.0), texture_res=512, axis="auto",
        source_name="shadow_source", recursive=True, mode="stretch", ground_height=0.0,
    ):
        """Build a projected-shadow rig for ``targets`` (mirror of mayatk's ``ShadowRig.create``)."""
        import bpy

        rig = cls(targets=targets, ground_height=ground_height, mode=mode)
        if not rig.targets:
            raise ValueError("Shadow Rig needs at least one target object.")
        rig.get_or_create_shadow_source(position=light_pos, source_name=source_name)
        rig.create_contact_locator()
        rig.create_shadow_plane()
        rig.create_silhouette_texture(size=texture_res, axis=axis, recursive=recursive)
        rig.create_material()
        rig.setup_drivers()

        grp = RigUtils.create_group(f"{rig._base}_shadow_grp", children=[rig.shadow_plane])
        rig.group = grp
        rig.logger.success(
            f"Shadow rig '{rig._base}' ({mode}) — plane {rig.shadow_plane.name}, "
            f"source {rig.light.name}, texture {rig.texture_path}"
        )
        return rig


class ShadowRigSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Shadow Rig panel.

    Self-contained (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` helper is deferred into
    ``header_init``. Mirrors the Maya panel: a live :class:`blendertk.Preview` rebuilds the rig as
    options change; **Create Shadow** commits.
    """

    _AXIS_MAP = {0: "auto", 1: "x", 2: "y", 3: "z"}
    _MODE_MAP = {0: "stretch", 1: "orbit"}

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shadow_rig
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[shadow_rig] ")

        from blendertk.core_utils.preview import Preview

        self.preview = Preview(
            self, self.ui.chk_preview, self.ui.b000, message_func=self.sb.message_box
        )
        # Any option change re-bakes the previewed rig (mirror of the Maya panel).
        self.ui.cmb_mode.currentIndexChanged.connect(self.preview.refresh)
        self.ui.chk_combine.toggled.connect(self.preview.refresh)
        self.ui.txt_source.editingFinished.connect(self.preview.refresh)
        self.ui.s000.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)
        self.ui.b001.clicked.connect(self.b001)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Shadow Rig",
                body="Create a projected-shadow plane rig that exports cleanly for game engines "
                "(Unity, etc.). The plane carries a baked silhouette PNG; its transform is driven "
                "to follow the shadow source. Bake the drivers to keys (or leave them live) before "
                "FBX export.",
                steps=[
                    "Select one or more target objects.",
                    "Enable <b>Preview</b> to build the rig live.",
                    "Tweak <b>Mode</b>, <b>Resolution</b>, <b>Axis</b>, and <b>Include Children</b> "
                    "— the preview refreshes on each change.",
                    "Press <b>Create Shadow</b> to commit, or disable Preview to discard.",
                    "Move the <i>shadow_source</i> empty to warp the shadow.",
                ],
                sections=[
                    ("Modes", [
                        "<b>Stretch</b> — Plane stays axis-aligned; scales + compensatory "
                        "translation warp the shadow. Bake-friendly (default).",
                        "<b>Orbit</b> — Plane rotates about Z to face away from the light; shadow "
                        "always points away from the source.",
                    ]),
                ],
                notes=[
                    "Blender is Z-up: the plane lies on the XY ground. The Axis combo's labels "
                    "are mirrored verbatim from Maya's Y-up panel — <b>Y (Top)</b> is the "
                    "top-down projection (along Blender's actual up axis, Z) and "
                    "<b>Z (Front)</b> is the other horizontal (Blender Y).",
                    "Tweak <i>shadowIntensity</i> / <i>falloffPower</i> / <i>scaleInfluence</i> "
                    "live on the plane's Custom Properties.",
                    "For export: bake the drivers (Object ▸ Animation ▸ Bake Action, or leave "
                    "live), export FBX with the plane + texture, and use an Unlit/Transparent "
                    "shader in-engine.",
                ],
            )
        )

    def b001(self):
        """Reset to Defaults — restore all UI widgets to their default values."""
        self.ui.state.reset_all()

    def perform_operation(self, objects):
        """Build the shadow rig for the selected target(s). Called by Preview on enable/refresh and
        on commit."""
        targets = [o for o in (objects or []) if o is not None]
        if not targets:
            return

        res_text = self.ui.s000.currentText()
        try:
            resolution = int(res_text.replace("Resolution: ", "").strip())
        except (ValueError, AttributeError):
            resolution = 512
        source_name = (self.ui.txt_source.text() or "").strip() or "shadow_source"
        recursive = self.ui.chk_combine.isChecked()
        axis = self._AXIS_MAP.get(self.ui.cmb000.currentIndex(), "auto")
        mode = self._MODE_MAP.get(self.ui.cmb_mode.currentIndex(), "stretch")

        ShadowRig.create(
            targets, texture_res=resolution, axis=axis, source_name=source_name,
            recursive=recursive, mode=mode,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("shadow_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
