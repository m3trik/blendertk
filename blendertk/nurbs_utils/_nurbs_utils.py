# !/usr/bin/python
# coding=utf-8
"""Shared curve helpers — Blender mirror of mayatk's ``nurbs_utils.NurbsUtils`` namespace.

Maya's ``NurbsUtils`` is a thick layer over ``cmds`` NURBS commands (loft / planarSrf / nurbsToPoly
/ extrude / MASH). Blender ships those capabilities as object properties (curve ``bevel_depth`` /
``fill_mode``) + one evaluated-mesh bake, so this holds only the **two primitives the curve tools
share**: build a curve object from a point list, and bake a curve (its evaluated bevel / 2D-fill)
to a mesh. Tool-specific behaviour (image-contour tracing, tube bevel/RDP) lives in each tool module.

``import bpy`` is deferred into the call bodies (no import side effects).
"""

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils


class NurbsUtils(ptk.LoggingMixin):
    """Shared Blender curve primitives (mirror of mayatk's ``NurbsUtils``)."""

    @staticmethod
    def add_spline(curve, points, cyclic=False, kind="POLY"):
        """Append a spline of ``points`` (each an ``(x, y, z)``) to an existing curve.

        Parameters:
            curve (bpy.types.Object | bpy.types.Curve): The curve object or its data.
            points (list): ``(x, y, z)`` control points.
            cyclic (bool): Close the spline (``use_cyclic_u``).
            kind (str): ``"POLY"`` (linear — Blender's analogue of Maya ``degree=1``) or ``"NURBS"``.

        Returns:
            bpy.types.Spline: The new spline.
        """
        import bpy

        cu = curve.data if isinstance(curve, bpy.types.Object) else curve
        pts = [tuple(p) for p in points]
        if not pts:
            raise ValueError("add_spline requires at least one point.")
        spline = cu.splines.new(kind)
        spline.points.add(len(pts) - 1)  # a fresh spline starts with one point
        for sp_pt, p in zip(spline.points, pts):
            sp_pt.co = (p[0], p[1], p[2], 1.0)
        if kind == "NURBS":
            spline.order_u = min(4, len(pts))  # ≤ point count
            spline.use_endpoint_u = True  # pass through the endpoints
        spline.use_cyclic_u = bool(cyclic)
        return spline

    @classmethod
    def create_curve(
        cls,
        points,
        name="curve",
        cyclic=False,
        kind="POLY",
        dimensions="3D",
        link=True,
        collection=None,
    ):
        """Build a curve object from a point list — mirror of mayatk's ``cmds.curve`` usage.

        Parameters:
            points (list): ``(x, y, z)`` control points for the (single) spline.
            name (str): Object/data name.
            cyclic (bool): Close the spline.
            kind (str): ``"POLY"`` or ``"NURBS"``.
            dimensions (str): ``"3D"`` or ``"2D"`` (2D enables planar fill).
            link (bool): Link the object into a collection.
            collection (bpy.types.Collection): Target collection (else the active one).

        Returns:
            bpy.types.Object: The curve object.
        """
        import bpy

        cu = bpy.data.curves.new(name, "CURVE")
        cu.dimensions = dimensions
        cls.add_spline(cu, points, cyclic=cyclic, kind=kind)
        obj = bpy.data.objects.new(name, cu)
        if link:
            (collection or bpy.context.collection).objects.link(obj)
        return obj

    @staticmethod
    def duplicate_curve(curve_obj, name=None, link=True):
        """A curve-data duplicate of ``curve_obj``, linked into the same collection(s) — the
        curve-domain analogue of ``bpy.ops.object.duplicate`` usable without a viewport/selection
        context (mirrors mayatk's ``cmds.duplicate``-driven curve copies).

        Parameters:
            curve_obj (bpy.types.Object): Source curve object.
            name (str): Name for the duplicate (defaults to the source's name).
            link (bool): Link the duplicate into the source's collection(s).

        Returns:
            bpy.types.Object: The duplicate curve object.
        """
        dup = curve_obj.copy()
        dup.data = curve_obj.data.copy()
        if name:
            dup.name = name
        if link:
            for c in curve_obj.users_collection or []:
                c.objects.link(dup)
        return dup

    @staticmethod
    def create_plane(
        width=1.0,
        height=1.0,
        location=(0.0, 0.0, 0.0),
        name="plane",
        link=True,
        collection=None,
    ):
        """Build a simple rectangular mesh plane centered at ``location`` — Blender analogue of
        Maya's ``nurbsPlane`` (used e.g. as a projection/backing surface under traced curves).

        Parameters:
            width (float): Size along X.
            height (float): Size along Y.
            location (tuple): World-space center ``(x, y, z)``.
            name (str): Object/data name.
            link (bool): Link the object into a collection.
            collection (bpy.types.Collection): Target collection (else the active one).

        Returns:
            bpy.types.Object: The plane mesh object.
        """
        import bpy

        hw, hh = width / 2.0, height / 2.0
        verts = [(-hw, -hh, 0.0), (hw, -hh, 0.0), (hw, hh, 0.0), (-hw, hh, 0.0)]
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], [(0, 1, 2, 3)])
        me.update()
        obj = bpy.data.objects.new(name, me)
        obj.location = location
        if link:
            (collection or bpy.context.collection).objects.link(obj)
        return obj

    @staticmethod
    def curve_to_mesh(
        curve_obj, name=None, link=True, keep_curve=False, collection=None
    ):
        """Bake a curve object's **evaluated** geometry (its bevel sweep / 2D fill) to a new mesh
        object — Blender's analogue of Maya's ``nurbsToPoly``.

        Parameters:
            curve_obj (bpy.types.Object): The (beveled or 2D-fill) curve object.
            name (str): Mesh object name (defaults to the curve's name).
            link (bool): Link the mesh object into a collection.
            keep_curve (bool): Leave the source curve in the scene (else remove it + purge its
                orphaned curve datablock, mirroring the ImageToPlane orphan purge).
            collection (bpy.types.Collection): Target collection (else the curve's own, else active).

        Returns:
            bpy.types.Object: The new mesh object.
        """
        import bpy

        name = name or curve_obj.name
        target = collection or (
            curve_obj.users_collection[0]
            if curve_obj.users_collection
            else bpy.context.collection
        )
        from blendertk.core_utils._core_utils import CoreUtils

        deps = CoreUtils._evaluated_depsgraph()  # the window layer's
        me = bpy.data.meshes.new_from_object(curve_obj.evaluated_get(deps))
        me.name = name
        mesh_obj = bpy.data.objects.new(name, me)
        if link:
            target.objects.link(mesh_obj)
        if not keep_curve:
            cu_data = curve_obj.data
            bpy.data.objects.remove(curve_obj, do_unlink=True)
            if cu_data.users == 0:  # purge the now-orphaned curve datablock
                bpy.data.curves.remove(cu_data)
        return mesh_obj

    # ------------------------------------------------------------------ CV deformation family
    # Rolled equivalents of Maya's Modify/Edit Curves commands (BendCurves / CurlCurves /
    # ScaleCurvature / StraightenCurves / RebuildCurve / ExtendCurve) — pure control-point math
    # on POLY/NURBS splines (Bézier splines are skipped: their handle model needs its own
    # authoring pass). A SIMPLE_DEFORM-modifier route was probed 2026-07-11 and refuted
    # (silently no-ops unless deform_axis ⊥ the curve plane); CV math has no such trap.

    @staticmethod
    def _curve_splines(objects):
        """Yield ``(spline, points)`` for every editable POLY/NURBS spline in the curve
        objects — ``points`` as a list of ``mathutils.Vector`` (xyz, local space)."""
        from mathutils import Vector

        for o in ptk.make_iterable(objects):
            if o is None or getattr(o, "type", None) != "CURVE":
                continue
            for sp in o.data.splines:
                if sp.type not in ("POLY", "NURBS") or len(sp.points) < 2:
                    continue
                yield sp, [Vector(p.co[:3]) for p in sp.points]

    @staticmethod
    def _arc_params(pts):
        """Cumulative chord-length parameters ``t`` in [0, 1] per point (0 at the start)."""
        acc, total = [0.0], 0.0
        for a, b in zip(pts, pts[1:]):
            total += (b - a).length
            acc.append(total)
        return [a / total if total > 0 else 0.0 for a in acc]

    @staticmethod
    def _write_pts(spline, pts):
        for sp_pt, p in zip(spline.points, pts):
            sp_pt.co = (p.x, p.y, p.z, sp_pt.co[3])

    @classmethod
    def straighten_curve(cls, objects, straightness=1.0, preserve_length=True):
        """Interpolate control points toward the start→end line (Maya ``StraightenCurves``:
        ``straightness`` 0..1). ``preserve_length`` distributes the line targets by cumulative
        arc length (the curve keeps its length as it straightens) instead of by chord fraction.
        """
        for sp, pts in cls._curve_splines(objects):
            start, end = pts[0], pts[-1]
            direction = end - start
            if direction.length < 1e-9:
                continue
            t = cls._arc_params(pts)
            if preserve_length:
                arc_total = sum((b - a).length for a, b in zip(pts, pts[1:]))
                targets = [
                    start + direction.normalized() * (ti * arc_total) for ti in t
                ]
            else:
                targets = [start + direction * ti for ti in t]
            cls._write_pts(sp, [p.lerp(q, straightness) for p, q in zip(pts, targets)])
        return objects

    @classmethod
    def bend_curve(cls, objects, angle=45.0, axis="z"):
        """Bend each curve into a circular arc of ``angle`` degrees around the local
        ``axis`` (Maya ``BendCurves``' magnitude, as an explicit angle). Arc-length exact:
        each point's chord position maps onto a circle whose arc runs the same distance
        (radius = length / angle), and its deviation from the chord rotates with the local
        tangent — a straight curve becomes a true arc of unchanged length."""
        import math

        from mathutils import Matrix, Vector

        total_angle = math.radians(angle)
        if abs(total_angle) < 1e-9:
            return objects
        axis_v = Vector(
            {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[str(axis).lower()]
        )
        for sp, pts in cls._curve_splines(objects):
            start, end = pts[0], pts[-1]
            direction = end - start
            if direction.length < 1e-9:
                continue
            d = direction.normalized()
            n = axis_v.cross(d)
            if n.length < 1e-9:  # axis parallel to the curve — nothing to bend around
                continue
            n.normalize()
            t = cls._arc_params(pts)
            length = direction.length
            radius = length / total_angle
            out = []
            for p, ti in zip(pts, t):
                theta = total_angle * ti
                base = (
                    start
                    + d * (radius * math.sin(theta))
                    + n * (radius * (1.0 - math.cos(theta)))
                )
                deviation = p - (start + d * (ti * length))
                out.append(base + (Matrix.Rotation(theta, 3, axis_v) @ deviation))
            cls._write_pts(sp, out)
        return objects

    @classmethod
    def curl_curve(cls, objects, angle=270.0, frequency=1.0, axis="z"):
        """Curl each curve — like :func:`bend_curve` but with an accelerating rotation
        (``angle * frequency * t²``), spiraling the far end in on itself (Maya
        ``CurlCurves``' amount/frequency)."""
        import math

        from mathutils import Matrix, Vector

        axis_v = Vector(
            {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[str(axis).lower()]
        )
        for sp, pts in cls._curve_splines(objects):
            t = cls._arc_params(pts)
            start = pts[0]
            out = [
                start
                + (
                    Matrix.Rotation(
                        math.radians(angle) * frequency * ti * ti, 3, axis_v
                    )
                    @ (p - start)
                )
                for p, ti in zip(pts, t)
            ]
            cls._write_pts(sp, out)
        return objects

    @classmethod
    def scale_curvature(cls, objects, factor=1.5):
        """Scale each control point's deviation from the start→end chord by ``factor``
        (Maya ``ScaleCurvature``): >1 exaggerates the curve's bow, <1 flattens it
        (0 collapses onto the chord)."""
        for sp, pts in cls._curve_splines(objects):
            start, end = pts[0], pts[-1]
            t = cls._arc_params(pts)
            chord = [start + (end - start) * ti for ti in t]
            cls._write_pts(sp, [c + (p - c) * factor for p, c in zip(pts, chord)])
        return objects

    @classmethod
    def rebuild_curve(cls, objects, spans=8):
        """Redistribute each spline's control points uniformly by arc length with a new
        count of ``spans + 1`` (Maya ``RebuildCurve``'s uniform rebuild, at the
        control-polygon level — the spline's own NURBS basis re-smooths the result; exact
        for POLY splines). Spline points can't be removed in place, so each spline is
        replaced by a fresh one carrying its type/settings."""

        def _resample(pts, n):
            arc = [0.0]
            for a, b in zip(pts, pts[1:]):
                arc.append(arc[-1] + (b - a).length)
            total = arc[-1]
            if total <= 0:
                return None
            out, seg = [], 0
            for i in range(n):
                target = total * i / (n - 1)
                while seg < len(arc) - 2 and arc[seg + 1] < target:
                    seg += 1
                span_len = arc[seg + 1] - arc[seg]
                f = (target - arc[seg]) / span_len if span_len > 0 else 0.0
                out.append(pts[seg].lerp(pts[seg + 1], f))
            return out

        from mathutils import Vector

        n = max(2, int(spans) + 1)
        for o in ptk.make_iterable(objects):
            if o is None or getattr(o, "type", None) != "CURVE":
                continue
            cu = o.data
            # Two-phase by INDEX: removing a spline can invalidate other held spline
            # references, so gather every target's rebuild data first, then remove+recreate
            # with a fresh lookup per index (descending, so earlier indices stay valid).
            # Rebuilt splines re-append at the collection's end — spline order is cosmetic.
            jobs = []
            for i, sp in enumerate(cu.splines):
                if sp.type not in ("POLY", "NURBS") or len(sp.points) < 2:
                    continue
                new_pts = _resample([Vector(p.co[:3]) for p in sp.points], n)
                if new_pts is not None:
                    jobs.append((i, sp.type, sp.use_cyclic_u, sp.order_u, new_pts))
            for i, kind, cyclic, order, new_pts in sorted(jobs, reverse=True):
                cu.splines.remove(cu.splines[i])
                fresh = cu.splines.new(kind)
                fresh.points.add(n - 1)
                for sp_pt, p in zip(fresh.points, new_pts):
                    sp_pt.co = (p.x, p.y, p.z, 1.0)
                if kind == "NURBS":
                    fresh.order_u = min(order, n)
                    fresh.use_endpoint_u = True
                fresh.use_cyclic_u = cyclic
        return objects

    @classmethod
    def extend_curve(cls, objects, distance=1.0, from_start=False):
        """Extend each spline by one control point continuing its end tangent for
        ``distance`` (Maya ``ExtendCurve``'s linear extend; ``from_start`` extends the
        other end)."""
        for sp, pts in cls._curve_splines(objects):
            if from_start:
                tangent = (pts[0] - pts[1]).normalized()
                new_pt = pts[0] + tangent * distance
                out = [new_pt] + pts
            else:
                tangent = (pts[-1] - pts[-2]).normalized()
                new_pt = pts[-1] + tangent * distance
                out = pts + [new_pt]
            sp.points.add(1)
            cls._write_pts(sp, out)
        return objects


    # ------------------------------------------------------------------ curve actions
    #: The curve actions tentacle's Nurbs list (and its Extrude button) offers ->
    #: ``(method, options)``: how Blender performs each. Twin of mayatk's
    #: ``NurbsUtils.CURVE_ACTIONS`` (Maya's runtime commands), whose keys these
    #: are. Maya's parametric-NURBS actions -- ``lock_length``,
    #: ``unlock_length``, ``insert_isoparm``, ``insert_knot``,
    #: ``extend_options``, ``extend_on_surface`` -- have no Blender counterpart (no
    #: arc-length lock, isoparm, exposed knot vector, or surface-constrained
    #: curve) and are absent.
    CURVE_ACTIONS = {
        "project": ("project_curves_on_mesh", {}),
        "extract": ("curves_from_mesh", {}),
        "duplicate": ("duplicate_curves", {}),
        "bend": ("bend_curve", {"angle": 45.0}),
        "curl": ("curl_curve", {"angle": 270.0}),
        "scale_curvature": ("scale_curvature", {"factor": 1.5}),
        "smooth": ("smooth_curves", {}),
        "straighten": ("straighten_curve", {}),
        "planar": ("fill_planar_curves", {}),
        "attach": ("attach_curves", {}),
        "detach": ("separate_curve_points", {}),
        "cut": ("split_curve_points", {}),
        "open_close": ("toggle_curves_cyclic", {}),
        "rebuild": ("rebuild_curve", {"spans": 8}),
        "reverse": ("reverse_curves", {}),
        "extend": ("extend_curve", {"distance": 1.0}),
        "extrude": ("extrude_curves", {}),
    }

    #: The actions of the CV-deformation family: poly and NURBS splines only.
    _SPLINE_ACTIONS = frozenset(
        {"bend", "curl", "scale_curvature", "straighten", "rebuild", "extend"}
    )

    @classmethod
    @CoreUtils.undoable
    def curve_action(cls, action, curves=None):
        """Run the curve action *action* (a :attr:`CURVE_ACTIONS` key) on *curves*.

        Twin of mayatk's ``NurbsUtils.curve_action``: one call per action for a
        panel shared by both hosts. One undo step.

        Parameters:
            action (str): A :attr:`CURVE_ACTIONS` key (``"reverse"``, ``"planar"``).
            curves (list/None): The objects to act on; None takes the selection.

        Returns:
            (list): The objects the action made or changed.

        Raises:
            ValueError: *action* is not one Blender serves, or the selection
                cannot serve it (the message says what to select).
            RuntimeError: Blender refused it (an object it cannot edit).
        """
        try:
            name, options = cls.CURVE_ACTIONS[action]
        except KeyError:
            raise ValueError(
                f"Blender has no {action!r} curve action: expected one of "
                f"{', '.join(sorted(cls.CURVE_ACTIONS))}."
            ) from None
        if action in cls._SPLINE_ACTIONS:
            curves = cls._spline_curves(curves)
        return getattr(cls, name)(curves, **options)

    @staticmethod
    def _typed(objects, kind):
        """The objects of *kind* (``"CURVE"``, ``"MESH"``) in *objects*, the
        selection when None."""
        pool = (
            CoreUtils.selected_objects()
            if objects is None
            else ptk.make_iterable(objects)
        )
        return [o for o in pool if o is not None and getattr(o, "type", None) == kind]

    @classmethod
    def _curves(cls, objects):
        """The curves of *objects* (the selection when None); ValueError when none."""
        curves = cls._typed(objects, "CURVE")
        if not curves:
            raise ValueError("Select one or more curves.")
        return curves

    @classmethod
    def _spline_curves(cls, objects):
        """The curves the CV-deformation family edits: those with a poly or NURBS
        spline (a Bezier handle model needs its own authoring pass)."""
        eligible = [
            c
            for c in cls._curves(objects)
            if any(sp.type in ("POLY", "NURBS") for sp in c.data.splines)
        ]
        if not eligible:
            raise ValueError(
                "Poly and NURBS splines only: Bezier curves are not supported."
            )
        return eligible

    @staticmethod
    def _curve_edit_op(curves, op):
        """Run the curve-domain Edit-Mode operator ``bpy.ops.curve.<op>`` ONCE across
        every control point of *curves*. Maya's curve commands act on the object;
        Blender's only poll in Edit Mode, and since 2.8x Edit Mode spans every
        selected curve with a multi-object operator, so a per-curve loop would run
        it N times on each (Reverse on two curves = no visible change).
        ``CoreUtils.edit_mode`` brackets it and restores the selection and mode."""
        import bpy

        with CoreUtils.edit_mode(curves):
            bpy.ops.curve.select_all(action="SELECT")
            getattr(bpy.ops.curve, op)()
        return curves

    @classmethod
    @CoreUtils.window_context_override()
    def project_curves_on_mesh(cls, objects=None):
        """Project: conform the curves of *objects* (the selection when None) onto
        its first mesh -- a Shrinkwrap (project) modifier, applied, so the
        projected positions land in the curve's own points (Maya's
        ``ProjectCurveOnMesh``). Returns the curves.

        Raises:
            ValueError: No curve, or no mesh to project onto.
            RuntimeError: Blender refused to apply the modifier (it is removed).
        """
        import bpy

        curves = cls._typed(objects, "CURVE")
        meshes = cls._typed(objects, "MESH")
        if not curves or not meshes:
            raise ValueError(
                "Select one or more curves and the mesh to project them onto."
            )
        target = meshes[0]
        for curve in curves:
            mod = curve.modifiers.new(name="Shrinkwrap", type="SHRINKWRAP")
            mod.target = target
            mod.wrap_method = "PROJECT"
            mod.use_project_z = True
            mod.use_negative_direction = True
            mod.use_positive_direction = True
            bpy.context.view_layer.objects.active = curve
            try:
                bpy.ops.object.modifier_apply(modifier=mod.name)
            except RuntimeError:
                curve.modifiers.remove(mod)  # never leave a failed Shrinkwrap stacked
                raise
        return curves

    @classmethod
    @CoreUtils.window_context_override()
    def curves_from_mesh(cls, objects=None):
        """Extract: a curve built from each mesh's edges (Maya's
        ``CreateCurveFromPoly``). The meshes are duplicated first --
        ``object.convert`` replaces data in place -- so the source stays intact.
        Returns the new curves, selected.

        Raises:
            ValueError: No mesh in *objects* (the selection when None).
        """
        import bpy

        meshes = cls._typed(objects, "MESH")
        if not meshes:
            raise ValueError("Select one or more meshes to extract curves from.")
        dups = []
        for mesh in meshes:
            dup = mesh.copy()
            dup.data = mesh.data.copy()
            for collection in mesh.users_collection or [bpy.context.scene.collection]:
                collection.objects.link(dup)
            dups.append(dup)
        CoreUtils.select(dups)
        bpy.ops.object.convert(target="CURVE")
        return dups

    @classmethod
    def duplicate_curves(cls, objects=None):
        """Duplicate: a copy of each curve (:meth:`duplicate_curve`), the copies
        selected (Maya's ``DuplicateCurve``). Returns the copies."""
        dups = [cls.duplicate_curve(c) for c in cls._curves(objects)]
        CoreUtils.select(dups)
        return dups

    @classmethod
    def smooth_curves(cls, objects=None):
        """Smooth: ``curve.smooth`` over every control point of the curves."""
        return cls._curve_edit_op(cls._curves(objects), "smooth")

    @classmethod
    def toggle_curves_cyclic(cls, objects=None):
        """Open/Close: ``curve.cyclic_toggle`` over every spline of the curves."""
        return cls._curve_edit_op(cls._curves(objects), "cyclic_toggle")

    @classmethod
    def reverse_curves(cls, objects=None):
        """Reverse: ``curve.switch_direction`` over every spline of the curves."""
        return cls._curve_edit_op(cls._curves(objects), "switch_direction")

    @staticmethod
    def _curve_points_world(curve):
        """Every control point of *curve* in world space, Bezier handles included
        (coplanar anchors can still bow out of plane through their handles)."""
        mw = curve.matrix_world
        pts = []
        for spline in curve.data.splines:
            if spline.type == "BEZIER":
                for p in spline.bezier_points:
                    pts.extend((mw @ p.co, mw @ p.handle_left, mw @ p.handle_right))
            else:
                pts.extend(mw @ p.co.to_3d() for p in spline.points)
        return pts

    @staticmethod
    def _coplanar(points, tol=1e-4):
        """True when *points* (world-space Vectors) lie on one plane within *tol*:
        anchored at the first point, the normal from the best-conditioned pair;
        fewer than four points, or collinear ones, are trivially planar."""
        if len(points) < 4:
            return True
        origin = points[0]
        edge = next((p - origin for p in points[1:] if (p - origin).length > tol), None)
        if edge is None:
            return True  # every point coincident
        normal = max((edge.cross(p - origin) for p in points[1:]), key=lambda v: v.length)
        if normal.length <= tol:
            return True  # collinear: degenerate but planar
        normal.normalize()
        return all(abs((p - origin).dot(normal)) <= tol for p in points)

    @classmethod
    def fill_planar_curves(cls, objects=None):
        """Planar: fill each closed, coplanar curve (``dimensions = "2D"``,
        ``fill_mode = "BOTH"``; Blender has no fill operator). A 2D curve is
        projected onto its local XY plane, so a non-planar curve is skipped rather
        than silently flattened, and an open one -- a fill shows on closed splines
        only -- is skipped too (Maya's ``Planar`` refuses both). Skips are logged.

        Returns:
            (list): The curves filled.

        Raises:
            ValueError: No curve could be filled (the message says why).
        """
        filled, notes = [], []
        for curve in cls._curves(objects):
            if not cls._coplanar(cls._curve_points_world(curve)):
                notes.append(f"{curve.name} is not planar")
                continue
            if not any(s.use_cyclic_u for s in curve.data.splines):
                notes.append(f"{curve.name} is open (Open/Close closes it)")
                continue
            curve.data.dimensions = "2D"
            curve.data.fill_mode = "BOTH"
            filled.append(curve)
        if notes:
            reason = "Planar fills closed, coplanar curves: " + "; ".join(notes) + "."
            if not filled:
                raise ValueError(reason)
            cls.logger.warning(reason)
        return filled

    @classmethod
    @CoreUtils.window_context_override()
    def attach_curves(cls, objects=None):
        """Attach: join the curves into the first of them (``object.join``; Maya's
        ``AttachCurve``). Returns ``[the joined curve]``.

        Raises:
            ValueError: Fewer than two curves.
            RuntimeError: Blender refused the join.
        """
        import bpy

        curves = cls._typed(objects, "CURVE")
        if len(curves) < 2:
            raise ValueError("Select two or more curves to attach.")
        CoreUtils.select(curves)  # the first is active: the one the others join
        bpy.ops.object.join()
        return [curves[0]]

    @staticmethod
    def _edit_curve(curve, action):
        """*curve* (the active object when None) when it is a curve in Edit Mode."""
        obj = curve if curve is not None else CoreUtils.active_object()
        if isinstance(obj, (list, tuple)):
            obj = obj[0] if obj else None
        if obj is None or obj.type != "CURVE" or obj.mode != "EDIT":
            raise ValueError(
                f"{action} needs the curve in Edit Mode with its point(s) selected."
            )
        return obj

    @classmethod
    @CoreUtils.window_context_override()
    def separate_curve_points(cls, curve=None):
        """Detach: separate the selected points of the curve in Edit Mode (*curve*,
        the active object when None) into a new object (``curve.separate``).

        Raises:
            ValueError: The curve is not in Edit Mode.
            RuntimeError: Blender refused it (no points selected).
        """
        import bpy

        obj = cls._edit_curve(curve, "Detach")
        bpy.ops.curve.separate()
        return [obj]

    @classmethod
    @CoreUtils.window_context_override()
    def split_curve_points(cls, curve=None):
        """Cut: split the curve in Edit Mode (*curve*, the active object when None)
        at its selected points (``curve.split``).

        Raises:
            ValueError: The curve is not in Edit Mode.
            RuntimeError: Blender refused it (no points selected).
        """
        import bpy

        obj = cls._edit_curve(curve, "Cut")
        bpy.ops.curve.split()
        return [obj]

    @classmethod
    def extrude_curves(cls, objects=None):
        """Extrude: a surface from the curves, in Maya's two styles. Two or more:
        the active curve (else the last) is the path and the first other curve the
        profile swept along it (``bevel_object``). One: extruded along its local Z
        into a ribbon (``extrude``). Returns ``[the curve given the surface]``."""
        curves = cls._curves(objects)
        if len(curves) >= 2:
            active = CoreUtils.active_object()
            path = active if active in curves else curves[-1]
            profile = next(c for c in curves if c != path)
            path.data.bevel_mode = "OBJECT"
            path.data.bevel_object = profile
            path.data.use_fill_caps = True
            return [path]
        curve = curves[0]
        curve.data.extrude = curve.data.extrude or 0.5
        curve.data.use_fill_caps = True
        return [curve]

    @staticmethod
    @CoreUtils.selected
    @CoreUtils.undoable
    def revolve(
        curves=None,
        axis="y",
        start_sweep=0.0,
        end_sweep=360.0,
        sections=8,
        degree=3,
        range_=False,
        polygon=True,
        use_tolerance=False,
        tolerance=0.001,
        auto_correct_normal=False,
    ):
        """Revolve each profile (a curve or mesh object) into a surface: a Screw
        modifier sweeping ``end_sweep - start_sweep`` degrees about *axis* in
        *sections* steps -- mayatk's ``NurbsUtils.revolve`` (name and parameters).
        An object already carrying a Screw has it re-set, not a second stacked.
        A Screw mesh has no degree, curve range, polygon toggle or build
        tolerance: those parameters are Maya's, accepted for the shared signature.
        One undo step.

        Returns:
            (list): The objects revolved; ``[]`` when *curves* (the selection
                when None) holds no curve or mesh.

        Raises:
            ValueError: An unknown *axis*.
        """
        import math

        name = str(axis).upper()
        if name not in ("X", "Y", "Z"):
            raise ValueError(f"axis must be 'x', 'y' or 'z', not {axis!r}.")
        targets = [
            o
            for o in ptk.make_iterable(curves)
            if getattr(o, "type", None) in ("MESH", "CURVE")
        ]
        angle = math.radians(end_sweep - start_sweep)
        for obj in targets:
            mod = next((m for m in obj.modifiers if m.type == "SCREW"), None)
            if mod is None:
                mod = obj.modifiers.new(name="Screw", type="SCREW")
            mod.axis = name
            mod.angle = angle
            mod.steps = mod.render_steps = int(sections)  # RNA minimum is 2
            mod.use_normal_calculate = bool(auto_correct_normal)
        return targets

    @staticmethod
    @CoreUtils.undoable
    def loft(
        objects=None,
        uniform=True,
        close=False,
        degree=3,
        autoReverse=False,
        sectionSpans=1,
        range_=False,
        polygon=True,
        reverseSurfaceNormals=True,
        angle_loft_between_two_curves=False,
        angleLoftSpans=6,
    ):
        """Loft a mesh surface across the profile curves / mesh loops of *objects*
        (the selection when None, in its order) -- mayatk's ``NurbsUtils.loft``
        (name and parameters). Blender ships no NURBS loft, so each profile's
        points are resampled to a common count and consecutive profiles bridged
        with quads; *close* bridges the last back to the first, *sectionSpans*
        interpolates rings between profiles, *reverseSurfaceNormals* flips the
        winding. The parametric-surface parameters (uniform, degree, autoReverse,
        range_, polygon, the angle loft) are Maya's, accepted for the shared
        signature. One undo step.

        Returns:
            (list): ``[the lofted mesh]``; ``[]`` with fewer than two usable
                profiles.
        """
        from blendertk.edit_utils._edit_utils import EditUtils

        lofted = EditUtils._loft_profiles(
            objects,
            close=close,
            reverse_normals=reverseSurfaceNormals,
            section_spans=sectionSpans,
        )
        return [lofted] if lofted is not None else []

# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
