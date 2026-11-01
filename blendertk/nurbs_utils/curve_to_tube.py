# !/usr/bin/python
# coding=utf-8
"""Curve to Tube tool — Blender port of mayatk's ``nurbs_utils.curve_to_tube``.

Sweep a circular profile along curve(s) to build a tube. Maya needs a whole command chain for this
(``extrude`` → ``nurbsToPoly`` → ``curveWarp`` deformer → RDP ring placement → normal-conform);
**Blender ships it natively** as the curve's own **bevel**, so this port is a thin wrapper over
curve properties + the shared :meth:`~blendertk.nurbs_utils._nurbs_utils.NurbsUtils.curve_to_mesh`
bake:

  * **NURBS Tube** → a **beveled curve** (``bevel_mode='ROUND'``) — smooth and inherently *live*
    (edit the curve and the tube updates), Blender's analogue of Maya's NURBS surface.
  * **Polygon Tube** → a **mesh**: a ``bevel_object`` profile **circle of exactly ``sections``
    points** gives exactly ``sections`` sides, ``resolution_u`` the ring density, ``use_fill_caps``
    the caps; baked to a mesh via ``curve_to_mesh``.

The panel is tentacle's ``slots/tools/curve_to_tube.py``: one Slots class for both hosts, over
this class as ``btk.CurveToTube`` and :class:`~blendertk.core_utils.preview.Preview` as
``btk.Preview``. The engine accepts any ``bpy`` ``CURVE`` object, and "Keep History" bevels the
source curve in place rather than keeping Maya-only construction-history nodes.

Divergences (documented for parity — see ``tentacle/docs/PARITY_PORTING_PLAN.md``):
  * **``output_type`` decides the result type** (Blender unifies curve+surface): ``nurbs`` → a live
    beveled curve, ``polygon`` → a baked mesh. The **source curve is always preserved** (Maya does
    too). **``live`` (Keep History)** is honored for ``nurbs`` (in-place on the source = the curve
    *is* the live tube, vs a beveled duplicate); for ``polygon`` Blender has **no live curve→mesh**,
    so the mesh is always baked and ``live`` has no effect there.
  * **No RDP curvature ring placement / curveWarp / normal-conform** — Blender's bevel produces
    uniform rings (``resolution_u`` = Path Res) with consistent outward normals natively. ``Degree``
    maps to the round-bevel resolution (1 ≈ faceted, 3 ≈ smooth); ``Sections`` scales it for NURBS.
  * ``import bpy`` / ``bmesh`` deferred into the call bodies.
"""

import math

import pythontk as ptk

from blendertk.nurbs_utils._nurbs_utils import NurbsUtils


