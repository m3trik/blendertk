# !/usr/bin/python
# coding=utf-8
"""Tube-mesh centerline extraction — Blender port of mayatk's ``rig_utils.tube_rig.tube_path.TubePath``.

Pure geometry analysis: given a tube-shaped mesh, produce an ordered list of centerline points
(ring centers) the rig's bone chain / driver curve follow. Creates **no** scene objects.

The robust core — slicing the tube's world-space vertices along their dominant axis into ring
centers — is the DCC-neutral ``ptk.Polyline.from_point_cloud`` (shared SSoT, the analogue of
mayatk's bounding-box slicing). This module only gathers the world vertices (via the evaluated
mesh) and offers the explicit **edge-selection** override (mayatk's ``get_centerline_using_edges``)
for tubes the auto slice can't resolve (sharp curves that double back along the dominant axis).

Maya's surface-normal / edge-loop-topology methods are **not** mirrored 1:1 — they exist in Maya to
work around its low-level API; the shared slice + the explicit-edge override cover the same need
(see ``tentacle/docs/PARITY_PORTING_PLAN.md``). ``import bpy`` / ``bmesh`` are deferred into the call
bodies so importing this module never needs a running Blender.
"""

import pythontk as ptk


class TubePath:
    """Extract centerline paths from tube meshes (static helpers; no scene objects created)."""

    @staticmethod
    def get_centerline(mesh, num_joints=10, precision=None, edges=None):
        """Unified centerline dispatcher — mirror of mayatk's ``TubePath.get_centerline``.

        Args:
            mesh (bpy.types.Object): The tube mesh.
            num_joints (int): Requested centerline points. ``-1`` = auto (a sensible default count,
                since Blender has no cheap edge-loop count like Maya's).
            precision (int): Slab count for the slice (higher resolves curves better); ``None`` =
                auto (see :func:`ptk.Polyline.from_point_cloud`).
            edges: Optional explicit edge selection (a list of ``bmesh``/mesh edges or edge indices)
                whose vertices define the centerline directly (the manual override).

        Returns:
            tuple[list[list[float]], int]: ``(centerline_points, resolved_num_joints)``.
        """
        if edges:
            pts = TubePath.get_centerline_using_edges(mesh, edges)
            return pts, (len(pts) if num_joints == -1 else num_joints)

        count = num_joints if (num_joints and num_joints > 0) else 10
        pts = ptk.Polyline.from_point_cloud(
            TubePath._world_vertices(mesh), count, precision=precision
        )
        return pts, len(pts)

    @staticmethod
    def estimate_radius(mesh, centerline):
        """Estimate the tube's radius -- mirror of mayatk's ``TubePath.estimate_radius``: the
        median distance from the interior centerline points to the mesh surface.

        Points within one probe length (half the mesh's smallest dimension, about the radius
        upper bound) of either end are skipped: their nearest surface is the end cap, which
        reads near zero and poisons the estimate. A tube too short for interior points samples
        its arc midpoint.

        Args:
            mesh (bpy.types.Object): The tube mesh.
            centerline (list): World-space points along the tube.

        Returns:
            float: The radius, or ``None`` when no mesh / centerline is usable.
        """
        from mathutils import Vector

        if getattr(mesh, "type", None) != "MESH" or not centerline or len(centerline) < 2:
            return None
        pts = [Vector((float(p[0]), float(p[1]), float(p[2]))) for p in centerline]
        arc = [0.0]
        for a, b in zip(pts, pts[1:]):
            arc.append(arc[-1] + (b - a).length)
        total = arc[-1]
        if total < 1e-6:
            return None
        dims = [d for d in mesh.dimensions if d > 1e-6]
        probe = 0.5 * min(dims) if dims else 1.0
        interior = [p for p, d in zip(pts, arc) if d >= probe and total - d >= probe]
        if not interior:  # shorter than ~2 radii: the arc midpoint
            half = total / 2.0
            i = next(i for i in range(1, len(arc)) if arc[i] >= half)
            t = (half - arc[i - 1]) / max(arc[i] - arc[i - 1], 1e-9)
            interior = [pts[i - 1].lerp(pts[i], t)]

        mw = mesh.matrix_world
        inv = mw.inverted_safe()
        dists = []
        for p in interior[:: max(1, len(interior) // 15)]:
            try:
                hit, location, _normal, _index = mesh.closest_point_on_mesh(inv @ p)
            except RuntimeError:  # no mesh data to query (e.g. the mesh is in Edit Mode)
                return None
            if hit:
                d = (mw @ location - p).length
                if d > 1e-6:
                    dists.append(d)
        if not dists:
            return None
        dists.sort()
        return dists[len(dists) // 2]

    @staticmethod
    def _world_vertices(mesh):
        """World-space vertex positions of *mesh* (read from the evaluated mesh so modifiers /
        shape keys are included — the deformed tube, like Maya reads the live shape)."""

        from blendertk.core_utils._core_utils import CoreUtils

        depsgraph = CoreUtils._evaluated_depsgraph()  # the window layer's
        evaluated = mesh.evaluated_get(depsgraph)
        me = evaluated.to_mesh()
        try:
            mw = mesh.matrix_world
            return [tuple(mw @ v.co) for v in me.vertices]
        finally:
            evaluated.to_mesh_clear()

    @staticmethod
    def get_selected_edges(mesh):
        """The mesh's selected EDIT-mode edges — mirror of mayatk's optional
        ``cmds.filterExpand(selectionMask=32)`` edge override for ``get_centerline``. Returns
        ``None`` when *mesh* isn't in Edit Mode or has no edge selection, so callers can pass the
        result straight through to :meth:`get_centerline`'s ``edges=`` (falsy → ignored)."""
        if getattr(mesh, "mode", None) != "EDIT":
            return None
        import bmesh

        bm = bmesh.from_edit_mesh(mesh.data)
        sel = [e for e in bm.edges if e.select]
        return sel or None

    @staticmethod
    def get_centerline_using_edges(mesh, edges):
        """Centerline from an explicit edge selection — mirror of mayatk's
        ``get_centerline_using_edges``. Each edge contributes its two endpoints (world space); the
        union (deduped by position) is ordered into a continuous path via the shared
        ``Polyline.order_points``. Accepts ``bmesh`` edges (``.verts``), mesh edges (``.vertices``),
        or integer edge indices (resolved against ``mesh.data.edges``) — an edit-mode selection
        hands over ``bmesh`` edges, an object-mode one hands over mesh edges."""
        mw = mesh.matrix_world
        data_verts = mesh.data.vertices
        seen = {}
        for e in edges:
            if hasattr(e, "verts"):  # bmesh BMEdge — verts carry .co directly
                cos = [v.co for v in e.verts]
            elif hasattr(
                e, "vertices"
            ):  # mesh MeshEdge — indices into mesh.data.vertices
                cos = [data_verts[i].co for i in e.vertices]
            else:  # integer edge index
                cos = [data_verts[i].co for i in mesh.data.edges[int(e)].vertices]
            for co in cos:
                key = (round(co[0], 6), round(co[1], 6), round(co[2], 6))
                if key not in seen:
                    seen[key] = tuple(mw @ co)
        if len(seen) < 2:
            return []
        return [
            [float(c) for c in p]
            for p in ptk.Polyline.order_points(list(seen.values()))
        ]


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
