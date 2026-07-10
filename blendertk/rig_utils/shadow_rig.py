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
    depth. Correct for animated/orbiting lights (the silhouette never mirrors).

Shadow behavior (mirror of Maya; both modes):
  - The plane hangs off the **projected ground anchor** — where the light ray through the contact
    point hits the ground — so the shadow slides away from the light as the target leaves the
    ground (and stays put while it's grounded).
  - Stretch is proportional to the measured ``objectHeight`` (tall objects cast long shadows,
    flat ones don't), clamped by the keyable ``maxStretch``.
  - Opacity fades with stretch (``falloffPower``), with the light dropping toward the contact,
    and with the target rising off the ground (``fadeHeight`` = rise at full fade).

**Coordinate system.** Maya is Y-up (ground = XZ, plane rotates about Y); Blender is **Z-up**
(ground = XY, plane rotates about **Z**). The port remaps Maya-Y→Blender-Z (up) and Maya-Z→Blender-Y
(the second horizontal axis) throughout — the silhouette, the driver math, and the driven channels.
Note the two frames differ by a reflection, so rotation/orientation formulas are re-derived rather
than transliterated: orbit's Z rotation is ``atan2(Lx-Cx, Cy-Ly)`` (R_z(t) sends +Y to
(-sin t, cos t)), and the light-view silhouette u-axis is ``(dy, -dx)``.

**Drivers, not an expression.** Maya wires the transform with one ``expression`` node; Blender uses
one **driver per driven channel** (built on the shared ``RigUtils``), each reading the light's and
contact's WORLD-space position via ``TRANSFORMS`` variables (so parent animation is captured without
Maya's ``decomposeMatrix``) plus the plane's keyable props via ``SINGLE_PROP`` vars. Expressions are
**branchless arithmetic** (``min``/``max``/``sqrt``/``atan2``/``pow``) so they stay on Blender's fast
driver parser — a Python ternary would force the slow full-Python parser (and a security gate). The
places Maya branches (the ``px``/``py`` pivot sign and the ``scaleInfluence`` height gate) are
expressed branchlessly with an ``abs``-sign and a clamped ``max``; they match Maya exactly except in
a degenerate pose (light exactly on a horizontal axis / at-or-below the contact **with**
``scaleInfluence`` engaged), where the bounded result differs harmlessly. Expressions are written
compact (no spaces, short float literals) because Blender caps a driver expression at 255 chars —
the stretch-mode location driver inlines the full scale formula and would overflow a spaced form.
Orbit mode sidesteps the same overflow by building its plane with the origin at the **heel** edge
(rotation/scale about the anchor needs no offset term) — world-space result identical to Maya's
center-pivot + offset.

Silhouette: world tris are pulled from the evaluated depsgraph and handed to the shared, Qt-free
``pythontk.ImgUtils.rasterize_silhouette``. ``axis='auto'``/``'light'`` projects **as seen from the
light's horizontal bearing** (the physically correct shape for a ground shadow; 'auto' falls back
to the widest-dimension heuristic when the light is overhead). Explicit ``'x'/'y'/'z'`` keep Maya's
Y-up semantics via a Y/Z column swap. PNG persisted via ``bpy.data.images`` (no cv2/PIL dependency).
Material: unlit **black Emission** mixed against a **Transparent BSDF** by ``tex.alpha × opacity``
(opacity a driven Value node) — the Blender analogue of Maya's StingrayPBS unlit-transparent. The
opacity fade is shader-side (viewport only): FBX carries the plane's baked transform, not material
animation — mirror of the Maya note.

**Engine hand-off.** ``refresh_export_metadata`` publishes a ``shadow_metadata`` JSON channel onto
the shared ``data_export`` carrier (``btk.DataNodes``; at create/bake — blendertk producers publish
at authoring time, there is no before-FBX-export hook). The Scene Exporter's ``export_data_node``
task ships it; unitytk's ``ShadowPlaneController.cs`` joins records to the imported planes by
GameObject name and finishes the Unity setup automatically (mirror of the lightmap channel).