class CurveToTube(ptk.LoggingMixin):
    """Sweep a circular profile along curve(s) to build a tube — Blender mirror of mayatk's
    ``CurveToTube``. Stateless: every entry point is a ``classmethod`` returning plain objects."""

    # (label, value) pairs for the UI combo and for input validation (mirror of mayatk).
    OUTPUT_TYPES = (("NURBS Tube", "nurbs"), ("Polygon Tube", "polygon"))

    @classmethod
    def create(
        cls,
        curves,
        output_type="nurbs",
        radius=1.0,
        sections=8,
        path_divisions=1,
        degree=3,
        caps=True,
        quads=True,
        live=False,
        name="tube",
    ):
        """Build a tube along each given curve.

        Parameters:
            curves (list): Curve object(s) to sweep along (non-curve selections are ignored).
            output_type (str): ``"nurbs"`` → a beveled curve (smooth, live), ``"polygon"`` → a mesh.
            radius (float): Radius of the swept profile.
            sections (int): Sides around the profile — exact for polygon, round-bevel smoothness
                for NURBS.
            path_divisions (int): Polygon only — ring density along the path (``resolution_u``).
            degree (int): NURBS only — round-bevel resolution (1 ≈ faceted, 3 ≈ smooth).
            caps (bool): Polygon only — fill the open ends (``use_fill_caps``).
            quads (bool): Polygon only — keep quads (True) or triangulate (False).
            live (bool): NURBS — bevel the source curve in place (it *is* the live tube) vs a
                beveled duplicate. Polygon — no effect: Blender has no live curve→mesh, so the mesh
                is baked and the source curve kept either way (as Maya's baked tube keeps it).
            name (str): Base name for the created tube.

        Returns:
            list: The created tube object(s) (curves for ``nurbs``, meshes for ``polygon``).
        """
        if output_type not in ("nurbs", "polygon"):
            raise ValueError(f"Unknown output_type: {output_type!r}")
        sources = cls._curve_objects(curves)
        if not sources:
            raise RuntimeError(
                "No curve selected — select one or more curve objects to sweep along."
            )
        return [
            cls._build_one(
                src,
                output_type,
                radius,
                max(3, int(sections)),
                max(1, int(path_divisions)),
                int(degree),
                bool(caps),
                bool(quads),
                bool(live),
                name,
            )
            for src in sources
        ]

    @staticmethod
    def describe(tubes):
        """One line of stats for built tubes — a polygon tube's triangle count, a beveled curve's
        point count and bevel resolution — or ``""`` when none of them exists any more (a preview
        rollback removes them). Mirror of mayatk's ``CurveToTube.describe``; the panel's footer.

        Parameters:
            tubes (list): Tube objects, as :meth:`create` returns them.

        Returns:
            str: e.g. ``"1,024 tris"``, ``"beveled curve · 4 pts, bevel res 2"``.
        """
        import bpy

        alive = []
        for t in tubes or []:
            try:
                if t and t.name in bpy.data.objects:
                    alive.append(t)
            except ReferenceError:  # a removed (rolled-back) object
                continue
        if not alive:
            return ""
        prefix = f"{len(alive)} tubes — " if len(alive) > 1 else ""
        if alive[0].type == "MESH":
            tris = 0
            for t in alive:
                t.data.calc_loop_triangles()
                tris += len(t.data.loop_triangles)
            return f"{prefix}{tris:,} tris"
        info = []
        for t in alive:
            n_pts = sum((len(s.points) or len(s.bezier_points)) for s in t.data.splines)
            info.append(f"{n_pts} pts, bevel res {t.data.bevel_resolution}")
        return f"{prefix}beveled curve · {', '.join(info)}"

    # ----------------------------------------------------------- internals
    @staticmethod
    def _curve_objects(curves):
        """Resolve the input to a de-duplicated list of curve objects."""
        seen, result = set(), []
        for o in ptk.make_iterable(curves):
            if o and getattr(o, "type", None) == "CURVE" and o.name not in seen:
                seen.add(o.name)
                result.append(o)
        return result

    @staticmethod
    def _profile_circle(radius, sections, name):
        """A faceted (POLY) circle of exactly ``sections`` points → the polygon tube's exact sides."""
        n = max(3, int(sections))
        r = float(radius)
        pts = [
            (r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), 0.0)
            for i in range(n)
        ]
        return NurbsUtils.create_curve(
            pts, name=f"{name}_profile", cyclic=True, kind="POLY"
        )

    @classmethod
    def _build_one(
        cls,
        source,
        output_type,
        radius,
        sections,
        path_divisions,
        degree,
        caps,
        quads,
        live,
        name,
    ):
        """Build one tube along one curve; return the finished object."""
        import bpy

        if output_type == "nurbs":
            # NURBS → a beveled CURVE (round, smooth, live). live = bevel the source in place (the
            # curve IS the tube); baked = bevel a duplicate (source preserved). Only the duplicate is
            # renamed — renaming the in-place source would make the Preview's name-based rollback
            # treat it as a new object (remove + recreate) instead of cleanly restoring its data.
            if live:
                target = source
            else:
                target = NurbsUtils.duplicate_curve(source, name=name, link=True)
            target.data.bevel_mode = "ROUND"
            target.data.bevel_depth = float(radius)
            # Degree maps to the round-bevel resolution (1 ≈ faceted, 3 ≈ smooth); Sections scales it.
            target.data.bevel_resolution = (
                0 if degree <= 1 else max(1, round(sections / 4))
            )
            return target

        # Polygon → a MESH. A bevel_object circle of `sections` points gives exactly `sections`
        # sides; resolution_u the rings; use_fill_caps the caps. The source curve is always
        # preserved (work on a copy), as Maya's is; `live` has no effect (no live curve→mesh).
        work = NurbsUtils.duplicate_curve(source, link=True)
        profile = cls._profile_circle(radius, sections, name)
        work.data.bevel_mode = "OBJECT"
        work.data.bevel_object = profile
        work.data.resolution_u = path_divisions
        work.data.use_fill_caps = bool(caps)
        mesh = NurbsUtils.curve_to_mesh(
            work, name=name, keep_curve=False
        )  # consumes the copy
        if profile.users_collection:
            bpy.data.objects.remove(profile, do_unlink=True)
        if not quads:
            cls._triangulate(mesh)
        return mesh

    @staticmethod
    def _triangulate(mesh_obj):
        """Triangulate a mesh in place (the Quads-off path) — bmesh, no view context needed."""
        import bmesh

        bm = bmesh.new()
        bm.from_mesh(mesh_obj.data)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.to_mesh(mesh_obj.data)
        bm.free()
