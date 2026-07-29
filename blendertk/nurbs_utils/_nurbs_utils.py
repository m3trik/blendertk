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
    def create_curve(cls, points, name="curve", cyclic=False, kind="POLY",
                     dimensions="3D", link=True, collection=None):
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
    def create_plane(width=1.0, height=1.0, location=(0.0, 0.0, 0.0), name="plane",
                     link=True, collection=None):
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
    def curve_to_mesh(curve_obj, name=None, link=True, keep_curve=False, collection=None):
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
        deps = bpy.context.evaluated_depsgraph_get()
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
            cls._write_pts(
                sp, [p.lerp(q, straightness) for p, q in zip(pts, targets)]
            )
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
            cls._write_pts(
                sp, [c + (p - c) * factor for p, c in zip(pts, chord)]
            )
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


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