``import bpy`` / ``numpy`` are deferred into the call bodies and the Qt-only ``uitk`` helper into its
method, so the module resolves headless and loads under the workspace ``.venv``.
"""
import os

import pythontk as ptk

from blendertk.rig_utils._rig_utils import RigUtils


class ShadowRig(ptk.LoggingMixin):
    """Projected-shadow rig for engine export (mirror of mayatk's ``ShadowRig``)."""

    MODES = ("orbit", "stretch")
    # Lift above the ground plane to avoid z-fighting (build + drivers; Maya parity).
    GROUND_OFFSET = 0.01
    # data_export carrier channel (see refresh_export_metadata).
    SHADOW_METADATA = "shadow_metadata"

    # UI axis letter → ``rasterize_silhouette`` axis for the EXPLICIT letters. The combo's item
    # TEXT is copied verbatim from mayatk's Y-up labels ("Y (Top)" / "Z (Front)"), but the letter
    # each maps to is unchanged (``ShadowRigSlots._AXIS_MAP``, identical to mayatk's).
    # ``create_silhouette_texture`` Y/Z-swaps the point columns before rasterizing so Blender-Z
    # sits in the rasterizer's "up" (frame-col1) slot and Blender-Y in its "front" (frame-col2)
    # slot — i.e. the swap alone reproduces Maya's Y-up/Z-front frame, so the UI letter needs NO
    # further translation. ``'auto'``/``'light'`` are handled before this map (light-view frame).
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
        self.object_height = 0.0

        # Naming base — first target, or "combined" for a multi-target shadow (Maya parity).
        self._base = self.targets[0].name if len(self.targets) == 1 else "combined"

    # ------------------------------------------------------------------ handles
    def create_contact_locator(self):
        """Empty at the footprint's lowest point (min-Z), parented to the first target so it tracks."""
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
        ``maxStretch`` / ``fadeHeight``) + the measured constants the drivers read
        (``basePlaneSize`` / ``objectHeight``)."""
        if not self.targets:
            raise ValueError("Target object(s) required")

        lo, hi = self._world_bounds()
        self.plane_size = max((hi[0] - lo[0]) * 1.1, (hi[1] - lo[1]) * 1.1, 1.0)
        self.object_height = max(hi[2] - lo[2], 0.001)
        cx, cy = (lo[0] + hi[0]) * 0.5, (lo[1] + hi[1]) * 0.5

        # Orbit rotates/scales about the heel (anchor) edge; stretch about the center (Maya parity).
        origin = "edge" if self.mode == "orbit" else "center"
        self.shadow_plane = self._build_plane(f"{self._base}_shadow", self.plane_size, origin)
        self.shadow_plane.location = (cx, cy, self.ground_height + self.GROUND_OFFSET)

        RigUtils.ensure_custom_prop(self.shadow_plane, "shadowIntensity", 1.0, 0.0, 1.0)
        RigUtils.ensure_custom_prop(self.shadow_plane, "falloffPower", 1.2, 0.0, 5.0)
        RigUtils.ensure_custom_prop(self.shadow_plane, "scaleInfluence", 0.0, 0.0, 1.0)
        RigUtils.ensure_custom_prop(self.shadow_plane, "maxStretch", 4.0, 0.0, 10.0)
        # Rise above the ground at which the shadow has fully faded out.
        RigUtils.ensure_custom_prop(
            self.shadow_plane, "fadeHeight", max(2.0 * self.object_height, 0.001), 0.0
        )
        # Measured constants are read live by the drivers; always (re)stamp to this build's values.
        RigUtils.ensure_custom_prop(self.shadow_plane, "basePlaneSize", self.plane_size, 0.0)
        RigUtils.ensure_custom_prop(self.shadow_plane, "objectHeight", self.object_height, 0.0)
        self.shadow_plane["basePlaneSize"] = self.plane_size
        self.shadow_plane["objectHeight"] = self.object_height
        return self.shadow_plane

    @staticmethod
    def _build_plane(name, size, origin="center"):
        """A single-quad XY plane mesh (4 verts, full 0-1 UV) — no ``bpy.ops`` (so it is
        context-free / preview-safe). ``origin='center'`` centers the verts (stretch mode);
        ``origin='edge'`` puts the origin on the heel edge (local -Y side at y=0) so orbit mode
        can rotate/scale about the ground anchor without a compensatory offset."""
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
    def create_silhouette_texture(self, size=512, axis="auto", recursive=True, **kwargs):
        """Rasterize the targets' world silhouette to an RGBA PNG via
        ``pythontk.ImgUtils.rasterize_silhouette`` and load it as a reusable image datablock.

        ``axis='light'`` (and ``'auto'`` when a source exists) projects the silhouette **as seen
        from the light's horizontal bearing** — the physically correct shape for a ground shadow.
        The points are rebased into the light-view frame (u = the horizontal coordinate
        perpendicular to the bearing, ``u_axis = (dy, -dx)``; v = world up Z) and rasterized as
        that frame's front ('z') view. Explicit letters keep Maya's Y-up semantics (Y/Z swap).

        ``kwargs`` (``uniform_alpha`` / ``falloff_power`` / ``vertical_weight`` / ``blur_amount``)
        pass straight through to the shared rasterizer.
        """
        import numpy as np
        import bpy

        meshes = self._gather_world_meshes(recursive)
        if not meshes:
            raise ValueError("No mesh geometry found on the target(s).")

        axis = str(axis).lower()
        basis = self._light_view_basis() if axis in ("auto", "light") else None
        if basis is not None:
            dx_n, dy_n = basis
            swapped = [
                (
                    np.column_stack(
                        [p[:, 0] * dy_n - p[:, 1] * dx_n, p[:, 2], np.zeros(len(p))]
                    ),
                    t,
                )
                for p, t in meshes
            ]
            raster_axis = "z"
        else:
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

    def _light_view_basis(self):
        """Horizontal unit bearing ``(dx, dy)`` from the light to the targets' center, or None
        when the light is absent or (near) directly overhead."""
        import math

        if self.light is None:
            return None
        lo, hi = self._world_bounds()
        cx, cy = (lo[0] + hi[0]) * 0.5, (lo[1] + hi[1]) * 0.5
        lp = self.light.matrix_world.translation
        dx, dy = cx - lp[0], cy - lp[1]
        d = math.hypot(dx, dy)
        if d < 1e-4:
            return None
        return dx / d, dy / d

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
    # ground height baked as a literal (Maya bakes it the same way). Fragments are compact —
    # no spaces, short float literals — because Blender caps a driver expression at 255 chars.
    def _frags(self):
        G = f"{float(self.ground_height):g}"
        relH = f"max(0.1,Lz-{G})"
        # baseScale (scaleInfluence): Maya gates on (si>0 and hDiff>0.1); branchless here via
        # max(Lz-Cz, 0.1). Identical when si==0 (default) for any pose; differs only with si>0 AND
        # the light at/below the contact, where the clamped result is harmlessly bounded.
        base = "min(max(1+((Lz/max(Lz-Cz,0.1))-1)*si,0.5),3)"
        # Projected ground anchor coefficient: the light ray through the contact scaled to the
        # ground. ==1 while grounded; >1 (shadow slides away from the light) as the target rises;
        # clamped against blowup when the light drops to the contact's height (Maya parity).
        k = f"min(max((Lz-{G})/max(Lz-Cz,0.1),0),10)"
        return G, relH, base, k

    def _plane_props(self, *, influence=False, size=False, height=False, limit=False,
                     opacity=False, fade=False):
        """Convenience: the SINGLE_PROP var specs this driver needs off the shadow plane."""
        specs = []
        if influence:
            specs.append(("si", self.shadow_plane, '["scaleInfluence"]'))
        if size:
            specs.append(("size", self.shadow_plane, '["basePlaneSize"]'))
        if height:
            specs.append(("objH", self.shadow_plane, '["objectHeight"]'))
        if limit:
            specs.append(("lim", self.shadow_plane, '["maxStretch"]'))
        if opacity:
            specs.append(("intensity", self.shadow_plane, '["shadowIntensity"]'))
            specs.append(("power", self.shadow_plane, '["falloffPower"]'))
        if fade:
            specs.append(("fadeH", self.shadow_plane, '["fadeHeight"]'))
        return specs

    def _rise_fade(self, G):
        """Opacity term: fade out as the contact rises off the ground (blob-shadow convention)."""
        return f"min(max(1-max(Cz-{G},0)/max(0.001,fadeH),0),1)"

    def _drivers_stretch(self):
        """Stretch mode: axis-aligned plane, scale X/Y + compensatory translation, no rotation."""
        p, L, C = self.shadow_plane, self.light, self.contact
        G, relH, base, k = self._frags()
        # Stretch ~ objectHeight x (horizontal offset / light height) / plane size.
        sx = f"((1+min(max((objH*(abs(Cx-Lx)/{relH}))/size,0),lim))*{base})"
        sy = f"((1+min(max((objH*(abs(Cy-Ly)/{relH}))/size,0),lim))*{base})"
        # px/py: Maya's (d>0 ? -radius : +radius). eps-guarded sign — exact except at d==0, where
        # tx = px*(1-sx) is 0 anyway unless scaleInfluence pushes sx≠1 (a degenerate, bounded case).
        px = "(-(size*0.5)*(Cx-Lx)/(abs(Cx-Lx)+1e-6))"
        py = "(-(size*0.5)*(Cy-Ly)/(abs(Cy-Ly)+1e-6))"

        # location: the projected ground anchor + compensatory shift (the scale formula is inlined
        # rather than read off the plane's driven scale channel — a transform channel reading its
        # own object's transform is a depsgraph cycle); Z static on the ground.
        props = self._plane_props(influence=True, size=True, height=True, limit=True)
        x_locs = [("Cx", C, "LOC_X"), ("Lx", L, "LOC_X"), ("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")]
        y_locs = [("Cy", C, "LOC_Y"), ("Ly", L, "LOC_Y"), ("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")]
        self._driver(p, "location", 0, f"(Lx+(Cx-Lx)*{k})+{px}*(1-{sx})", x_locs, props)
        self._driver(p, "location", 1, f"(Ly+(Cy-Ly)*{k})+{py}*(1-{sy})", y_locs, props)
        self._driver(p, "scale", 0, sx, x_locs, props)
        self._driver(p, "scale", 1, sy, y_locs, props)
        self._set_static(rotation_z=0.0)
        # Opacity falls off with the larger stretch (reads the plane's own driven scale X/Y —
        # fine from the material tree, a different ID), the light height, and the target's rise.
        self._opacity_driver(
            f"min(max((intensity/max(0.001,pow(max(sclX,sclY),power)))"
            f"*min(max(Lz-Cz,0),1)*{self._rise_fade(G)},0),1)",
            [("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")],
            self._plane_props(opacity=True, fade=True)
            + [("sclX", p, "scale[0]"), ("sclY", p, "scale[1]")],
        )

    def _drivers_orbit(self):
        """Orbit mode: plane rotates about Z to face away from the light; scales along its depth (Y).

        The plane mesh is built with its origin on the heel edge (see ``_build_plane``), so
        rotation and scale pivot the anchor directly — location is just the projected anchor
        pulled back half a plane toward the light, with no scale-dependent offset (which also
        keeps every expression under the 255-char driver cap). World-space result is identical
        to Maya's center-pivot + compensatory offset."""
        p, L, C = self.shadow_plane, self.light, self.contact
        G, relH, base, k = self._frags()
        dist = "sqrt((Cx-Lx)*(Cx-Lx)+(Cy-Ly)*(Cy-Ly))"
        sy = f"((1+min(max((objH*(({dist})/{relH}))/size,0),lim))*{base})"

        common = [("Cx", C, "LOC_X"), ("Lx", L, "LOC_X"), ("Cy", C, "LOC_Y"),
                  ("Ly", L, "LOC_Y"), ("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")]
        # Heel sits half a plane behind the projected anchor, toward the light (Maya parity).
        self._driver(p, "location", 0, f"(Lx+(Cx-Lx)*{k})-(size*0.5)*(Cx-Lx)/(({dist})+1e-6)",
                     common, self._plane_props(size=True))
        self._driver(p, "location", 1, f"(Ly+(Cy-Ly)*{k})-(size*0.5)*(Cy-Ly)/(({dist})+1e-6)",
                     common, self._plane_props(size=True))
        self._driver(p, "scale", 1, sy, common,
                     self._plane_props(influence=True, size=True, height=True, limit=True))
        # rotation about Z (radians — Blender, unlike Maya's degrees): face the head (local +Y)
        # away from the light. R_z(t) sends +Y to (-sin t, cos t) → t = atan2(Lx-Cx, Cy-Ly).
        # (atan2(Cx-Lx, Cy-Ly) — the naive Maya transliteration — mirrors the bearing across Y.)
        self._driver(p, "rotation_euler", 2, "atan2(Lx-Cx,Cy-Ly)",
                     [("Cx", C, "LOC_X"), ("Lx", L, "LOC_X"), ("Cy", C, "LOC_Y"), ("Ly", L, "LOC_Y")], [])
        self._set_static(scale_x=1.0)
        self._opacity_driver(
            f"min(max((intensity/max(0.001,pow(sclY,power)))"
            f"*min(max(Lz-Cz,0),1)*{self._rise_fade(G)},0),1)",
            [("Lz", L, "LOC_Z"), ("Cz", C, "LOC_Z")],
            self._plane_props(opacity=True, fade=True) + [("sclY", p, "scale[1]")],
        )

    def _set_static(self, rotation_z=None, scale_x=None):
        """Set the channels that are constant in a given mode (Z stays on the ground always)."""
        self.shadow_plane.location[2] = self.ground_height + self.GROUND_OFFSET
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
                pool.append(o)
                pool.extend(o.children_recursive)
        else:
            pool = list(bpy.data.objects)
        return [o for o in pool if o.get("basePlaneSize") is not None]

    @classmethod
    def bake_planes(cls, planes=None, start=None, end=None):
        """Bake shadow planes' driven channels to keyframes and remove the drivers so the
        result exports cleanly to FBX (mirror of mayatk's ``bake_planes``).

        Args:
            planes: Shadow plane object(s)/name(s); None bakes every shadow plane in the
                file that still has live drivers.
            start/end: Frame range; defaults to the scene frame range.

        Returns:
            The list of planes that were baked.

        Note: the shader-side opacity fade freezes at its last evaluated value (FBX carries
        no material animation into the engine anyway).
        """
        planes = cls.find_shadow_planes(planes)
        baked = []
        for p in planes:
            if not (p.animation_data and p.animation_data.drivers):
                continue  # already baked / hand-keyed
            cls._bake_plane(p, start, end)
            baked.append(p)
        if baked:
            cls.refresh_export_metadata()
        return baked

    @staticmethod
    def _bake_plane(plane, start=None, end=None):
        """Sample the evaluated (driver-driven) transform per frame, strip the drivers, then
        key the samples — a context-free visual bake (no ``bpy.ops.nla.bake``)."""
        import bpy

        scene = bpy.context.scene
        start = int(scene.frame_start if start is None else start)
        end = int(scene.frame_end if end is None else end)
        cur = scene.frame_current

        samples = []
        for f in range(start, end + 1):
            scene.frame_set(f)
            ev = plane.evaluated_get(bpy.context.evaluated_depsgraph_get())
            samples.append((f, tuple(ev.location), tuple(ev.rotation_euler), tuple(ev.scale)))
        scene.frame_set(cur)

        for path in ("location", "rotation_euler", "scale"):
            for i in range(3):
                RigUtils.remove_driver(plane, path, i)
        # Freeze the shader-side opacity at its last evaluated value (mirror of Maya's bake).
        for mat in (m for m in plane.data.materials if m and m.node_tree):
            node = mat.node_tree.nodes.get("opacity")
            if node is not None:
                val = float(node.outputs[0].default_value)
                RigUtils.remove_driver(
                    mat.node_tree, 'nodes["opacity"].outputs[0].default_value', None
                )
                node.outputs[0].default_value = val

        for f, loc, rot, scl in samples:
            plane.location = loc
            plane.rotation_euler = rot
            plane.scale = scl
            plane.keyframe_insert("location", frame=f)
            plane.keyframe_insert("rotation_euler", frame=f)
            plane.keyframe_insert("scale", frame=f)
        return plane

    # ------------------------------------------------------------------ export metadata
    @staticmethod
    def _plane_texture_path(plane):
        """Full path of the plane's silhouette texture — from the material's image node
        (SSoT; survives retexturing)."""
        import bpy

        for mat in (m for m in plane.data.materials if m and m.node_tree):
            node = mat.node_tree.nodes.get("shadow_tex")
            img = getattr(node, "image", None) if node is not None else None
            if img is not None and img.filepath_raw:
                return bpy.path.abspath(img.filepath_raw)
        return None

    @classmethod
    def refresh_export_metadata(cls):
        """Republish the ``shadow_metadata`` channel on the ``data_export`` carrier
        from the file's shadow planes (mirror of mayatk's producer; blendertk
        convention publishes at authoring time — create/bake — since Blender has no
        before-FBX-export hook). Payload joins Unity-side by GameObject name
        (unitytk's ``ShadowPlaneController.cs``):

        ``{"version": 1, "planes": [{"name", "texture", "intensity"}]}``

        Clears the channel when the file has no shadow planes.

        Returns:
            The published JSON string, or None when cleared.
        """
        import json

        from blendertk.node_utils.data_nodes import DataNodes

        planes = cls.find_shadow_planes()
        if not planes:
            DataNodes.set_export_string(cls.SHADOW_METADATA, "")
            return None
        records = []
        for p in planes:
            tex = cls._plane_texture_path(p)
            records.append(
                {
                    "name": p.name,
                    "texture": os.path.basename(tex) if tex else "",
                    "intensity": round(float(p.get("shadowIntensity", 1.0)), 4),
                }
            )
        payload = json.dumps({"version": 1, "planes": records})
        DataNodes.set_export_string(cls.SHADOW_METADATA, payload)
        return payload

    # ------------------------------------------------------------------ orchestration
    @classmethod
    def create(
        cls, targets, light_pos=(5.0, 5.0, 10.0), texture_res=512, axis="auto",
        source_name="shadow_source", recursive=True, mode="stretch", ground_height=0.0,
    ):
        """Build a projected-shadow rig for ``targets`` (mirror of mayatk's ``ShadowRig.create``)."""
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
        # Publish the engine hand-off record onto the data_export carrier
        # (blendertk producers publish at authoring time — no pre-export hook).
        cls.refresh_export_metadata()
        rig.logger.success(
            f"Shadow rig '{rig._base}' ({mode}) — plane {rig.shadow_plane.name}, "
            f"source {rig.light.name}, texture {rig.texture_path}"
        )
        return rig


class ShadowRigSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Shadow Rig panel.

    Self-contained (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` helper is deferred into
    ``header_init``. Mirrors the Maya panel: a live :class:`blendertk.Preview` rebuilds the rig as
    options change; **Create Shadow** commits; **Bake to Keyframes** bakes for export.
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
        # b001/b002 are auto-wired by the switchboard (method name ==
        # objectName); a raw connect here stacked a second connection →
        # double-fire.

        self._init_tooltips()

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Shadow Rig",
                body="Create a projected-shadow plane rig that exports cleanly for game engines "
                "(Unity, etc.). The plane carries a baked silhouette PNG (rendered as seen from "
                "the light); its transform is driven from the light ray's projected ground "
                "contact, so the shadow slides, stretches, and fades realistically as the target "
                "or light moves.",
                steps=[
                    "Select one or more target objects.",
                    "Enable <b>Preview</b> to build the rig live.",
                    "Tweak <b>Mode</b>, <b>Resolution</b>, <b>Axis</b>, and <b>Include Children</b> "
                    "— the preview refreshes on each change.",
                    "Press <b>Create Shadow</b> to commit, or disable Preview to discard.",
                    "Move the <i>shadow_source</i> empty to warp the shadow.",
                    "Press <b>Bake to Keyframes</b> to bake the drivers to keys over the frame "
                    "range (FBX-ready).",
                    "Export through the <b>Scene Exporter</b> — the rig's "
                    "<i>shadow_metadata</i> rides the data_export carrier automatically.",
                ],
                sections=[
                    ("Modes", [
                        "<b>Stretch</b> — Plane stays axis-aligned; scales + compensatory "
                        "translation warp the shadow. Bake-friendly (default), but the "
                        "silhouette mirrors if the light crosses to the target's opposite side.",
                        "<b>Orbit</b> — Plane rotates about Z to face away from the light. "
                        "Correct for animated/orbiting lights.",
                    ]),
                    ("Plane properties", [
                        "<b>shadowIntensity</b> / <b>falloffPower</b> — overall strength and "
                        "distance falloff.",
                        "<b>maxStretch</b> — clamp on shadow elongation.",
                        "<b>fadeHeight</b> — rise off the ground at which the shadow has fully "
                        "faded.",
                        "<b>scaleInfluence</b> — art-directed extra grow.",
                    ]),
                ],
                notes=[
                    "Blender is Z-up: the plane lies on the XY ground. The Axis combo's labels "
                    "are mirrored verbatim from Maya's Y-up panel — <b>Y (Top)</b> is the "
                    "top-down projection (along Blender's actual up axis, Z) and "
                    "<b>Z (Front)</b> is the other horizontal (Blender Y). <b>Auto</b> projects "
                    "as seen from the light.",
                    "Tweak <i>shadowIntensity</i> / <i>falloffPower</i> / <i>maxStretch</i> / "
                    "<i>fadeHeight</i> / <i>scaleInfluence</i> live on the plane's Custom "
                    "Properties.",
                    "Unity plug-and-play: deploy unitytk's C# templates once "
                    "(<i>unitytk.deploy_templates</i>), export via the Scene Exporter, and "
                    "copy the silhouette PNG into Assets — the import sets up the "
                    "unlit-transparent material and shadow flags automatically. Other "
                    "engines: assign an unlit/transparent shader with the PNG by hand. The "
                    "opacity fade is shader-side (viewport only) — FBX carries the plane's "
                    "transform animation, not material animation.",
                ],
            )
        )

    def _init_tooltips(self):
        """Set the polished (uitk ``fmt``) tooltips for every option and action."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        ui = self.ui

        ui.cmb_mode.setToolTip(
            fmt(
                title="Rig Mode",
                body="How the shadow plane reacts to the light's position.",
                sections=[
                    (
                        "Stretch",
                        [
                            "Plane stays axis-aligned; scale and compensatory "
                            "translation warp the shadow.",
                            "Bake-friendly.",
                            "Silhouette mirrors if the light crosses to the "
                            "target's opposite side.",
                        ],
                    ),
                    (
                        "Orbit",
                        [
                            "Plane rotates around the target to face away from "
                            "the light.",
                            "Correct for animated / orbiting lights.",
                        ],
                    ),
                ],
            )
        )
        ui.chk_combine.setToolTip(
            fmt(
                title="Include Children",
                body="Include the selected objects' descendant meshes in the "
                "baked silhouette.",
                notes=[
                    "The selection always shares a single combined shadow "
                    "plane.",
                    "Off — only the selected meshes themselves are "
                    "rasterized.",
                ],
            )
        )
        ui.txt_source.setToolTip(
            fmt(
                title="Source Name",
                body="Name for the shadow-source empty that anchors the "
                "projection.",
                notes=[
                    "Reuse a name to share one source; use distinct names for "
                    "separate shadow sources.",
                ],
            )
        )
        ui.s000.setToolTip(
            fmt(
                title="Texture Resolution",
                body="Pixel resolution of the baked silhouette PNG carried by "
                "the shadow plane.",
                notes=[
                    "Higher = crisper shadow edge, but a larger texture on disk.",
                ],
            )
        )
        ui.cmb000.setToolTip(
            fmt(
                title="Projection Axis",
                body="Viewing axis the silhouette is rendered along.",
                rows=[
                    ("Auto", "projects as seen from the light"),
                    ("X / Y / Z", "force side / top / front projection"),
                ],
                notes=[
                    "Labels mirror Maya's Y-up panel — <b>Y (Top)</b> is the "
                    "top-down projection along Blender's actual up axis (Z).",
                ],
            )
        )
        ui.chk_preview.setToolTip(
            fmt(
                title="Preview",
                body="Builds the shadow rig live so you can judge it before "
                "committing.",
                notes=[
                    "Tweaking any option refreshes the preview.",
                    "<b>Create Shadow</b> commits it; disabling Preview "
                    "discards it.",
                ],
            )
        )
        ui.b000.setToolTip(
            fmt(
                title="Create Shadow",
                body="Commits the previewed shadow rig for the selected "
                "target(s).",
                steps=[
                    "Select one or more target objects.",
                    "Enable <b>Preview</b> and dial in the options.",
                    "Press <b>Create Shadow</b>.",
                ],
                notes=[
                    "Only commits an active preview — enable <b>Preview</b> "
                    "first, or this does nothing.",
                ],
            )
        )
        ui.b001.setToolTip(
            fmt(
                title="Reset to Defaults",
                body="Restores every option on this panel to its default value.",
            )
        )
        ui.b002.setToolTip(
            fmt(
                title="Bake to Keyframes",
                body="Bakes the shadow plane's driven motion to keyframes over "
                "the scene frame range and removes the live rig — leaving an "
                "FBX-ready plane.",
                notes=[
                    "Applies to selected shadow planes, or all planes if none "
                    "are selected.",
                    "Bake before exporting to Unity / a game engine.",
                ],
            )
        )

    def b001(self):
        """Reset to Defaults — restore all UI widgets to their default values."""
        self.ui.state.reset_all()

    def b002(self):
        """Bake to Keyframes: bake selected (or all) shadow planes' drivers to keys over the
        scene frame range and remove the live rig."""
        import blendertk as btk

        sel = btk.selected_objects() or None
        planes = ShadowRig.find_shadow_planes(sel)
        if sel and not planes:
            # A non-empty selection with no shadow planes must NOT silently
            # fall back to baking (destructively de-rigging) every plane in
            # the file — that's only the documented behavior for an empty
            # selection.
            self.sb.message_box(
                "Selection contains no shadow planes. Select the plane(s) "
                "to bake, or clear the selection to bake all."
            )
            return
        baked = ShadowRig.bake_planes(planes)
        if baked:
            self.sb.message_box(f"Baked {len(baked)} shadow plane(s) to keyframes.")
        else:
            self.sb.message_box("No shadow planes with live drivers found.")

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
