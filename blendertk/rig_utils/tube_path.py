# !/usr/bin/python
# coding=utf-8
"""Tube-mesh centerline extraction — Blender port of mayatk's ``rig_utils.tube_rig.TubePath``.

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
    def _world_vertices(mesh):
        """World-space vertex positions of *mesh* (read from the evaluated mesh so modifiers /
        shape keys are included — the deformed tube, like Maya reads the live shape)."""
        import bpy

        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = mesh.evaluated_get(depsgraph)
        me = evaluated.to_mesh()
        try:
            mw = mesh.matrix_world
            return [tuple(mw @ v.co) for v in me.vertices]
        finally:
            evaluated.to_mesh_clear()

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
            elif hasattr(e, "vertices"):  # mesh MeshEdge — indices into mesh.data.vertices
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
